#!/usr/bin/env python3
"""Generate a one-shot follow-up taxonomy prompt and structured candidate bundles."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from common import (
    CLASSIFIED_OUTPUT_SCHEMA_VERSION,
    DEFAULT_GENERIC_PLATFORM_DOMAINS,
    TAXONOMY_FOLLOWUP_CANDIDATES_SCHEMA_VERSION,
    USER_TAXONOMY_SCHEMA_VERSION,
    build_parser,
    build_signal_pack,
    ensure_parent,
    is_generic_platform_domain,
    load_config_from_args,
    normalize_topic_token,
    require_payload_schema,
    signal_pack_sections,
)


def _load_cluster_module():
    path = Path(__file__).resolve().parent / "5_cluster_bookmarks.py"
    spec = importlib.util.spec_from_file_location("cluster_bookmarks_for_followup", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["cluster_bookmarks_for_followup"] = module
    spec.loader.exec_module(module)
    return module


cluster_module = _load_cluster_module()


def bookmark_identity(bookmark: dict[str, Any]) -> str:
    signal_pack = bookmark.get("signal_pack") or build_signal_pack(bookmark)
    sections = signal_pack_sections(signal_pack)
    return str(sections["identity"].get("canonical_identity") or bookmark.get("url") or bookmark.get("id") or "")


def bookmark_registered_domain(bookmark: dict[str, Any]) -> str:
    signal_pack = bookmark.get("signal_pack") or build_signal_pack(bookmark)
    identity = signal_pack_sections(signal_pack)["identity"]
    registrable = str(identity.get("registrable_domain") or "").lower()
    if registrable:
        return registrable
    return str(bookmark.get("domain") or "").lower()


def top_open_topic(bookmark: dict[str, Any]) -> str:
    candidates = bookmark.get("classification", {}).get("open_topic_candidates", []) or []
    if not candidates:
        return ""
    return str(candidates[0].get("topic") or "").strip()


def confirmation_bucket(bookmark: dict[str, Any]) -> str:
    classification = bookmark.get("classification", {}) if isinstance(bookmark.get("classification"), dict) else {}
    return str(classification.get("confirmation_bucket") or "low_confidence").strip().lower()


def stable_bundle_id(identities: list[str]) -> str:
    digest = hashlib.sha1("\n".join(sorted(identities)).encode("utf-8")).hexdigest()[:12]
    return f"tf_{digest}"


def _counter_rows(counter: Counter[str], *, key_name: str, limit: int = 8) -> list[dict[str, Any]]:
    return [
        {key_name: key, "count": count}
        for key, count in counter.most_common(limit)
        if key
    ]


def summarize_bookmarks(bookmarks: list[dict[str, Any]], *, generic_platform_domains: set[str]) -> dict[str, Any]:
    open_topics = Counter()
    cluster_hints = Counter()
    resource_types = Counter()
    domains = Counter()
    rule_candidates = Counter()
    confirmation_buckets = Counter()
    representative_bookmarks = []
    identities = []

    for bookmark in bookmarks:
        identity = bookmark_identity(bookmark)
        if identity:
            identities.append(identity)
        topic = top_open_topic(bookmark)
        if topic:
            open_topics[topic] += 1
        for hint in (bookmark.get("classification", {}).get("cluster_hints", []) or [])[:4]:
            if hint:
                cluster_hints[str(hint)] += 1
        resource_type = str(bookmark.get("classification", {}).get("resource_type") or "未知")
        resource_types[resource_type] += 1
        confirmation_buckets[confirmation_bucket(bookmark)] += 1
        domain = bookmark_registered_domain(bookmark)
        if domain:
            domains[domain] += 1
        for item in (bookmark.get("classification", {}).get("rule_candidates", []) or [])[:3]:
            category = str(item.get("category") or "").strip()
            if category:
                rule_candidates[category] += 1
        if len(representative_bookmarks) < 5:
            representative_bookmarks.append(
                {
                    "id": bookmark.get("id"),
                    "title": bookmark.get("name"),
                    "url": bookmark.get("url"),
                    "domain": bookmark.get("domain"),
                    "resource_type": resource_type,
                }
            )

    generic_share = 0.0
    if domains:
        generic_count = sum(
            count for domain, count in domains.items()
            if is_generic_platform_domain(domain, generic_platform_domains)
        )
        generic_share = round(generic_count / max(sum(domains.values()), 1), 4)

    return {
        "bookmark_identities": sorted(set(identities)),
        "support_count": len(bookmarks),
        "top_open_topics": _counter_rows(open_topics, key_name="topic"),
        "top_cluster_hints": _counter_rows(cluster_hints, key_name="hint"),
        "resource_type_distribution": _counter_rows(resource_types, key_name="resource_type"),
        "confirmation_bucket_distribution": _counter_rows(confirmation_buckets, key_name="confirmation_bucket"),
        "top_domains": [
            {
                "domain": domain,
                "count": count,
                "generic_platform": is_generic_platform_domain(domain, generic_platform_domains),
            }
            for domain, count in domains.most_common(8)
        ],
        "closest_existing_rule_candidates": _counter_rows(rule_candidates, key_name="category", limit=5),
        "representative_bookmarks": representative_bookmarks,
        "generic_platform_share": generic_share,
    }


def bundle_from_cluster(
    profile: dict[str, Any],
    bookmarks: list[dict[str, Any]],
    *,
    generic_platform_domains: set[str],
) -> dict[str, Any]:
    summary = summarize_bookmarks(bookmarks, generic_platform_domains=generic_platform_domains)
    bundle_id = stable_bundle_id(summary["bookmark_identities"])
    return {
        "bundle_id": bundle_id,
        "cluster_id": bundle_id,
        "bundle_type": "cluster",
        "source_cluster_id": profile.get("cluster_id"),
        "cluster_label": profile.get("cluster_label"),
        "support_count": summary["support_count"],
        **summary,
    }


def bundle_from_tidy_semantic(
    bundle: dict[str, Any],
    *,
    generic_platform_domains: set[str],
) -> dict[str, Any]:
    bookmarks = list(bundle.get("bookmarks") or [])
    summary = summarize_bookmarks(bookmarks, generic_platform_domains=generic_platform_domains)
    bundle_id = str(bundle.get("bundle_id") or stable_bundle_id(summary["bookmark_identities"]))
    return {
        "bundle_id": bundle_id,
        "cluster_id": bundle_id,
        "bundle_type": "tidy_semantic",
        "source_cluster_id": "",
        "cluster_label": str(bundle.get("bundle_label") or ""),
        "bucket_name": str(bundle.get("bucket_name") or ""),
        "root_hint": str(bundle.get("root_hint") or ""),
        "source_types": list(bundle.get("source_types") or []),
        "support_count": summary["support_count"],
        **summary,
    }


def bundle_from_aggregate(
    key: tuple[str, str, str],
    bookmarks: list[dict[str, Any]],
    *,
    generic_platform_domains: set[str],
) -> dict[str, Any]:
    summary = summarize_bookmarks(bookmarks, generic_platform_domains=generic_platform_domains)
    topic_key, resource_type, domain = key
    bundle_id = stable_bundle_id(summary["bookmark_identities"])
    return {
        "bundle_id": bundle_id,
        "cluster_id": bundle_id,
        "bundle_type": "aggregate",
        "source_cluster_id": "",
        "cluster_label": summary["top_open_topics"][0]["topic"] if summary["top_open_topics"] else topic_key or resource_type,
        "aggregate_key": {
            "normalized_top_open_topic": topic_key,
            "resource_type": resource_type,
            "registered_domain": domain,
        },
        "support_count": summary["support_count"],
        **summary,
    }


def build_followup_bundles(
    bookmarks: list[dict[str, Any]],
    cluster_profiles: list[dict[str, Any]],
    *,
    generic_platform_domains: set[str],
    tidy_root_name: str,
) -> list[dict[str, Any]]:
    eligible = [
        bookmark
        for bookmark in bookmarks
        if confirmation_bucket(bookmark) in {"rule_gap", "low_confidence"}
        and not bookmark.get("classification", {}).get("review_required")
    ]
    eligible_identities = {
        bookmark_identity(bookmark)
        for bookmark in eligible
        if bookmark_identity(bookmark)
    }

    selected_identities: set[str] = set()
    bundles: list[dict[str, Any]] = []
    tidy_clusterer = cluster_module.BookmarkClusterer(generic_platform_domains=generic_platform_domains)
    for profile in cluster_profiles:
        if profile.get("destination_root") != tidy_root_name:
            continue
        profile_bookmarks = [
            bookmark
            for bookmark in profile.get("bookmarks", [])
            if bookmark_identity(bookmark) in eligible_identities
        ]
        if len(profile_bookmarks) < 2:
            continue
        bundles.append(
            bundle_from_cluster(
                profile,
                profile_bookmarks,
                generic_platform_domains=generic_platform_domains,
            )
        )
        selected_identities.update(bookmark_identity(bookmark) for bookmark in profile_bookmarks)

    remaining_by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for bookmark in eligible:
        identity = bookmark_identity(bookmark)
        if not identity or identity in selected_identities:
            continue
        remaining_by_bucket[confirmation_bucket(bookmark)].append(bookmark)

    for bucket, bucket_bookmarks in sorted(remaining_by_bucket.items()):
        bundles_from_bucket, leftovers = cluster_module.build_tidy_semantic_bundles(
            tidy_clusterer,
            bucket_bookmarks,
            bucket_name=cluster_module.TIDY_BUCKET_DISPLAY_NAMES.get(bucket, cluster_module.TIDY_BUCKET_DISPLAY_NAMES["low_confidence"]),
            tidy_root_name=tidy_root_name,
        )
        for bundle in bundles_from_bucket:
            bundles.append(
                bundle_from_tidy_semantic(
                    bundle,
                    generic_platform_domains=generic_platform_domains,
                )
            )
            for bookmark in bundle.get("bookmarks", []):
                identity = bookmark_identity(bookmark)
                if identity:
                    selected_identities.add(identity)
        remaining_by_bucket[bucket] = leftovers

    aggregated: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for leftovers in remaining_by_bucket.values():
        for bookmark in leftovers:
            identity = bookmark_identity(bookmark)
            if not identity or identity in selected_identities:
                continue
            topic = normalize_topic_token(top_open_topic(bookmark))
            if not topic:
                continue
            resource_type = str(bookmark.get("classification", {}).get("resource_type") or "未知")
            domain = bookmark_registered_domain(bookmark)
            if is_generic_platform_domain(domain, generic_platform_domains):
                domain = ""
            aggregated[(topic, resource_type, domain)].append(bookmark)

    for key, group in sorted(
        aggregated.items(),
        key=lambda item: (-len(item[1]), item[0][0], item[0][1], item[0][2]),
    ):
        if len(group) < 2:
            continue
        bundles.append(
            bundle_from_aggregate(
                key,
                group,
                generic_platform_domains=generic_platform_domains,
            )
        )

    bundles.sort(key=lambda item: (-item["support_count"], item["bundle_type"], item["cluster_id"]))
    return bundles


def _load_json(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def render_prompt(
    candidates_payload: dict[str, Any],
    *,
    existing_root_groups: list[dict[str, Any]],
    existing_category_samples: list[str],
    rule_gap_domain_hotspots: list[dict[str, Any]],
    max_summary_bundles: int,
) -> str:
    bundles = candidates_payload.get("bundles", [])[:max_summary_bundles]
    lines = [
        "# Bookmark Taxonomy Follow-up",
        "",
        "下面是一次针对 `待整理` 长尾书签的增量 taxonomy 补全任务。",
        "本项目不会直接调用任何 LLM API；请使用你自己的外部 LLM 或 code agent 读取 `taxonomy_followup_candidates.json` 后返回严格 JSON。",
        "",
        "要求：",
        "- 只补充新增或需要修正的 taxonomy / assignment，不要重写整套 taxonomy。",
        "- `root_groups` 可省略；省略时表示保留现有 generated taxonomy 里的 root_groups。",
        "- `cluster_assignments[].cluster_id` 必须引用 `taxonomy_followup_candidates.json` 中的 `cluster_id`。",
        "- 不要把 GitHub、知乎、CSDN、YouTube、Stack Overflow 等通用平台当作 topic domain。",
        "- `bundle_type=cluster` 表示现有 tidy cluster；`bundle_type=tidy_semantic` 表示确定性二次聚合后的 tidy 语义包；`bundle_type=aggregate` 表示剩余长尾的保底聚合。",
        "- `title_patterns` 普通字符串按字面量短语处理；确实需要正则时用 `{ \"regex\": \"...\" }`。",
        "- 只返回一个 fenced `json` 代码块，不要输出解释文字。",
        "",
        "返回 JSON schema：",
        "```json",
        json.dumps(
            {
                "schema_version": "user_taxonomy_response/v1",
                "categories": [
                    {
                        "path": "示例根主题/示例子主题",
                        "description": "这个分类收纳什么",
                        "aliases": ["stable token"],
                        "title_patterns": ["Example Literal", {"regex": "\\\\bExample\\\\b"}],
                        "topic_domains": ["example.org"],
                    }
                ],
                "cluster_assignments": [
                    {"cluster_id": "tf_xxxxxxxx", "category": "示例根主题/示例子主题", "confidence": "high"}
                ],
                "uncategorized_cluster_ids": [],
                "notes": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        f"现有 root_groups: {json.dumps(existing_root_groups, ensure_ascii=False)}",
        f"现有 category 样例: {json.dumps(existing_category_samples, ensure_ascii=False)}",
        f"rule_gap domain hotspots: {json.dumps(rule_gap_domain_hotspots, ensure_ascii=False)}",
        "",
        f"候选 bundle 总数: {candidates_payload.get('bundle_count', 0)}",
        f"下面列出前 {len(bundles)} 个 bundle 摘要；完整上下文见 `taxonomy_followup_candidates.json`：",
        "",
    ]
    for item in bundles:
        lines.extend(
            [
                f"## {item['cluster_id']} · {item.get('cluster_label') or '未命名'} · support={item['support_count']} · type={item['bundle_type']}",
                f"- confirmation_bucket_distribution: {json.dumps(item.get('confirmation_bucket_distribution', []), ensure_ascii=False)}",
                f"- bucket_name/root_hint/source_types: {json.dumps({'bucket_name': item.get('bucket_name'), 'root_hint': item.get('root_hint'), 'source_types': item.get('source_types', [])}, ensure_ascii=False)}",
                f"- top_open_topics: {json.dumps(item.get('top_open_topics', []), ensure_ascii=False)}",
                f"- top_cluster_hints: {json.dumps(item.get('top_cluster_hints', []), ensure_ascii=False)}",
                f"- top_domains: {json.dumps(item.get('top_domains', []), ensure_ascii=False)}",
                f"- closest_existing_rule_candidates: {json.dumps(item.get('closest_existing_rule_candidates', []), ensure_ascii=False)}",
                f"- representative_bookmarks: {json.dumps(item.get('representative_bookmarks', []), ensure_ascii=False)}",
                "",
            ]
        )
    return "\n".join(lines)


def main() -> int:
    parser = build_parser("生成 rule_gap taxonomy follow-up prompt 和候选包")
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--signal-audit", type=Path, default=None)
    parser.add_argument("--prompt-output", type=Path, default=None)
    parser.add_argument("--candidates-output", type=Path, default=None)
    parser.add_argument("--max-summary-bundles", type=int, default=40)
    args = parser.parse_args()

    config = load_config_from_args(args)
    input_file = args.input or config.paths.classified_file
    signal_audit_file = args.signal_audit or config.paths.signal_audit_report_file
    prompt_output = args.prompt_output or config.paths.taxonomy_followup_prompt_file
    candidates_output = args.candidates_output or config.paths.taxonomy_followup_candidates_file

    data = require_payload_schema(
        json.loads(input_file.read_text(encoding="utf-8")),
        CLASSIFIED_OUTPUT_SCHEMA_VERSION,
        "taxonomy follow-up 输入",
        input_file,
    )
    options = config.clustering_options
    generic_platform_domains = {
        item.lower()
        for item in options.get("generic_platform_domains", DEFAULT_GENERIC_PLATFORM_DOMAINS)
    }
    clusterer = cluster_module.BookmarkClusterer(
        min_cluster_size=options.get("min_cluster_size", 10),
        max_keywords=options.get("max_keywords", 3),
        max_depth=options.get("max_depth", 3),
        merge_small_nodes_threshold=options.get("merge_small_nodes_threshold"),
        domain_split_min_size=options.get("domain_split_min_size", 5),
        generic_platform_domains=generic_platform_domains,
    )
    tidy_root_name = options.get("tidy_root_name", options.get("display", {}).get("tidy_root_name", "待整理"))
    cluster_profiles = cluster_module.build_cluster_payloads(
        clusterer,
        data["bookmarks"],
        threshold=options.get("max_bookmarks_without_clustering", 20),
        discovery_root_name=options.get("discovery_root_name", "发现主题"),
        tidy_root_name=tidy_root_name,
    )
    bundles = build_followup_bundles(
        data["bookmarks"],
        cluster_profiles,
        generic_platform_domains=generic_platform_domains,
        tidy_root_name=tidy_root_name,
    )

    signal_audit = _load_json(signal_audit_file)
    existing_taxonomy = _load_json(config.paths.user_taxonomy_file)
    if existing_taxonomy and existing_taxonomy.get("schema_version") != USER_TAXONOMY_SCHEMA_VERSION:
        raise ValueError(f"现有 user taxonomy schema_version 必须是 {USER_TAXONOMY_SCHEMA_VERSION}: {config.paths.user_taxonomy_file}")
    existing_root_groups = existing_taxonomy.get("root_groups", []) if isinstance(existing_taxonomy.get("root_groups"), list) else []
    existing_category_samples = sorted((existing_taxonomy.get("categories", {}) or {}).keys())[:20]
    rule_gap_domain_hotspots = (
        signal_audit.get("hotspots", {}).get("rule_gap_domains", [])[:15]
        if isinstance(signal_audit.get("hotspots"), dict)
        else []
    )

    payload = {
        "schema_version": TAXONOMY_FOLLOWUP_CANDIDATES_SCHEMA_VERSION,
        "task_type": "taxonomy_followup",
        "bundle_count": len(bundles),
        "bundle_type_counts": dict(Counter(item.get("bundle_type", "unknown") for item in bundles)),
        "bundles": bundles,
        "existing_root_groups": existing_root_groups,
        "existing_category_samples": existing_category_samples,
        "rule_gap_domain_hotspots": rule_gap_domain_hotspots,
    }

    ensure_parent(candidates_output)
    candidates_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    ensure_parent(prompt_output)
    prompt_output.write_text(
        render_prompt(
            payload,
            existing_root_groups=existing_root_groups,
            existing_category_samples=existing_category_samples,
            rule_gap_domain_hotspots=rule_gap_domain_hotspots,
            max_summary_bundles=args.max_summary_bundles,
        ),
        encoding="utf-8",
    )

    print(f"✓ taxonomy follow-up candidates: {candidates_output}")
    print(f"✓ taxonomy follow-up prompt: {prompt_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

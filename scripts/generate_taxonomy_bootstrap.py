#!/usr/bin/env python3
"""Generate a copy-paste prompt for external LLM taxonomy bootstrapping."""
from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from common import (
    CLASSIFIED_OUTPUT_SCHEMA_VERSION,
    DEFAULT_GENERIC_PLATFORM_DOMAINS,
    TAXONOMY_BOOTSTRAP_CLUSTERS_SCHEMA_VERSION,
    build_parser,
    build_signal_pack,
    ensure_parent,
    is_generic_platform_domain,
    load_config_from_args,
    require_payload_schema,
    signal_pack_sections,
)


def _load_cluster_module():
    path = Path(__file__).resolve().parent / "5_cluster_bookmarks.py"
    spec = importlib.util.spec_from_file_location("cluster_bookmarks_for_bootstrap", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["cluster_bookmarks_for_bootstrap"] = module
    spec.loader.exec_module(module)
    return module


cluster_module = _load_cluster_module()


def bookmark_identity(bookmark: dict[str, Any]) -> str:
    signal_pack = bookmark.get("signal_pack") or build_signal_pack(bookmark)
    sections = signal_pack_sections(signal_pack)
    return str(sections["identity"].get("canonical_identity") or bookmark.get("url") or bookmark.get("id") or "")


def summarize_cluster(profile: dict[str, Any], generic_platform_domains: set[str]) -> dict[str, Any]:
    bookmarks = profile.get("bookmarks", [])
    resource_counter = Counter(
        bookmark.get("classification", {}).get("resource_type") or "未知"
        for bookmark in bookmarks
    )
    generic_share = max(
        (
            float(item.get("share", 0.0) or 0.0)
            for item in profile.get("top_domains", [])
            if item.get("domain") and is_generic_platform_domain(item["domain"], generic_platform_domains)
        ),
        default=0.0,
    )
    return {
        "cluster_id": profile.get("cluster_id"),
        "label": profile.get("cluster_label"),
        "size": len(bookmarks),
        "representative_tokens": profile.get("representative_tokens", [])[:8],
        "discovered_topics": profile.get("discovered_topics", [])[:8],
        "top_domains": profile.get("top_domains", [])[:6],
        "resource_types": [
            {"type": resource_type, "count": count}
            for resource_type, count in resource_counter.most_common(6)
        ],
        "generic_platform_share": round(generic_share, 4),
        "fetch_blocked_share": profile.get("fetch_blocked_share", 0.0),
        "review_required_share": profile.get("review_required_share", 0.0),
        "bookmark_identities": [bookmark_identity(bookmark) for bookmark in bookmarks],
        "representative_bookmarks": [
            {
                "id": bookmark.get("id"),
                "title": bookmark.get("name"),
                "url": bookmark.get("url"),
                "domain": bookmark.get("domain"),
                "resource_type": bookmark.get("classification", {}).get("resource_type"),
            }
            for bookmark in bookmarks[:5]
        ],
    }


def render_prompt(clusters: list[dict[str, Any]], max_clusters: int) -> str:
    selected = clusters[:max_clusters]
    lines = [
        "# Bookmark Taxonomy Bootstrap",
        "",
        "下面是一次无预设主题规则的书签聚类摘要。请根据这些簇设计适合该用户的分类体系，并给出簇级分类建议。",
        "本项目不会直接调用任何 LLM API；请使用你自己的外部 LLM 或 code agent 读取 `taxonomy_bootstrap_clusters.json` 后返回严格 JSON。",
        "",
        "要求：",
        "- 不要把 GitHub、知乎、CSDN、Medium、YouTube、Stack Overflow 等通用平台当作主题域名。",
        "- 分类路径用 1-3 层中文或用户常用语言，使用 `/` 分隔。",
        "- `title_patterns` 里的字符串会按字面量短语匹配；确实需要正则时用对象形式 `{ \"regex\": \"...\" }`。",
        "- 只返回一个 fenced `json` 代码块，不要输出解释文字。",
        "- `confidence` 只能是 `high`、`medium`、`low`。",
        "",
        "返回 JSON schema：",
        "```json",
        json.dumps(
            {
                "schema_version": "user_taxonomy_response/v1",
                "root_groups": [{"name": "示例分组", "roots": ["示例根主题"]}],
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
                    {"cluster_id": "bc_xxxxxxxx", "category": "示例根主题/示例子主题", "confidence": "high"}
                ],
                "uncategorized_cluster_ids": [],
                "notes": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        f"下面列出按簇大小排序的前 {len(selected)} 个簇：",
        "",
    ]
    for cluster in selected:
        lines.extend(
            [
                f"## {cluster['cluster_id']} · {cluster.get('label') or '未命名'} · size={cluster['size']}",
                f"- representative_tokens: {', '.join(cluster.get('representative_tokens') or [])}",
                f"- discovered_topics: {', '.join(cluster.get('discovered_topics') or [])}",
                f"- generic_platform_share: {cluster.get('generic_platform_share')}",
                f"- fetch_blocked_share: {cluster.get('fetch_blocked_share')}",
                f"- review_required_share: {cluster.get('review_required_share')}",
                f"- top_domains: {json.dumps(cluster.get('top_domains', []), ensure_ascii=False)}",
                f"- resource_types: {json.dumps(cluster.get('resource_types', []), ensure_ascii=False)}",
                "- representative_bookmarks:",
            ]
        )
        for bookmark in cluster.get("representative_bookmarks", []):
            lines.append(f"  - {bookmark.get('title')} | {bookmark.get('domain')} | {bookmark.get('url')}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = build_parser("生成外部 LLM taxonomy bootstrap prompt")
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--prompt-output", type=Path, default=None)
    parser.add_argument("--clusters-output", type=Path, default=None)
    parser.add_argument("--max-clusters", type=int, default=120)
    args = parser.parse_args()

    config = load_config_from_args(args)
    input_file = args.input or config.paths.classified_file
    prompt_output = args.prompt_output or config.paths.taxonomy_bootstrap_prompt_file
    clusters_output = args.clusters_output or config.paths.taxonomy_bootstrap_clusters_file

    data = require_payload_schema(
        json.loads(input_file.read_text(encoding="utf-8")),
        CLASSIFIED_OUTPUT_SCHEMA_VERSION,
        "taxonomy bootstrap 输入",
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
    profiles = cluster_module.build_cluster_payloads(
        clusterer,
        data["bookmarks"],
        threshold=options.get("max_bookmarks_without_clustering", 20),
        discovery_root_name=options.get("discovery_root_name", "发现主题"),
        tidy_root_name=options.get("tidy_root_name", options.get("display", {}).get("tidy_root_name", "待整理")),
    )
    summaries = [summarize_cluster(profile, generic_platform_domains) for profile in profiles]
    summaries.sort(key=lambda item: (-item["size"], item["cluster_id"]))
    payload = {
        "schema_version": TAXONOMY_BOOTSTRAP_CLUSTERS_SCHEMA_VERSION,
        "task_type": "taxonomy_bootstrap",
        "cluster_count": len(summaries),
        "clusters": summaries,
    }

    ensure_parent(clusters_output)
    clusters_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    ensure_parent(prompt_output)
    prompt_output.write_text(render_prompt(summaries, args.max_clusters), encoding="utf-8")

    print(f"✓ taxonomy bootstrap clusters: {clusters_output}")
    print(f"✓ taxonomy bootstrap prompt: {prompt_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

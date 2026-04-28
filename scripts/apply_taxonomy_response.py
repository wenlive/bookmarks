#!/usr/bin/env python3
"""Apply an external LLM taxonomy response to generated user rules."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from common import (
    BOOKMARK_TAXONOMY_ASSIGNMENTS_SCHEMA_VERSION,
    DEFAULT_GENERIC_PLATFORM_DOMAINS,
    TAXONOMY_BOOTSTRAP_CLUSTERS_SCHEMA_VERSION,
    USER_TAXONOMY_RESPONSE_SCHEMA_VERSION,
    USER_TAXONOMY_SCHEMA_VERSION,
    build_parser,
    ensure_parent,
    is_generic_platform_domain,
    load_config_from_args,
    require_payload_schema,
)


CONFIDENCE_VALUES = {"high": 0.95, "medium": 0.75, "low": 0.4}


def load_response_payload(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    payload = json.loads(text)
    if payload.get("schema_version") != USER_TAXONOMY_RESPONSE_SCHEMA_VERSION:
        raise ValueError(f"schema_version 必须是 {USER_TAXONOMY_RESPONSE_SCHEMA_VERSION}")
    return payload


def validate_category_path(path: str) -> None:
    parts = [part.strip() for part in path.split("/") if part.strip()]
    if not parts or len(parts) > 3 or "/".join(parts) != path:
        raise ValueError(f"非法 category path: {path}")


def _literal_title_pattern(value: str) -> str:
    escaped = re.escape(value)
    normalized = value.lower()
    if normalized.isascii() and re.fullmatch(r"[a-z0-9+.#/-]+", normalized):
        return rf"(?<![a-z0-9]){escaped}(?![a-z0-9])"
    return escaped


def _validate_regex(pattern: str, category_path: str) -> None:
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"{category_path} title_patterns 包含非法正则 {pattern!r}: {exc}") from exc


def normalize_title_patterns(raw_patterns: list[Any], category_path: str) -> list[str]:
    patterns: list[str] = []
    for item in raw_patterns:
        if isinstance(item, dict):
            if item.get("regex"):
                pattern = str(item["regex"]).strip()
            elif item.get("literal"):
                pattern = _literal_title_pattern(str(item["literal"]).strip())
            else:
                raise ValueError(f"{category_path} title_patterns 对象必须包含 regex 或 literal")
        else:
            value = str(item).strip()
            pattern = _literal_title_pattern(value)
        if not pattern:
            continue
        _validate_regex(pattern, category_path)
        patterns.append(pattern)
    return list(dict.fromkeys(patterns))


def validate_patterns(patterns: list[str], category_path: str) -> None:
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"{category_path} title_patterns 包含非法正则 {pattern!r}: {exc}") from exc


def validate_topic_domains(domains: list[str], category_path: str) -> None:
    for domain in domains:
        if is_generic_platform_domain(domain, DEFAULT_GENERIC_PLATFORM_DOMAINS):
            raise ValueError(f"{category_path} 不能把通用平台作为 topic domain: {domain}")


def normalize_categories(categories: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], set[str]]:
    category_rules: dict[str, dict[str, Any]] = {}
    paths: set[str] = set()
    for item in categories:
        path = str(item.get("path") or "").strip()
        validate_category_path(path)
        if path in paths:
            raise ValueError(f"重复 category path: {path}")
        aliases = [str(value).strip() for value in item.get("aliases", []) if str(value).strip()]
        title_patterns = normalize_title_patterns(item.get("title_patterns", []), path)
        topic_domains = [str(value).strip().lower() for value in item.get("topic_domains", []) if str(value).strip()]
        validate_topic_domains(topic_domains, path)
        category_rules[path] = {
            "domains": topic_domains,
            "keywords": aliases,
            "title_patterns": title_patterns,
            "folder_keywords": [],
            "description": str(item.get("description") or ""),
            "source": "llm_taxonomy",
        }
        paths.add(path)
    return category_rules, paths


def normalize_root_groups(root_groups: list[dict[str, Any]], category_paths: set[str]) -> list[dict[str, Any]]:
    if not root_groups:
        roots = list(dict.fromkeys(path.split("/")[0] for path in category_paths))
        return [{"name": "主要主题", "roots": roots}] if roots else []
    normalized = []
    known_roots = {path.split("/")[0] for path in category_paths}
    for item in root_groups:
        name = str(item.get("name") or "").strip()
        roots = [str(value).strip() for value in item.get("roots", []) if str(value).strip()]
        if not name:
            raise ValueError("root_groups.name 不能为空")
        unknown = [root for root in roots if root not in known_roots]
        if unknown:
            raise ValueError(f"root_groups 包含未知 root: {unknown}")
        normalized.append({"name": name, "roots": list(dict.fromkeys(roots))})
    return normalized


def build_assignments(
    response: dict[str, Any],
    clusters_payload: dict[str, Any],
    category_paths: set[str],
) -> dict[str, dict[str, Any]]:
    cluster_map = {
        cluster["cluster_id"]: cluster
        for cluster in clusters_payload.get("clusters", [])
        if cluster.get("cluster_id")
    }
    assignments: dict[str, dict[str, Any]] = {}
    for item in response.get("cluster_assignments", []):
        cluster_id = str(item.get("cluster_id") or "").strip()
        category = str(item.get("category") or "").strip()
        confidence_label = str(item.get("confidence") or "").strip().lower()
        if cluster_id not in cluster_map:
            raise ValueError(f"未知 cluster_id: {cluster_id}")
        if category not in category_paths:
            raise ValueError(f"{cluster_id} 指向未知 category: {category}")
        if confidence_label not in CONFIDENCE_VALUES:
            raise ValueError(f"{cluster_id} confidence 非法: {confidence_label}")
        confidence = CONFIDENCE_VALUES[confidence_label]
        for identity in cluster_map[cluster_id].get("bookmark_identities", []):
            if not identity:
                continue
            existing = assignments.get(identity)
            if existing and float(existing.get("confidence", 0.0)) > confidence:
                continue
            assignments[identity] = {
                "category": category,
                "confidence": confidence,
                "confidence_label": confidence_label,
                "source_cluster_id": cluster_id,
                "source": "llm_cluster_assignment",
            }
    for cluster_id in response.get("uncategorized_cluster_ids", []):
        if cluster_id not in cluster_map:
            raise ValueError(f"uncategorized_cluster_ids 包含未知 cluster_id: {cluster_id}")
    return assignments


def main() -> int:
    parser = build_parser("导入外部 LLM taxonomy JSON")
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--clusters", type=Path, default=None)
    parser.add_argument("--taxonomy-output", type=Path, default=None)
    parser.add_argument("--assignments-output", type=Path, default=None)
    args = parser.parse_args()

    config = load_config_from_args(args)
    clusters_file = args.clusters or config.paths.taxonomy_bootstrap_clusters_file
    taxonomy_output = args.taxonomy_output or config.paths.user_taxonomy_file
    assignments_output = args.assignments_output or config.paths.bookmark_assignment_file

    response = load_response_payload(args.response)
    clusters_payload = require_payload_schema(
        json.loads(clusters_file.read_text(encoding="utf-8")),
        TAXONOMY_BOOTSTRAP_CLUSTERS_SCHEMA_VERSION,
        "taxonomy bootstrap clusters",
        clusters_file,
    )
    category_rules, category_paths = normalize_categories(response.get("categories", []))
    root_groups = normalize_root_groups(response.get("root_groups", []), category_paths)
    assignments = build_assignments(response, clusters_payload, category_paths)

    taxonomy_payload = {
        "schema_version": USER_TAXONOMY_SCHEMA_VERSION,
        "categories": category_rules,
        "root_groups": root_groups,
        "source": "external_llm_taxonomy_response",
    }
    assignments_payload = {
        "schema_version": BOOKMARK_TAXONOMY_ASSIGNMENTS_SCHEMA_VERSION,
        "assignments": assignments,
    }

    ensure_parent(taxonomy_output)
    taxonomy_output.write_text(json.dumps(taxonomy_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    ensure_parent(assignments_output)
    assignments_output.write_text(json.dumps(assignments_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✓ user taxonomy: {taxonomy_output}")
    print(f"✓ bookmark taxonomy assignments: {assignments_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

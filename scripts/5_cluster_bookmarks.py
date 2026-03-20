#!/usr/bin/env python3
"""步骤5: 聚类分析与层级构建。"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List

from common import build_parser, configure_logging, ensure_parent, load_config_from_args


STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "by",
    "from", "as", "is", "was", "are", "were", "been", "this", "that", "how", "what", "why",
    "的", "了", "和", "是", "在", "有", "个", "我", "他", "她", "它", "教程", "指南",
}


class BookmarkClusterer:
    def __init__(
        self,
        min_cluster_size: int = 10,
        max_keywords: int = 3,
        max_depth: int = 3,
        merge_small_nodes_threshold: int | None = None,
        domain_split_min_size: int = 5,
    ):
        self.min_cluster_size = min_cluster_size
        self.max_keywords = max_keywords
        self.max_depth = max_depth
        self.merge_small_nodes_threshold = merge_small_nodes_threshold or max(2, min_cluster_size // 2)
        self.domain_split_min_size = domain_split_min_size

    def extract_keywords(self, text: str) -> List[str]:
        text = re.sub(r"[^\w\s\u4e00-\u9fff-]", " ", text.lower())
        words = [word for word in text.split() if word not in STOPWORDS and len(word) > 1]
        return words

    @staticmethod
    def normalize_name(name: str) -> str:
        cleaned = re.sub(r"[_\-/]+", " ", (name or "").strip().lower())
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned or "其他"

    def clean_topic_token(self, token: str) -> str:
        cleaned = re.sub(r"[_/]+", " ", token or "")
        cleaned = re.sub(r"[^\w\s\u4e00-\u9fff.-]", " ", cleaned).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        parts = []
        seen = set()
        for part in cleaned.split():
            key = part.lower()
            if key in STOPWORDS or key in seen:
                continue
            seen.add(key)
            parts.append(part)
        return " ".join(parts) or "其他"

    def make_node(self, name: str, *, node_type: str = "mixed", children: List[dict] | None = None, bookmarks: List[dict] | None = None) -> dict:
        node = {
            "name": name,
            "children": children or [],
            "bookmarks": bookmarks or [],
            "count": 0,
            "node_type": node_type,
        }
        return self.refresh_count(node)

    def refresh_count(self, node: dict) -> dict:
        node["count"] = len(node.get("bookmarks", [])) + sum(child.get("count", 0) for child in node.get("children", []))
        return node

    def cluster_by_keywords(self, bookmarks: List[dict]) -> Dict[str, List[dict]]:
        keyword_groups = defaultdict(list)
        for bookmark in bookmarks:
            text = " ".join(
                [
                    bookmark.get("name", ""),
                    bookmark.get("metadata", {}).get("title", ""),
                    bookmark.get("metadata", {}).get("description", ""),
                    bookmark.get("metadata", {}).get("keywords", ""),
                ]
            )
            keywords = [self.clean_topic_token(word) for word in self.extract_keywords(text)]
            keyword = Counter(keywords).most_common(self.max_keywords)
            label = keyword[0][0] if keyword else "其他"
            keyword_groups[label].append(bookmark)

        final_groups = {}
        others = []
        for keyword, group in keyword_groups.items():
            if len(group) >= self.min_cluster_size:
                final_groups[keyword] = group
            else:
                others.extend(group)
        if others:
            final_groups["其他"] = others
        return final_groups

    @staticmethod
    def cluster_by_domain(bookmarks: List[dict]) -> Dict[str, List[dict]]:
        groups = defaultdict(list)
        for bookmark in bookmarks:
            domain = bookmark.get("domain") or "其他"
            groups[domain].append(bookmark)
        return dict(groups)

    def infer_node_name(self, parent_name: str, label: str, node_type: str) -> str:
        cleaned = self.clean_topic_token(label)
        if node_type == "resource_type":
            return f"{cleaned} 资源"
        if node_type == "reference":
            return f"{parent_name} · {cleaned}"
        if node_type == "mixed" and parent_name and cleaned != parent_name:
            return f"{parent_name} · {cleaned}"
        return cleaned or parent_name or "其他"

    def _build_children(self, groups: Dict[str, List[dict]], parent_name: str, *, depth: int, threshold: int, node_type: str) -> List[dict]:
        children = []
        others = []
        for label, group in groups.items():
            if label == "其他" or len(group) < self.merge_small_nodes_threshold:
                others.extend(group)
                continue
            child_name = self.infer_node_name(parent_name, label, node_type)
            children.append(self.build_hierarchy(group, child_name, threshold=threshold, depth=depth + 1, node_type=node_type))
        if others:
            children.append(self.make_node("其他", node_type="mixed", bookmarks=others))
        return children

    def optimize_tree(self, node: dict, *, is_root: bool = False) -> dict:
        optimized_children = [self.optimize_tree(child) for child in node.get("children", []) if child.get("count", 0) > 0]
        merged_children: dict[str, dict] = {}
        for child in optimized_children:
            key = self.normalize_name(child["name"])
            existing = merged_children.get(key)
            if existing is None:
                merged_children[key] = child
            else:
                existing["bookmarks"].extend(child.get("bookmarks", []))
                existing["children"].extend(child.get("children", []))
                existing["node_type"] = existing["node_type"] if existing["node_type"] == child.get("node_type") else "mixed"
                self.refresh_count(existing)

        node["children"] = sorted(merged_children.values(), key=lambda item: (-item["count"], item["name"]))

        low_value_bookmarks = []
        retained_children = []
        low_value_children = [
            child for child in node["children"]
            if child["count"] <= self.merge_small_nodes_threshold and not child.get("children")
        ]
        protected_child = low_value_children[0] if len(low_value_children) == len(node["children"]) and low_value_children else None
        for child in node["children"]:
            if child is protected_child:
                retained_children.append(child)
            elif child["count"] <= self.merge_small_nodes_threshold and not child.get("children"):
                low_value_bookmarks.extend(child.get("bookmarks", []))
            else:
                retained_children.append(child)
        node["children"] = retained_children
        if low_value_bookmarks:
            node.setdefault("bookmarks", []).extend(low_value_bookmarks)

        while len(node["children"]) == 1 and not node.get("bookmarks") and not is_root:
            only_child = node["children"][0]
            node["name"] = only_child["name"]
            node["node_type"] = only_child.get("node_type", node.get("node_type", "mixed"))
            node["children"] = only_child.get("children", [])
            node["bookmarks"] = only_child.get("bookmarks", [])

        return self.refresh_count(node)

    def build_hierarchy(self, bookmarks: List[dict], category: str, threshold: int = 20, depth: int = 0, node_type: str = "topic") -> Dict:
        node = self.make_node(category, node_type=node_type, bookmarks=list(bookmarks))
        if len(bookmarks) <= threshold or depth >= self.max_depth:
            return node

        domain_clusters = self.cluster_by_domain(bookmarks)
        large_domains = {domain: group for domain, group in domain_clusters.items() if len(group) >= self.domain_split_min_size}
        if len(large_domains) >= 2:
            children = self._build_children(large_domains, category, depth=depth, threshold=threshold, node_type="reference")
            grouped_ids = {id(item) for group in large_domains.values() for item in group}
            leftovers = [bookmark for bookmark in bookmarks if id(bookmark) not in grouped_ids]
            if leftovers:
                children.append(self.make_node("其他", bookmarks=leftovers))
            node["children"] = children
            node["bookmarks"] = []
            return self.optimize_tree(node, is_root=(depth == 0))

        keyword_clusters = self.cluster_by_keywords(bookmarks)
        interesting_groups = {label: group for label, group in keyword_clusters.items() if label != "其他" and len(group) >= self.min_cluster_size}
        if interesting_groups:
            children = self._build_children(interesting_groups, category, depth=depth, threshold=threshold, node_type="topic")
            other_group = keyword_clusters.get("其他", [])
            grouped_ids = {id(item) for group in interesting_groups.values() for item in group}
            leftovers = [bookmark for bookmark in bookmarks if id(bookmark) not in grouped_ids]
            leftovers.extend(other_group)
            deduped = []
            seen = set()
            for bookmark in leftovers:
                marker = id(bookmark)
                if marker in seen:
                    continue
                seen.add(marker)
                deduped.append(bookmark)
            if deduped:
                children.append(self.make_node("其他", bookmarks=deduped))
            node["children"] = children
            node["bookmarks"] = []
            return self.optimize_tree(node, is_root=(depth == 0))

        return node


def main() -> int:
    parser = build_parser("聚类分类结果")
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    config = load_config_from_args(args)
    logger = configure_logging(config, args.log_level)
    input_file = args.input or config.paths.classified_file
    output_file = args.output or config.paths.clustering_file

    if not input_file.exists():
        print(f"错误: 输入文件不存在: {input_file}")
        return 1

    data = json.loads(input_file.read_text(encoding="utf-8"))
    category_groups = defaultdict(list)
    for bookmark in data["bookmarks"]:
        category_groups[bookmark["classification"]["category"]].append(bookmark)

    options = config.clustering_options
    clusterer = BookmarkClusterer(
        min_cluster_size=options.get("min_cluster_size", 10),
        max_keywords=options.get("max_keywords", 3),
        max_depth=options.get("max_depth", 3),
        merge_small_nodes_threshold=options.get("merge_small_nodes_threshold"),
        domain_split_min_size=options.get("domain_split_min_size", 5),
    )
    hierarchy = {
        category: clusterer.build_hierarchy(
            bookmarks,
            category,
            threshold=options.get("max_bookmarks_without_clustering", 20),
        )
        for category, bookmarks in category_groups.items()
    }

    output = {
        "hierarchy": hierarchy,
        "stats": {
            "total_categories": len(hierarchy),
            "category_sizes": {category: item["count"] for category, item in hierarchy.items()},
            "subcategories_count": sum(1 for item in hierarchy.values() if item["children"]),
        },
    }
    ensure_parent(output_file)
    output_file.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info("步骤5完成: %s -> %s", input_file, output_file)
    print(f"✓ 聚类完成: {output_file}")
    print(f"  分类数: {output['stats']['total_categories']}")
    print(f"  含子分类的分类数: {output['stats']['subcategories_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

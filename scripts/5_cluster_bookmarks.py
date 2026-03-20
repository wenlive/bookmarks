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
    def __init__(self, min_cluster_size: int = 10, max_keywords: int = 3):
        self.min_cluster_size = min_cluster_size
        self.max_keywords = max_keywords

    def extract_keywords(self, text: str) -> List[str]:
        text = re.sub(r"[^\w\s\u4e00-\u9fff-]", " ", text.lower())
        words = [word for word in text.split() if word not in STOPWORDS and len(word) > 1]
        return words

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
            keywords = self.extract_keywords(text)
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

    def build_hierarchy(self, bookmarks: List[dict], category: str, threshold: int = 20) -> Dict:
        if len(bookmarks) <= threshold:
            return {"category": category, "subcategories": {}, "bookmarks": bookmarks, "count": len(bookmarks)}

        domain_clusters = self.cluster_by_domain(bookmarks)
        if len([group for group in domain_clusters.values() if len(group) >= 5]) >= 2:
            subcategories = {}
            ungrouped = []
            for domain, group in domain_clusters.items():
                if len(group) >= 5:
                    subcategories[f"{category}/{domain}"] = {"bookmarks": group, "count": len(group)}
                else:
                    ungrouped.extend(group)
            return {"category": category, "subcategories": subcategories, "bookmarks": ungrouped, "count": len(bookmarks)}

        keyword_clusters = self.cluster_by_keywords(bookmarks)
        subcategories = {}
        ungrouped = []
        for keyword, group in keyword_clusters.items():
            if keyword != "其他" and len(group) >= self.min_cluster_size:
                subcategories[f"{category}/{keyword}"] = {"bookmarks": group, "count": len(group)}
            else:
                ungrouped.extend(group)
        return {"category": category, "subcategories": subcategories, "bookmarks": ungrouped, "count": len(bookmarks)}


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
            "subcategories_count": sum(1 for item in hierarchy.values() if item["subcategories"]),
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

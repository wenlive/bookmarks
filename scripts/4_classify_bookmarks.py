#!/usr/bin/env python3
"""步骤4: 智能分类书签并导出待确认报告。"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Tuple


def metadata_texts(metadata: dict) -> dict:
    page = metadata.get("page_signals", {})
    site = metadata.get("site_signals", {})
    profile = metadata.get("site_profile", {})
    page_profile = profile.get("page", {}) if isinstance(profile, dict) else {}
    site_profile = profile.get("site", {}) if isinstance(profile, dict) else {}
    return {
        "title": metadata.get("title") or page.get("title") or page_profile.get("title") or "",
        "description": metadata.get("description") or page.get("description") or page_profile.get("description") or "",
        "keywords": metadata.get("keywords") or page.get("keywords") or page_profile.get("keywords") or "",
        "h1": metadata.get("h1") or page.get("h1") or page_profile.get("h1") or "",
        "content_preview": metadata.get("content_preview") or page.get("content_preview") or page_profile.get("content_preview") or "",
        "page_type_hints": " ".join(page.get("page_type_hints") or page_profile.get("page_type_hints") or []),
        "site_type_candidates": " ".join(site.get("site_type_candidates") or site_profile.get("site_type_candidates") or []),
        "brand_terms": " ".join(site.get("brand_terms") or site_profile.get("brand_terms") or []),
        "site_name": site.get("site_name") or site_profile.get("site_name") or page.get("og:site_name") or page_profile.get("og:site_name") or "",
    }

from common import build_parser, configure_logging, ensure_parent, load_config_from_args


class BookmarkClassifier:
    def __init__(self, rules_file: Path, classification_options: dict | None = None):
        self.rules = json.loads(rules_file.read_text(encoding="utf-8"))
        self.categories = self.rules["categories"]
        self.default_category = self.rules["default_category"]
        self.scoring = dict(self.rules["scoring"])
        if classification_options:
            self.scoring.update({k: v for k, v in classification_options.items() if k in self.scoring or k == "title_weight"})
        self.scoring.setdefault("title_weight", self.scoring.get("keyword_weight", 40))

    @staticmethod
    def _contains_keyword(text: str, keyword: str) -> bool:
        if not keyword:
            return False
        normalized = keyword.lower()
        haystack = text.lower()
        if len(normalized) <= 2 and normalized.isascii():
            return bool(re.search(rf"\b{re.escape(normalized)}\b", haystack))
        return normalized in haystack

    def calculate_domain_score(self, bookmark: dict, category_rules: dict) -> int:
        domain = bookmark.get("domain", "").lower()
        for pattern in category_rules.get("domains", []):
            normalized = pattern.lower()
            if domain == normalized or domain.endswith(f".{normalized}"):
                return 100
        return 0

    def calculate_keyword_score(self, bookmark: dict, category_rules: dict) -> int:
        metadata = metadata_texts(bookmark.get("metadata", {}))
        text = " ".join([bookmark.get("name", ""), metadata["keywords"], metadata["description"], metadata["site_name"], metadata["brand_terms"]])
        score = sum(20 for keyword in category_rules.get("keywords", []) if self._contains_keyword(text, keyword))
        return min(score, 100)

    def calculate_title_score(self, bookmark: dict, category_rules: dict) -> int:
        metadata = metadata_texts(bookmark.get("metadata", {}))
        candidates = [bookmark.get("name", ""), metadata["title"], metadata["h1"], metadata["site_name"]]
        for pattern in category_rules.get("title_patterns", []):
            if any(re.search(pattern, candidate, re.IGNORECASE) for candidate in candidates if candidate):
                return 80
        return 0

    def calculate_folder_score(self, bookmark: dict, category_rules: dict) -> int:
        folder_str = "/".join(bookmark.get("original_folder_path", []))
        for keyword in category_rules.get("folder_keywords", []):
            if self._contains_keyword(folder_str, keyword):
                return 80
        return 0

    def calculate_content_score(self, bookmark: dict, category_rules: dict) -> int:
        metadata = metadata_texts(bookmark.get("metadata", {}))
        content = " ".join([metadata["description"], metadata["h1"], metadata["content_preview"], metadata["page_type_hints"], metadata["site_type_candidates"]])
        score = sum(10 for keyword in category_rules.get("keywords", []) if self._contains_keyword(content, keyword))
        return min(score, 50)

    def classify_bookmark(self, bookmark: dict) -> Tuple[str, float, dict]:
        scores = {}
        for category_name, category_rules in self.categories.items():
            domain_score = self.calculate_domain_score(bookmark, category_rules)
            keyword_score = self.calculate_keyword_score(bookmark, category_rules)
            title_score = self.calculate_title_score(bookmark, category_rules)
            folder_score = self.calculate_folder_score(bookmark, category_rules)
            content_score = self.calculate_content_score(bookmark, category_rules)
            total_score = (
                domain_score * self.scoring["domain_weight"] / 100
                + keyword_score * self.scoring["keyword_weight"] / 100
                + title_score * self.scoring["title_weight"] / 100
                + folder_score * self.scoring["folder_weight"] / 100
                + content_score * self.scoring["content_weight"] / 100
            )
            if total_score > 0:
                scores[category_name] = {
                    "total": total_score,
                    "domain": domain_score,
                    "keyword": keyword_score,
                    "title": title_score,
                    "folder": folder_score,
                    "content": content_score,
                }

        if not scores:
            return self.default_category, 0, {}

        best_category = max(scores.items(), key=lambda item: item[1]["total"])
        if best_category[1]["total"] < self.scoring["min_score"]:
            return self.default_category, best_category[1]["total"], scores
        return best_category[0], best_category[1]["total"], scores

    def classify_all(self, bookmarks: list) -> tuple[list, dict, list]:
        results = []
        category_stats = defaultdict(int)
        confirm_needed = []

        for bookmark in bookmarks:
            category, score, all_scores = self.classify_bookmark(bookmark)
            classified = bookmark.copy()
            classified["classification"] = {
                "category": category,
                "score": score,
                "needs_confirmation": score < self.scoring["confirm_threshold"],
                "all_scores": all_scores,
            }
            results.append(classified)
            category_stats[category] += 1
            if category != self.default_category and classified["classification"]["needs_confirmation"]:
                confirm_needed.append(classified)
        return results, dict(category_stats), confirm_needed


def export_confirmation_report(confirm_needed: list, report_file: Path) -> None:
    ensure_parent(report_file)
    report = {
        "count": len(confirm_needed),
        "bookmarks": [
            {
                "id": bm["id"],
                "name": bm["name"],
                "url": bm["url"],
                "category": bm["classification"]["category"],
                "score": bm["classification"]["score"],
            }
            for bm in confirm_needed
        ],
    }
    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = build_parser("分类书签")
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--rules", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=None, help="待确认报告输出路径")
    args = parser.parse_args()

    config = load_config_from_args(args)
    logger = configure_logging(config, args.log_level)
    input_file = args.input or config.paths.enriched_file
    rules_file = args.rules or config.paths.rules_file
    output_file = args.output or config.paths.classified_file
    report_file = args.report or config.paths.confirmation_report_file

    if not input_file.exists():
        print(f"错误: 输入文件不存在: {input_file}")
        return 1
    if not rules_file.exists():
        print(f"错误: 分类规则文件不存在: {rules_file}")
        return 1

    bookmarks = json.loads(input_file.read_text(encoding="utf-8"))["bookmarks"]
    classifier = BookmarkClassifier(rules_file, config.classification_options)
    classified_bookmarks, category_stats, confirm_needed = classifier.classify_all(bookmarks)

    output = {
        "bookmarks": classified_bookmarks,
        "stats": {
            "total_bookmarks": len(classified_bookmarks),
            "category_distribution": category_stats,
            "confirm_needed_count": len(confirm_needed),
            "confirm_needed_ids": [bm["id"] for bm in confirm_needed[:100]],
        },
    }
    ensure_parent(output_file)
    output_file.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    export_confirmation_report(confirm_needed, report_file)

    logger.info("步骤4完成: %s -> %s", input_file, output_file)
    print(f"✓ 分类完成: {output_file}")
    print(f"  分类数: {len(category_stats)}")
    print(f"  待确认书签数: {len(confirm_needed)}")
    print(f"  待确认报告: {report_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

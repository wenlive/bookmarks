#!/usr/bin/env python3
"""步骤4: 基于先验规则与开放候选的多维书签标注。"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from common import build_parser, configure_logging, ensure_parent, load_config_from_args


TOKEN_STOPWORDS = {
    "www", "com", "cn", "org", "net", "io", "co", "dev", "docs", "doc", "blog", "blogs", "www2",
    "the", "and", "for", "with", "from", "into", "your", "that", "this", "guide", "tutorial", "learn",
    "official", "reference", "documentation", "intro", "about", "index", "article", "posts", "post", "home",
    "的", "了", "和", "是", "在", "用", "教程", "指南", "文档", "文章", "首页", "官网", "页面",
}


class BookmarkClassifier:
    def __init__(self, rules_file: Path, classification_options: dict | None = None):
        self.rules = json.loads(rules_file.read_text(encoding="utf-8"))
        self.categories = self.rules["categories"]
        self.default_category = self.rules["default_category"]
        self.scoring = dict(self.rules["scoring"])
        if classification_options:
            self.scoring.update({k: v for k, v in classification_options.items() if k in self.scoring or k == "title_weight"})
        self.scoring.setdefault("title_weight", self.scoring.get("keyword_weight", 40))
        self.resource_type_rules = self.rules.get("resource_type_rules", {})
        self.intent_rules = self.rules.get("intent_rules", {})
        self.quality_signal_rules = self.rules.get("quality_signal_rules", {})
        self.dynamic_topic_rules = self.rules.get("dynamic_topic_rules", {})

    @staticmethod
    def _contains_keyword(text: str, keyword: str) -> bool:
        if not keyword:
            return False
        normalized = keyword.lower()
        haystack = text.lower()
        if normalized.isascii() and re.fullmatch(r"[a-z0-9+.#/-]+", normalized):
            return bool(re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", haystack))
        if len(normalized) <= 2 and normalized.isascii():
            return bool(re.search(rf"\b{re.escape(normalized)}\b", haystack))
        return normalized in haystack

    @staticmethod
    def _normalize_label(label: str) -> str:
        return re.sub(r"\s+", " ", label.strip())

    @staticmethod
    def _title_case_token(token: str) -> str:
        if token.isupper() or any(ch.isdigit() for ch in token):
            return token
        if len(token) <= 4 and token.isascii():
            return token.upper() if token.lower() in {"api", "sdk", "cli", "llm", "aws", "gcp", "css", "html", "json", "yaml"} else token.capitalize()
        return token.capitalize()

    def _collect_text_fields(self, bookmark: dict) -> dict[str, str]:
        metadata = bookmark.get("metadata", {})
        folder_path = " / ".join(bookmark.get("original_folder_path", []))
        parsed = urlparse(bookmark.get("url", ""))
        return {
            "name": bookmark.get("name", ""),
            "title": metadata.get("title", ""),
            "h1": metadata.get("h1", ""),
            "description": metadata.get("description", ""),
            "keywords": metadata.get("keywords", ""),
            "content_preview": metadata.get("content_preview", ""),
            "site_profile": metadata.get("site_profile", ""),
            "folder_path": folder_path,
            "url": bookmark.get("url", ""),
            "domain": bookmark.get("domain", ""),
            "url_path": parsed.path or "",
        }

    def calculate_domain_score(self, bookmark: dict, category_rules: dict) -> int:
        domain = bookmark.get("domain", "").lower()
        for pattern in category_rules.get("domains", []):
            normalized = pattern.lower()
            if domain == normalized or domain.endswith(f".{normalized}"):
                return 100
        return 0

    def calculate_keyword_score(self, bookmark: dict, category_rules: dict) -> int:
        text_fields = self._collect_text_fields(bookmark)
        text = " ".join([text_fields["name"], text_fields["title"], text_fields["keywords"], text_fields["description"], text_fields["site_profile"]])
        score = sum(20 for keyword in category_rules.get("keywords", []) if self._contains_keyword(text, keyword))
        return min(score, 100)

    def calculate_title_score(self, bookmark: dict, category_rules: dict) -> int:
        text_fields = self._collect_text_fields(bookmark)
        candidates = [text_fields["name"], text_fields["title"], text_fields["h1"]]
        for pattern in category_rules.get("title_patterns", []):
            if any(re.search(pattern, candidate, re.IGNORECASE) for candidate in candidates if candidate):
                return 80
        return 0

    def calculate_folder_score(self, bookmark: dict, category_rules: dict) -> int:
        folder_str = " / ".join(bookmark.get("original_folder_path", []))
        for keyword in category_rules.get("folder_keywords", []):
            if self._contains_keyword(folder_str, keyword):
                return 80
        return 0

    def calculate_content_score(self, bookmark: dict, category_rules: dict) -> int:
        text_fields = self._collect_text_fields(bookmark)
        content = " ".join([text_fields["description"], text_fields["h1"], text_fields["content_preview"], text_fields["site_profile"]])
        score = sum(10 for keyword in category_rules.get("keywords", []) if self._contains_keyword(content, keyword))
        return min(score, 50)

    def _score_category(self, bookmark: dict, category_name: str, category_rules: dict) -> dict[str, Any] | None:
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
        if total_score <= 0:
            return None
        evidence = []
        if domain_score:
            evidence.append({"signal": "domain", "strength": domain_score, "matched": bookmark.get("domain", "")})
        if keyword_score:
            evidence.append({"signal": "keywords", "strength": keyword_score, "matched": category_rules.get("keywords", [])})
        if title_score:
            evidence.append({"signal": "title", "strength": title_score, "matched": category_rules.get("title_patterns", [])})
        if folder_score:
            evidence.append({"signal": "folder", "strength": folder_score, "matched": category_rules.get("folder_keywords", [])})
        if content_score:
            evidence.append({"signal": "content", "strength": content_score, "matched": category_rules.get("keywords", [])})
        return {
            "topic": category_name,
            "total": round(total_score, 2),
            "domain": domain_score,
            "keyword": keyword_score,
            "title": title_score,
            "folder": folder_score,
            "content": content_score,
            "evidence": evidence,
        }

    def _infer_resource_type(self, bookmark: dict) -> tuple[str, list[dict[str, Any]]]:
        text_fields = self._collect_text_fields(bookmark)
        text = " ".join(text_fields.values()).lower()
        parsed = urlparse(bookmark.get("url", ""))
        path = parsed.path.lower()
        scores = Counter()
        evidence = defaultdict(list)
        for resource_type, rules in self.resource_type_rules.items():
            for domain in rules.get("domains", []):
                domain_value = bookmark.get("domain", "").lower()
                if domain_value == domain or domain_value.endswith(f".{domain}"):
                    scores[resource_type] += 4
                    evidence[resource_type].append({"signal": "domain", "matched": domain})
            for pattern in rules.get("url_patterns", []):
                if re.search(pattern, path, re.IGNORECASE):
                    scores[resource_type] += 3
                    evidence[resource_type].append({"signal": "url", "matched": pattern})
            for keyword in rules.get("keywords", []):
                if self._contains_keyword(text, keyword):
                    scores[resource_type] += 2
                    evidence[resource_type].append({"signal": "keyword", "matched": keyword})
            for pattern in rules.get("title_patterns", []):
                if any(re.search(pattern, text_fields[field], re.IGNORECASE) for field in ("name", "title", "h1") if text_fields[field]):
                    scores[resource_type] += 2
                    evidence[resource_type].append({"signal": "title", "matched": pattern})
        if path.endswith(".pdf"):
            scores["论文"] += 3
            evidence["论文"].append({"signal": "extension", "matched": ".pdf"})
        if not scores:
            return "未知", []
        best_type, _ = scores.most_common(1)[0]
        return best_type, evidence[best_type]

    def _infer_intent_labels(self, bookmark: dict) -> list[str]:
        text = " ".join(self._collect_text_fields(bookmark).values())
        labels = []
        for label, keywords in self.intent_rules.items():
            if any(self._contains_keyword(text, keyword) for keyword in keywords):
                labels.append(label)
        return labels

    def _infer_quality_signals(self, bookmark: dict, topic_scores: list[dict[str, Any]], resource_type: str) -> list[str]:
        text_fields = self._collect_text_fields(bookmark)
        text = " ".join(text_fields.values()).lower()
        signals = []
        domain = bookmark.get("domain", "").lower()
        for signal, rules in self.quality_signal_rules.items():
            matched = False
            for suffix in rules.get("domains", []):
                if domain == suffix or domain.endswith(f".{suffix}"):
                    matched = True
                    break
            if not matched and any(self._contains_keyword(text, keyword) for keyword in rules.get("keywords", [])):
                matched = True
            if matched:
                signals.append(signal)
        if resource_type == "文档" and (domain.startswith("docs.") or "/docs" in text_fields["url_path"].lower()):
            signals.append("官方")
        if resource_type == "博客" and domain not in {"", "medium.com"}:
            signals.append("社区")
        if len(text_fields["description"].strip()) < 20 and len(text_fields["site_profile"].strip()) < 15:
            signals.append("低信息量页")
        if topic_scores and topic_scores[0]["total"] >= max(self.scoring["confirm_threshold"], self.scoring["min_score"] + 10):
            signals.append("高频访问候选")
        return sorted(set(signals))

    def _folder_alignment_score(self, topic_scores: list[dict[str, Any]]) -> float:
        if not topic_scores:
            return 0.0
        best = topic_scores[0]
        if not best["total"]:
            return 0.0
        return round(min(best["folder"] / 80, 1.0), 2) if best["folder"] else 0.0

    def _extract_dynamic_topic_candidates(self, bookmark: dict, matched_topics: list[str]) -> list[dict[str, Any]]:
        text_fields = self._collect_text_fields(bookmark)
        parsed = urlparse(bookmark.get("url", ""))
        raw_tokens: list[tuple[str, str]] = []
        for source in ("site_profile", "title", "name", "keywords", "url_path"):
            value = text_fields[source]
            raw_tokens.extend((token, source) for token in re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{2,}|[\u4e00-\u9fff]{2,}", value))
        raw_tokens.extend((segment, "domain") for segment in parsed.netloc.split(".") if len(segment) > 2)

        known_topic_tokens = {part.lower() for topic in self.categories for part in re.split(r"[/-]", topic) if part}
        allowed_short = {token.lower() for token in self.dynamic_topic_rules.get("allow_short_tokens", [])}
        candidates: dict[str, dict[str, Any]] = {}
        for token, source in raw_tokens:
            normalized = token.strip("-_.").lower()
            if not normalized or normalized in TOKEN_STOPWORDS:
                continue
            if len(normalized) < 4 and normalized not in allowed_short:
                continue
            if normalized in known_topic_tokens:
                continue
            if normalized.isdigit():
                continue
            label = self._title_case_token(token.strip("-_."))
            item = candidates.setdefault(label.lower(), {"topic": label, "score": 0, "sources": set()})
            item["score"] += 2 if source in {"site_profile", "title", "name"} else 1
            item["sources"].add(source)

        items = []
        matched_lower = {topic.lower() for topic in matched_topics}
        for item in candidates.values():
            if item["topic"].lower() in matched_lower:
                continue
            items.append({
                "topic": item["topic"],
                "score": item["score"],
                "sources": sorted(item["sources"]),
            })
        items.sort(key=lambda entry: (-entry["score"], entry["topic"]))
        return items[: self.dynamic_topic_rules.get("max_candidates", 8)]

    def classify_bookmark(self, bookmark: dict) -> dict[str, Any]:
        topic_scores = []
        for category_name, category_rules in self.categories.items():
            scored = self._score_category(bookmark, category_name, category_rules)
            if scored:
                topic_scores.append(scored)
        topic_scores.sort(key=lambda item: item["total"], reverse=True)

        confident_topics = [item for item in topic_scores if item["total"] >= self.scoring["min_score"]]
        primary_topics = [item["topic"] for item in confident_topics[:2]]
        secondary_topics = [item["topic"] for item in confident_topics[2:5]]
        fallback_category = primary_topics[0] if primary_topics else self.default_category
        folder_alignment_score = self._folder_alignment_score(topic_scores)
        resource_type, resource_type_evidence = self._infer_resource_type(bookmark)
        intent_labels = self._infer_intent_labels(bookmark)
        topic_labels = sorted(set(primary_topics + secondary_topics))
        dynamic_candidates = self._extract_dynamic_topic_candidates(bookmark, topic_labels)
        quality_signals = self._infer_quality_signals(bookmark, topic_scores, resource_type)
        top_score = topic_scores[0]["total"] if topic_scores else 0.0
        needs_confirmation = top_score < self.scoring["confirm_threshold"]
        classification_evidence = {
            "topic_scores": topic_scores[:8],
            "resource_type": resource_type_evidence,
            "dynamic_topic_candidates": dynamic_candidates,
            "folder_alignment_score": folder_alignment_score,
        }

        return {
            "category": fallback_category,
            "primary_topics": primary_topics or ([self.default_category] if not dynamic_candidates else []),
            "secondary_topics": secondary_topics,
            "topic_labels": topic_labels,
            "resource_type": resource_type,
            "intent_labels": intent_labels,
            "quality_signals": quality_signals,
            "open_topic_candidates": dynamic_candidates,
            "classification_evidence": classification_evidence,
            "score": round(top_score, 2),
            "needs_confirmation": needs_confirmation,
            "all_scores": {item["topic"]: {k: v for k, v in item.items() if k != "topic"} for item in topic_scores},
            "folder_alignment_score": folder_alignment_score,
        }

    def classify_all(self, bookmarks: list) -> tuple[list, dict, list]:
        results = []
        topic_stats = defaultdict(int)
        resource_type_stats = defaultdict(int)
        uncovered_topic_candidates = Counter()
        low_confidence_items = []
        confirm_needed = []

        for bookmark in bookmarks:
            classification = self.classify_bookmark(bookmark)
            classified = bookmark.copy()
            classified["classification"] = classification
            results.append(classified)

            topic_key = classification["category"]
            topic_stats[topic_key] += 1
            resource_type_stats[classification["resource_type"]] += 1
            for candidate in classification["open_topic_candidates"][:3]:
                uncovered_topic_candidates[candidate["topic"]] += candidate["score"]
            if classification["needs_confirmation"]:
                low_confidence_items.append(
                    {
                        "id": bookmark.get("id"),
                        "name": bookmark.get("name"),
                        "url": bookmark.get("url"),
                        "score": classification["score"],
                        "primary_topics": classification["primary_topics"],
                        "open_topic_candidates": classification["open_topic_candidates"][:3],
                    }
                )
                if classification["category"] != self.default_category:
                    confirm_needed.append(classified)

        stats = {
            "topic_distribution": dict(topic_stats),
            "resource_type_distribution": dict(resource_type_stats),
            "uncovered_topic_candidates": [
                {"topic": topic, "score": score}
                for topic, score in uncovered_topic_candidates.most_common(20)
            ],
            "low_confidence_items": low_confidence_items[:100],
            "confirm_needed_count": len(confirm_needed),
            "confirm_needed_ids": [bm["id"] for bm in confirm_needed[:100]],
        }
        return results, stats, confirm_needed


def export_confirmation_report(confirm_needed: list, report_file: Path) -> None:
    ensure_parent(report_file)
    report = {
        "count": len(confirm_needed),
        "bookmarks": [
            {
                "id": bm["id"],
                "name": bm["name"],
                "url": bm["url"],
                "primary_topics": bm["classification"]["primary_topics"],
                "resource_type": bm["classification"]["resource_type"],
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
    classified_bookmarks, stats, confirm_needed = classifier.classify_all(bookmarks)

    output = {
        "bookmarks": classified_bookmarks,
        "stats": {
            "total_bookmarks": len(classified_bookmarks),
            **stats,
        },
    }
    ensure_parent(output_file)
    output_file.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    export_confirmation_report(confirm_needed, report_file)

    logger.info("步骤4完成: %s -> %s", input_file, output_file)
    print(f"✓ 分类完成: {output_file}")
    print(f"  主题数: {len(stats['topic_distribution'])}")
    print(f"  资源类型数: {len(stats['resource_type_distribution'])}")
    print(f"  待确认书签数: {len(confirm_needed)}")
    print(f"  待确认报告: {report_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""步骤4: 基于先验规则与开放候选的多维书签标注。"""
from __future__ import annotations

import copy
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from common import (
    CLASSIFIED_OUTPUT_SCHEMA_VERSION,
    DEFAULT_GENERIC_PLATFORM_DOMAINS,
    FETCH_OUTPUT_SCHEMA_VERSION,
    GENERIC_PLATFORM_TOKENS,
    SOURCE_LIKE_TOPIC_TOKENS,
    build_parser,
    build_signal_pack,
    configure_logging,
    ensure_parent,
    flatten_signal_pack,
    is_generic_platform_domain,
    is_source_like_topic_token,
    load_config_from_args,
    normalize_topic_token,
    require_payload_schema,
    signal_family_names,
    signal_pack_sections,
)


TOKEN_STOPWORDS = {
    "www", "com", "cn", "org", "net", "io", "co", "dev", "docs", "doc", "blog", "blogs", "www2",
    "the", "and", "for", "with", "from", "into", "your", "that", "this", "guide", "tutorial", "learn",
    "official", "reference", "documentation", "intro", "about", "index", "article", "posts", "post", "home",
    "product", "tool", "tools", "general", "read", "free", "download", "downloads", "file", "files",
    "的", "了", "和", "是", "在", "用", "教程", "指南", "文档", "文章", "首页", "官网", "页面",
}


def signal_text_fields(bookmark: dict[str, Any]) -> dict[str, str]:
    signal_pack = bookmark.get("signal_pack") or build_signal_pack(bookmark)
    sections = signal_pack_sections(signal_pack)
    identity = sections["identity"]
    content = sections["content"]
    structure = sections["structure"]
    health_access = sections["health_access"]
    context_time = sections["context_time"]
    return {
        "name": str(bookmark.get("name", "") or ""),
        "title": " ".join(content.get("title_candidates") or []) or str(content.get("preferred_title", "") or ""),
        "h1": " ".join(structure.get("headings_h1") or []),
        "description": str(content.get("preferred_description", "") or ""),
        "keywords": str(content.get("keywords_text", "") or ""),
        "content_preview": str(content.get("main_text", "") or ""),
        "site_profile": " ".join(
            part
            for part in (
                structure.get("site_name", ""),
                " ".join(structure.get("brand_terms") or []),
                " ".join(structure.get("site_type_candidates") or []),
                " ".join(structure.get("page_type_hints") or []),
                " ".join(structure.get("schema_types") or []),
            )
            if part
        ),
        "semantic_text": str(content.get("semantic_text", "") or ""),
        "schema_types": " ".join(structure.get("schema_types") or []),
        "page_type_hints": " ".join(structure.get("page_type_hints") or []),
        "site_type_candidates": " ".join(structure.get("site_type_candidates") or []),
        "source_facets": " ".join(structure.get("source_facets") or []),
        "site_name": str(structure.get("site_name", "") or ""),
        "brand_terms": " ".join(structure.get("brand_terms") or []),
        "language": str(content.get("language", "") or ""),
        "folder_path": " / ".join(context_time.get("original_folder_path") or []),
        "url": str(bookmark.get("url", "") or ""),
        "domain": str(identity.get("domain", "") or bookmark.get("domain", "") or ""),
        "url_path": "/" + "/".join(identity.get("path_segments") or []),
        "fetch_status": str(health_access.get("fetch_status", "") or ""),
    }


def merge_rule_payload(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        merged = {key: copy.deepcopy(value) for key, value in base.items()}
        for key, value in override.items():
            if key in merged:
                merged[key] = merge_rule_payload(merged[key], value)
            else:
                merged[key] = copy.deepcopy(value)
        return merged

    if isinstance(base, list) and isinstance(override, list):
        merged = list(base)
        seen = {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in merged}
        for item in override:
            marker = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if marker not in seen:
                merged.append(copy.deepcopy(item))
                seen.add(marker)
        return merged

    return copy.deepcopy(override)


def load_rule_bundle(rules_file: Path, overrides_file: Path | None = None) -> dict[str, Any]:
    payload = json.loads(rules_file.read_text(encoding="utf-8"))
    if overrides_file and overrides_file.exists():
        override_payload = json.loads(overrides_file.read_text(encoding="utf-8"))
        payload = merge_rule_payload(payload, override_payload)
    return payload


class BookmarkClassifier:
    def __init__(self, rules_file: Path, classification_options: dict | None = None, overrides_file: Path | None = None):
        self.rules = load_rule_bundle(rules_file, overrides_file)
        self.categories = merge_rule_payload(
            self.rules.get("categories") or {},
            self.rules.get("topics") or {},
        )
        self.non_topic_categories = set(self.rules.get("non_topic_categories", []))
        self.default_category = self.rules.get("default_category", "待整理")
        self.scoring = dict(self.rules["scoring"])
        if classification_options:
            self.scoring.update({k: v for k, v in classification_options.items() if k in self.scoring or k == "title_weight"})
        self.scoring.setdefault("title_weight", self.scoring.get("keyword_weight", 40))
        facets = self.rules.get("facets", {})
        self.resource_type_rules = facets.get("resource_types") or self.rules.get("resource_type_rules", {})
        self.intent_rules = facets.get("intents") or self.rules.get("intent_rules", {})
        self.quality_signal_rules = facets.get("quality_signals") or self.rules.get("quality_signal_rules", {})
        self.dynamic_topic_rules = self.rules.get("dynamic_topic_rules", {})
        self.cluster_hint_limit = self.dynamic_topic_rules.get("cluster_hint_limit", 12)
        configured_blocked_tokens = {
            normalize_topic_token(item)
            for item in self.dynamic_topic_rules.get("blocked_tokens", [])
            if normalize_topic_token(item)
        }
        self.source_like_topic_tokens = set(SOURCE_LIKE_TOPIC_TOKENS) | configured_blocked_tokens
        self.generic_platform_domains = {
            item.lower()
            for item in self.rules.get("generic_platform_domains", DEFAULT_GENERIC_PLATFORM_DOMAINS)
        } or set(DEFAULT_GENERIC_PLATFORM_DOMAINS)

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

    def _is_generic_platform_bookmark(self, bookmark: dict) -> bool:
        parsed = urlparse(bookmark.get("url", ""))
        domain = (bookmark.get("domain") or parsed.netloc).lower()
        return is_generic_platform_domain(domain, self.generic_platform_domains)

    @staticmethod
    def _generic_platform_token_key(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", (value or "").lower())

    def _is_generic_platform_token(self, value: str) -> bool:
        key = self._generic_platform_token_key(self._normalize_label(value))
        return key in GENERIC_PLATFORM_TOKENS

    def _is_source_like_topic_token(self, value: str) -> bool:
        return is_source_like_topic_token(value, self.source_like_topic_tokens)

    def _collect_text_fields(self, bookmark: dict) -> dict[str, str]:
        return signal_text_fields(bookmark)

    def calculate_domain_score(self, bookmark: dict, category_rules: dict) -> int:
        domain = self._collect_text_fields(bookmark)["domain"].lower()
        for pattern in category_rules.get("domains", []):
            normalized = pattern.lower()
            if domain == normalized or domain.endswith(f".{normalized}"):
                return 100
        return 0

    def calculate_keyword_score(self, bookmark: dict, category_rules: dict) -> int:
        text_fields = self._collect_text_fields(bookmark)
        text = " ".join([
            text_fields["title"],
            text_fields["description"],
            text_fields["keywords"],
            text_fields["site_name"],
            text_fields["brand_terms"],
            text_fields["language"],
        ])
        score = sum(20 for keyword in category_rules.get("keywords", []) if self._contains_keyword(text, keyword))
        return min(score, 100)

    def calculate_title_score(self, bookmark: dict, category_rules: dict) -> int:
        signal_pack = bookmark.get("signal_pack") or build_signal_pack(bookmark)
        text_fields = self._collect_text_fields(bookmark)
        candidates = list(signal_pack.get("title_candidates") or []) + [text_fields["title"], text_fields["h1"], text_fields["site_name"]]
        for pattern in category_rules.get("title_patterns", []):
            if any(re.search(pattern, candidate, re.IGNORECASE) for candidate in candidates if candidate):
                return 80
        return 0

    def calculate_folder_score(self, bookmark: dict, category_rules: dict) -> int:
        # Historical Chrome folders are user-maintained, often stale, and can
        # contain broad buckets such as "数据库" that corrupt topic assignment.
        # Keep the method for compatibility with older tests/callers, but never
        # let folder names contribute to topic scores.
        return 0

    def calculate_content_score(self, bookmark: dict, category_rules: dict) -> int:
        text_fields = self._collect_text_fields(bookmark)
        content = " ".join([
            text_fields["semantic_text"],
            text_fields["description"],
            text_fields["h1"],
            text_fields["content_preview"],
            text_fields["page_type_hints"],
            text_fields["site_type_candidates"],
            text_fields["schema_types"],
        ])
        score = sum(10 for keyword in category_rules.get("keywords", []) if self._contains_keyword(content, keyword))
        return min(score, 80)

    def _score_category(self, bookmark: dict, category_name: str, category_rules: dict) -> dict[str, Any] | None:
        if category_name in self.non_topic_categories or category_rules.get("topic") is False:
            return None
        domain_score = self.calculate_domain_score(bookmark, category_rules)
        keyword_score = self.calculate_keyword_score(bookmark, category_rules)
        title_score = self.calculate_title_score(bookmark, category_rules)
        folder_score = self.calculate_folder_score(bookmark, category_rules)
        content_score = self.calculate_content_score(bookmark, category_rules)
        total_score = (
            domain_score * self.scoring["domain_weight"] / 100
            + keyword_score * self.scoring["keyword_weight"] / 100
            + title_score * self.scoring["title_weight"] / 100
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
        text = " ".join(value for key, value in text_fields.items() if key != "folder_path").lower()
        parsed = urlparse(bookmark.get("url", ""))
        path = parsed.path.lower()
        scores = Counter()
        evidence = defaultdict(list)
        signal_pack = bookmark.get("signal_pack") or build_signal_pack(bookmark)
        for facet in signal_pack.get("resource_facets", []):
            scores[facet] += 5
            evidence[facet].append({"signal": "structured_facet", "matched": facet})
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
        text_fields = self._collect_text_fields(bookmark)
        text = " ".join(value for key, value in text_fields.items() if key != "folder_path")
        labels = []
        for label, keywords in self.intent_rules.items():
            if any(self._contains_keyword(text, keyword) for keyword in keywords):
                labels.append(label)
        return labels

    def _infer_quality_signals(self, bookmark: dict, topic_scores: list[dict[str, Any]], resource_type: str) -> list[str]:
        text_fields = self._collect_text_fields(bookmark)
        text = " ".join(value for key, value in text_fields.items() if key != "folder_path").lower()
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
        return 0.0

    def _extract_dynamic_topic_candidates(self, bookmark: dict, matched_topics: list[str]) -> list[dict[str, Any]]:
        text_fields = self._collect_text_fields(bookmark)
        parsed = urlparse(bookmark.get("url", ""))
        generic_platform = self._is_generic_platform_bookmark(bookmark)
        raw_tokens: list[tuple[str, str]] = []
        for source in ("site_profile", "title", "name", "keywords", "url_path"):
            value = text_fields[source]
            raw_tokens.extend((token, source) for token in re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{2,}|[\u4e00-\u9fff]{2,}", value))
        raw_tokens.extend((segment, "domain") for segment in parsed.netloc.split(".") if len(segment) > 2)

        known_topic_tokens = {
            part.lower()
            for topic, rules in self.categories.items()
            if topic not in self.non_topic_categories and rules.get("topic") is not False
            for part in re.split(r"[/-]", topic)
            if part
        }
        allowed_short = {token.lower() for token in self.dynamic_topic_rules.get("allow_short_tokens", [])}
        candidates: dict[str, dict[str, Any]] = {}
        for token, source in raw_tokens:
            normalized = token.strip("-_.").lower()
            if not normalized or normalized in TOKEN_STOPWORDS:
                continue
            if generic_platform and self._is_generic_platform_token(normalized):
                continue
            if self._is_source_like_topic_token(normalized):
                continue
            if len(normalized) < 4 and normalized not in allowed_short:
                continue
            if normalized in known_topic_tokens:
                continue
            if normalized.isdigit():
                continue
            label = self._title_case_token(token.strip("-_."))
            if self._is_source_like_topic_token(label):
                continue
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

    @staticmethod
    def _category_root(topic: str) -> str:
        return topic.split("/")[0] if "/" in topic else topic

    @staticmethod
    def _category_leaf(topic: str) -> str:
        parts = [part for part in topic.split("/") if part]
        return parts[-1] if parts else topic

    def _is_strong_rule_evidence(self, score_item: dict[str, Any]) -> bool:
        return any(
            evidence.get("signal") in {"domain", "title"}
            or (evidence.get("signal") == "keywords" and evidence.get("strength", 0) >= 40)
            for evidence in score_item.get("evidence", [])
        )

    def _build_rule_candidates(self, topic_scores: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
        candidates = []
        for item in topic_scores[:limit]:
            topic = item["topic"]
            candidates.append(
                {
                    "category": topic,
                    "root": self._category_root(topic),
                    "leaf": self._category_leaf(topic),
                    "total": item["total"],
                    "strong_evidence": self._is_strong_rule_evidence(item),
                    "evidence": item.get("evidence", []),
                }
            )
        return candidates

    def _build_rule_roots(self, topic_scores: list[dict[str, Any]]) -> list[dict[str, Any]]:
        totals: Counter[str] = Counter()
        min_root_score = max(10.0, float(self.scoring.get("min_score", 15)) * 0.7)
        for item in topic_scores[:8]:
            if item["total"] < min_root_score:
                continue
            totals[self._category_root(item["topic"])] += item["total"]
        grand_total = sum(totals.values())
        roots = []
        for root, total in totals.most_common():
            roots.append(
                {
                    "root": root,
                    "total": round(total, 2),
                    "support": round(total / grand_total, 4) if grand_total else 0.0,
                }
            )
        return roots

    def _rule_confidence(self, topic_scores: list[dict[str, Any]]) -> float:
        if not topic_scores:
            return 0.0

        top1 = topic_scores[0]["total"]
        top2 = topic_scores[1]["total"] if len(topic_scores) > 1 else 0.0
        margin = max(top1 - top2, 0.0)
        threshold = max(float(self.scoring.get("confirm_threshold", 25)), 1.0)
        confidence = min(top1 / threshold, 1.0) * 0.55 + min(margin / 20.0, 1.0) * 0.25
        if self._is_strong_rule_evidence(topic_scores[0]):
            confidence += 0.2
        if not any(
            evidence.get("signal") in {"domain", "title", "keywords", "content"}
            for evidence in topic_scores[0].get("evidence", [])
        ):
            confidence = min(confidence, 0.35)
        return round(min(confidence, 1.0), 3)

    def _cluster_hint_tokens(self, text: str) -> list[str]:
        allowed_short = {token.lower() for token in self.dynamic_topic_rules.get("allow_short_tokens", [])}
        tokens = []
        for raw in re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{1,}|[\u4e00-\u9fff]{2,}", text or ""):
            normalized = raw.strip("-_.")
            lowered = normalized.lower()
            if not lowered or lowered in TOKEN_STOPWORDS or lowered.isdigit():
                continue
            if self._is_source_like_topic_token(normalized):
                continue
            if len(lowered) < 4 and lowered not in allowed_short:
                continue
            tokens.append(self._title_case_token(normalized))
        return tokens

    def _extract_cluster_hints(
        self,
        bookmark: dict,
        topic_scores: list[dict[str, Any]],
        dynamic_candidates: list[dict[str, Any]],
    ) -> list[str]:
        text_fields = self._collect_text_fields(bookmark)
        generic_platform = self._is_generic_platform_bookmark(bookmark)
        hints: list[str] = []

        for candidate in dynamic_candidates[:6]:
            hints.append(candidate["topic"])

        site_name = text_fields["site_name"].strip()
        if (
            site_name
            and not self._is_source_like_topic_token(site_name)
            and not (generic_platform and self._is_generic_platform_token(site_name))
        ):
            hints.append(site_name)

        for value in (
            text_fields["brand_terms"],
            text_fields["name"],
            text_fields["title"],
            text_fields["h1"],
            text_fields["keywords"],
            text_fields["url_path"],
        ):
            hints.extend(self._cluster_hint_tokens(value))

        for item in topic_scores[:3]:
            if item["total"] < max(10, self.scoring.get("min_score", 15) * 0.7):
                continue
            if not any(evidence.get("signal") != "folder" for evidence in item.get("evidence", [])):
                continue
            hints.append(self._category_root(item["topic"]))
            hints.append(self._category_leaf(item["topic"]))

        deduped = []
        seen = set()
        for hint in hints:
            normalized = self._normalize_label(str(hint))
            if not normalized:
                continue
            if generic_platform and self._is_generic_platform_token(normalized):
                continue
            if self._is_source_like_topic_token(normalized):
                continue
            marker = normalized.lower()
            if marker in seen:
                continue
            seen.add(marker)
            deduped.append(normalized)
            if len(deduped) >= self.cluster_hint_limit:
                break
        return deduped

    @staticmethod
    def _mark_signal_usage(families: set[str], fields: set[str], signal_fields: list[str]) -> None:
        fields.update(signal_fields)
        families.update(field.split(".", 1)[0] for field in signal_fields)

    def _score_signal_fields(self, signal_name: str) -> list[str]:
        return {
            "domain": ["identity.domain"],
            "keywords": [
                "content.title_candidates",
                "content.preferred_title",
                "content.preferred_description",
                "content.keywords_text",
                "content.language",
                "structure.site_name",
                "structure.brand_terms",
            ],
            "title": [
                "content.title_candidates",
                "structure.headings_h1",
                "structure.site_name",
            ],
            "content": [
                "content.semantic_text",
                "content.preferred_description",
                "content.main_text",
                "structure.page_type_hints",
                "structure.site_type_candidates",
                "structure.schema_types",
            ],
            "folder": ["context_time.original_folder_path"],
        }.get(signal_name, [])

    def _resource_signal_fields(self, signal_name: str) -> list[str]:
        return {
            "structured_facet": ["structure.resource_facets"],
            "domain": ["identity.domain"],
            "url": ["identity.path_segments"],
            "keyword": ["content.semantic_text", "content.keywords_text"],
            "title": ["content.title_candidates", "structure.headings_h1"],
            "extension": ["identity.path_segments"],
        }.get(signal_name, [])

    def _dynamic_source_fields(self, source_name: str) -> list[str]:
        return {
            "site_profile": [
                "structure.site_name",
                "structure.brand_terms",
                "structure.site_type_candidates",
                "structure.page_type_hints",
                "structure.schema_types",
            ],
            "title": ["content.title_candidates"],
            "name": ["identity.saved_title"],
            "keywords": ["content.keywords_text"],
            "url_path": ["identity.path_segments"],
            "domain": ["identity.domain", "identity.registrable_domain"],
        }.get(source_name, [])

    def _collect_signal_usage(
        self,
        topic_scores: list[dict[str, Any]],
        resource_type_evidence: list[dict[str, Any]],
        dynamic_candidates: list[dict[str, Any]],
        quality_signals: list[str],
        confirmation_bucket: str,
        review_required: bool,
        link_health: dict[str, Any],
    ) -> tuple[list[str], list[str]]:
        families: set[str] = set()
        fields: set[str] = set()
        for item in topic_scores[:2]:
            for evidence in item.get("evidence", []):
                self._mark_signal_usage(families, fields, self._score_signal_fields(str(evidence.get("signal") or "")))
        for evidence in resource_type_evidence:
            self._mark_signal_usage(families, fields, self._resource_signal_fields(str(evidence.get("signal") or "")))
        for candidate in dynamic_candidates[:3]:
            for source in candidate.get("sources", []):
                self._mark_signal_usage(families, fields, self._dynamic_source_fields(str(source)))
        if quality_signals:
            self._mark_signal_usage(
                families,
                fields,
                [
                    "structure.resource_facets",
                ],
            )
        if confirmation_bucket in {"fetch_blocked", "low_confidence", "rule_gap"} or review_required:
            self._mark_signal_usage(
                families,
                fields,
                [
                    "health_access.fetch_status",
                    "health_access.link_health",
                    "health_access.review_required",
                    "health_access.trusted_override",
                    "health_access.fetch_context",
                ],
            )
        if link_health.get("trusted_override"):
            self._mark_signal_usage(families, fields, ["health_access.trusted_override"])
        return sorted(families), sorted(fields)

    def _build_top_decision_drivers(
        self,
        rule_candidates: list[dict[str, Any]],
        resource_type: str,
        resource_type_evidence: list[dict[str, Any]],
        dynamic_candidates: list[dict[str, Any]],
        review_required: bool,
        confirmation_bucket: str,
    ) -> list[dict[str, Any]]:
        drivers: list[dict[str, Any]] = []
        if rule_candidates:
            top_rule = rule_candidates[0]
            signal_fields = sorted(
                {
                    field
                    for evidence in top_rule.get("evidence", [])
                    for field in self._score_signal_fields(str(evidence.get("signal") or ""))
                }
            )
            drivers.append(
                {
                    "driver": "rule_category",
                    "summary": f"{top_rule['category']} ({top_rule['total']})",
                    "signal_fields": signal_fields,
                }
            )
        if resource_type_evidence:
            signal_fields = sorted(
                {
                    field
                    for evidence in resource_type_evidence
                    for field in self._resource_signal_fields(str(evidence.get("signal") or ""))
                }
            )
            drivers.append(
                {
                    "driver": "resource_type",
                    "summary": resource_type,
                    "signal_fields": signal_fields,
                }
            )
        if dynamic_candidates:
            signal_fields = sorted(
                {
                    field
                    for source in dynamic_candidates[0].get("sources", [])
                    for field in self._dynamic_source_fields(str(source))
                }
            )
            drivers.append(
                {
                    "driver": "open_topic_candidate",
                    "summary": dynamic_candidates[0]["topic"],
                    "signal_fields": signal_fields,
                }
            )
        if review_required:
            drivers.append(
                {
                    "driver": "review_gate",
                    "summary": confirmation_bucket,
                    "signal_fields": [
                        "health_access.fetch_status",
                        "health_access.link_health",
                        "health_access.review_required",
                    ],
                }
            )
        return drivers[:4]

    def classify_bookmark(self, bookmark: dict) -> dict[str, Any]:
        signal_pack = bookmark.get("signal_pack") or build_signal_pack(bookmark)
        signal_sections = signal_pack_sections(signal_pack)
        link_health = signal_sections["health_access"].get("link_health", {})
        topic_scores = []
        for category_name, category_rules in self.categories.items():
            scored = self._score_category(bookmark, category_name, category_rules)
            if scored:
                topic_scores.append(scored)
        topic_scores.sort(key=lambda item: item["total"], reverse=True)

        rule_candidates = self._build_rule_candidates(topic_scores)
        rule_roots = self._build_rule_roots(topic_scores)
        confident_topics = [
            item
            for item in topic_scores
            if item["total"] >= self.scoring["min_score"]
            and any(evidence.get("signal") != "folder" for evidence in item.get("evidence", []))
        ]
        primary_topics = [item["topic"] for item in confident_topics[:2]]
        secondary_topics = [item["topic"] for item in confident_topics[2:5]]
        fallback_category = primary_topics[0] if primary_topics else self.default_category
        folder_alignment_score = self._folder_alignment_score(topic_scores)
        resource_type, resource_type_evidence = self._infer_resource_type(bookmark)
        intent_labels = self._infer_intent_labels(bookmark)
        topic_labels = sorted(set(primary_topics + secondary_topics))
        dynamic_candidates = self._extract_dynamic_topic_candidates(bookmark, topic_labels)
        cluster_hints = self._extract_cluster_hints(bookmark, topic_scores, dynamic_candidates)
        quality_signals = self._infer_quality_signals(bookmark, topic_scores, resource_type)
        top_score = topic_scores[0]["total"] if topic_scores else 0.0
        runner_up_score = topic_scores[1]["total"] if len(topic_scores) > 1 else 0.0
        raw_rule_confidence = self._rule_confidence(topic_scores)
        rule_confidence = raw_rule_confidence
        review_required = bool(link_health.get("review_required"))
        review_penalty_applied = False
        if review_required and not link_health.get("trusted_override"):
            rule_confidence = min(rule_confidence, 0.45)
            review_penalty_applied = rule_confidence != raw_rule_confidence
            quality_signals = sorted(set(quality_signals + ["待审阅"]))
        auto_assign_confidence = float(self.scoring.get("auto_assign_confidence", 0.55))
        if rule_confidence < auto_assign_confidence:
            fallback_category = self.default_category
            primary_topics = []
            secondary_topics = []
            topic_labels = []
        fetch_status = str(signal_sections["health_access"].get("fetch_status") or "")
        needs_confirmation = (
            fallback_category == self.default_category
            or top_score < self.scoring["min_score"]
            or rule_confidence < auto_assign_confidence
        )
        needs_confirmation_reasons = []
        if review_required and not link_health.get("trusted_override"):
            needs_confirmation_reasons.append("fetch_review_required")
        if fallback_category == self.default_category:
            needs_confirmation_reasons.append("default_category_fallback")
        if top_score < self.scoring["min_score"]:
            needs_confirmation_reasons.append("score_below_min")
        if rule_confidence < auto_assign_confidence:
            needs_confirmation_reasons.append("low_rule_confidence")
        if dynamic_candidates:
            needs_confirmation_reasons.append("open_topic_candidates_present")
        if fetch_status and fetch_status != "success" and fallback_category == self.default_category:
            needs_confirmation_reasons.append("fetch_limited_signal")
        if (
            (fetch_status == "success" or not fetch_status)
            and not review_required
            and fallback_category == self.default_category
            and dynamic_candidates
        ):
            needs_confirmation_reasons.append("rule_coverage_gap_on_successful_fetch")
        if review_required and fetch_status != "success":
            confirmation_bucket = "fetch_blocked"
        elif (
            fallback_category == self.default_category
            and not review_required
            and (fetch_status == "success" or not fetch_status)
            and dynamic_candidates
        ):
            confirmation_bucket = "rule_gap"
        else:
            confirmation_bucket = "low_confidence"
        review_category = link_health.get("reason_label")
        review_reason_code = link_health.get("reason_code")
        used_signal_families, used_signal_fields = self._collect_signal_usage(
            topic_scores,
            resource_type_evidence,
            dynamic_candidates,
            quality_signals,
            confirmation_bucket,
            review_required,
            link_health,
        )
        top_decision_drivers = self._build_top_decision_drivers(
            rule_candidates,
            resource_type,
            resource_type_evidence,
            dynamic_candidates,
            review_required,
            confirmation_bucket,
        )
        confidence_components = {
            "top_score": round(top_score, 2),
            "runner_up_score": round(runner_up_score, 2),
            "score_margin": round(max(top_score - runner_up_score, 0.0), 2),
            "confirm_threshold": float(self.scoring.get("confirm_threshold", 25)),
            "auto_assign_confidence": auto_assign_confidence,
            "raw_rule_confidence": raw_rule_confidence,
            "final_rule_confidence": rule_confidence,
            "strong_rule_evidence": bool(topic_scores and self._is_strong_rule_evidence(topic_scores[0])),
            "review_penalty_applied": review_penalty_applied,
        }
        classification_evidence = {
            "topic_scores": topic_scores[:8],
            "resource_type": resource_type_evidence,
            "dynamic_topic_candidates": dynamic_candidates,
            "folder_alignment_score": folder_alignment_score,
            "link_health": link_health,
            "rule_candidates": rule_candidates,
            "rule_roots": rule_roots,
            "original_folder_path": bookmark.get("original_folder_path", []),
            "signal_pack": {
                "schema_version": signal_pack.get("schema_version"),
                "families": {
                    "identity": {
                        "domain": signal_sections["identity"].get("domain"),
                        "registrable_domain": signal_sections["identity"].get("registrable_domain"),
                        "canonical_identity": signal_sections["identity"].get("canonical_identity"),
                    },
                    "content": {
                        "preferred_title": signal_sections["content"].get("preferred_title"),
                        "preferred_description": signal_sections["content"].get("preferred_description"),
                        "keywords_text": signal_sections["content"].get("keywords_text"),
                    },
                    "structure": {
                        "resource_facets": signal_sections["structure"].get("resource_facets", []),
                        "site_name": signal_sections["structure"].get("site_name"),
                        "page_type_hints": signal_sections["structure"].get("page_type_hints", []),
                        "site_type_candidates": signal_sections["structure"].get("site_type_candidates", []),
                    },
                    "health_access": {
                        "fetch_status": signal_sections["health_access"].get("fetch_status"),
                        "review_required": signal_sections["health_access"].get("review_required"),
                    },
                    "context_time": {
                        "time_bucket": signal_sections["context_time"].get("time_bucket", {}),
                    },
                },
                "used_families": used_signal_families,
                "used_fields": used_signal_fields,
            },
            "top_decision_drivers": top_decision_drivers,
            "confidence_components": confidence_components,
        }

        return {
            "category": fallback_category,
            "display_category": fallback_category,
            "primary_topics": primary_topics or ([self.default_category] if not dynamic_candidates else []),
            "secondary_topics": secondary_topics,
            "topic_labels": topic_labels,
            "resource_type": resource_type,
            "intent_labels": intent_labels,
            "quality_signals": quality_signals,
            "open_topic_candidates": dynamic_candidates,
            "rule_candidates": rule_candidates,
            "rule_roots": rule_roots,
            "rule_confidence": rule_confidence,
            "cluster_hints": cluster_hints,
            "used_signal_families": used_signal_families,
            "used_signal_fields": used_signal_fields,
            "top_decision_drivers": top_decision_drivers,
            "confidence_components": confidence_components,
            "classification_evidence": classification_evidence,
            "score": round(top_score, 2),
            "needs_confirmation": needs_confirmation,
            "needs_confirmation_reasons": needs_confirmation_reasons,
            "confirmation_bucket": confirmation_bucket,
            "review_required": review_required,
            "review_category": review_category,
            "review_reason_code": review_reason_code,
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
        folder_only_count = 0
        low_confidence_normal_category_count = 0
        confirmation_reason_counts = Counter()
        confirmation_bucket_counts = Counter()
        tidy_breakdown = Counter()
        used_signal_family_counts = Counter()
        used_signal_field_counts = Counter()

        for bookmark in bookmarks:
            classified = bookmark.copy()
            classified["signal_pack"] = build_signal_pack(classified)
            classification = self.classify_bookmark(classified)
            classified["classification"] = classification
            results.append(classified)

            topic_key = classification["category"]
            topic_stats[topic_key] += 1
            resource_type_stats[classification["resource_type"]] += 1
            for candidate in classification["open_topic_candidates"][:3]:
                uncovered_topic_candidates[candidate["topic"]] += candidate["score"]
            top_scores = classification.get("classification_evidence", {}).get("topic_scores", [])
            if top_scores and all(
                evidence.get("signal") == "folder"
                for evidence in top_scores[0].get("evidence", [])
            ):
                folder_only_count += 1
            if (
                classification["category"] != self.default_category
                and classification.get("rule_confidence", 0.0) < float(self.scoring.get("auto_assign_confidence", 0.55))
            ):
                low_confidence_normal_category_count += 1
            used_signal_family_counts.update(classification.get("used_signal_families", []))
            used_signal_field_counts.update(classification.get("used_signal_fields", []))
            if classification["category"] == self.default_category:
                fetch_status = str(classified.get("signal_pack", {}).get("health_access", {}).get("fetch_status") or "unknown")
                tidy_breakdown[f"{fetch_status}|review={classification.get('review_required', False)}"] += 1
            if classification["needs_confirmation"]:
                confirmation_reason_counts.update(classification.get("needs_confirmation_reasons", []))
                confirmation_bucket_counts[classification.get("confirmation_bucket") or "unknown"] += 1
                low_confidence_items.append(
                    {
                        "id": bookmark.get("id"),
                        "name": bookmark.get("name"),
                        "url": bookmark.get("url"),
                        "score": classification["score"],
                        "fetch_status": classified.get("metadata", {}).get("fetch_status"),
                        "confirmation_bucket": classification.get("confirmation_bucket"),
                        "needs_confirmation_reasons": classification.get("needs_confirmation_reasons", []),
                        "primary_topics": classification["primary_topics"],
                        "open_topic_candidates": classification["open_topic_candidates"][:3],
                    }
                )
                if (
                    classification["category"] != self.default_category
                    or classification["open_topic_candidates"]
                ):
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
            "folder_only_classification_count": folder_only_count,
            "low_confidence_normal_category_count": low_confidence_normal_category_count,
            "confirmation_reason_counts": dict(confirmation_reason_counts),
            "confirmation_bucket_counts": dict(confirmation_bucket_counts),
            "tidy_breakdown": dict(tidy_breakdown),
            "used_signal_family_counts": dict(used_signal_family_counts),
            "used_signal_field_counts": dict(used_signal_field_counts),
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
                "fetch_status": bm.get("metadata", {}).get("fetch_status"),
                "confirmation_bucket": bm["classification"].get("confirmation_bucket"),
                "needs_confirmation_reasons": bm["classification"].get("needs_confirmation_reasons", []),
                "open_topic_candidates": bm["classification"]["open_topic_candidates"][:3],
                "top_decision_drivers": bm["classification"].get("top_decision_drivers", []),
                "review_required": bm["classification"]["review_required"],
                "review_category": bm["classification"]["review_category"],
            }
            for bm in confirm_needed
        ],
    }
    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = build_parser("分类书签")
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--rules", type=Path, default=None)
    parser.add_argument("--rules-override", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=None, help="待确认报告输出路径")
    args = parser.parse_args()

    config = load_config_from_args(args)
    logger = configure_logging(config, args.log_level)
    input_file = args.input or config.paths.enriched_file
    rules_file = args.rules or config.paths.rules_file
    rules_override_file = args.rules_override or config.paths.rules_override_file
    output_file = args.output or config.paths.classified_file
    report_file = args.report or config.paths.confirmation_report_file

    if not input_file.exists():
        print(f"错误: 输入文件不存在: {input_file}")
        return 1
    if not rules_file.exists():
        print(f"错误: 分类规则文件不存在: {rules_file}")
        return 1

    try:
        input_payload = require_payload_schema(
            json.loads(input_file.read_text(encoding="utf-8")),
            FETCH_OUTPUT_SCHEMA_VERSION,
            "步骤4输入",
            input_file,
        )
    except ValueError as exc:
        print(f"错误: {exc}")
        return 1

    bookmarks = input_payload["bookmarks"]
    classifier = BookmarkClassifier(rules_file, config.classification_options, rules_override_file)
    classified_bookmarks, stats, confirm_needed = classifier.classify_all(bookmarks)

    output = {
        "schema_version": CLASSIFIED_OUTPUT_SCHEMA_VERSION,
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

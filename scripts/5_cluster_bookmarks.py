#!/usr/bin/env python3
"""步骤5: 聚类分析与层级构建。"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence
from urllib.parse import urlparse

from common import (
    CLASSIFIED_OUTPUT_SCHEMA_VERSION,
    CLUSTERING_OUTPUT_SCHEMA_VERSION,
    DEFAULT_GENERIC_PLATFORM_DOMAINS,
    GENERIC_PLATFORM_TOKENS,
    SIGNAL_AUDIT_SCHEMA_VERSION,
    SOURCE_LIKE_TOPIC_TOKENS,
    build_parser,
    build_signal_pack,
    configure_logging,
    ensure_parent,
    flatten_signal_pack,
    is_noisy_topic_token,
    is_weak_topic_token,
    is_source_like_topic_token,
    is_generic_platform_domain,
    load_config_from_args,
    normalize_topic_token,
    require_payload_schema,
    signal_pack_sections,
)


def bookmark_cluster_text(bookmark: dict) -> str:
    signal_pack = bookmark.get("signal_pack") or build_signal_pack(bookmark)
    sections = signal_pack_sections(signal_pack)
    content = sections["content"]
    structure = sections["structure"]
    parts = [
        content.get("preferred_title", ""),
        content.get("preferred_description", ""),
        content.get("keywords_text", ""),
        " ".join(structure.get("page_type_hints") or []),
        structure.get("site_name", ""),
        " ".join(structure.get("brand_terms") or []),
        " ".join(structure.get("headings_h2") or []),
    ]
    return " ".join(part for part in parts if part)


STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "by",
    "from", "as", "is", "was", "are", "were", "been", "this", "that", "how", "what", "why",
    "www", "http", "https", "com", "org", "net", "io", "cn", "blog", "docs", "doc", "home",
    "page", "index", "guide", "tutorial", "official", "的", "了", "和", "是", "在", "有", "个",
    "我", "他", "她", "它", "教程", "指南", "官网", "文档", "文章", "页面",
}
RESOURCE_TYPE_ALIASES = {
    "documentation": "文档",
    "doc": "文档",
    "docs": "文档",
    "blog": "博客",
    "tool": "工具",
    "repository": "仓库",
    "repo": "仓库",
    "paper": "论文",
}
URL_TYPE_HINTS = {
    "docs": "文档",
    "documentation": "文档",
    "blog": "博客",
    "posts": "博客",
    "article": "博客",
    "articles": "博客",
    "tool": "工具",
    "tools": "工具",
    "repo": "仓库",
    "repos": "仓库",
    "github": "仓库",
    "paper": "论文",
    "papers": "论文",
    "arxiv": "论文",
}
RESOURCE_TYPE_PRIORITY = [
    "文档",
    "教程",
    "论文",
    "仓库",
    "工具",
    "产品",
    "博客",
    "阅读资料",
    "教育课程",
    "组织",
    "未知",
]
GENERIC_FOLDER_NAMES = {
    "其他", "未分类", "学习", "收藏", "书签", "资料", "文档", "教程",
    "docs", "doc", "documentation", "blog", "blogs", "article", "articles",
    "misc", "general", "home", "index", "www",
}
DOMAIN_DISPLAY_NAMES = {
    "zhihu.com": "知乎",
    "csdn.net": "CSDN",
    "github.com": "GitHub",
    "gitbook.com": "GitBook",
    "gitbook.io": "GitBook",
}
DISPLAY_ORDER_FALLBACK = 10**6
DISPLAY_TOKEN_OVERRIDES = {
    "api": "API",
    "aws": "AWS",
    "css": "CSS",
    "html": "HTML",
    "json": "JSON",
    "llm": "LLM",
    "github": "GitHub",
}
TIDY_BUCKET_DISPLAY_NAMES = {
    "fetch_blocked": "抓取受阻",
    "rule_gap": "规则缺口",
    "low_confidence": "低置信度",
}
TIDY_BUCKET_DISPLAY_ORDER = {
    "抓取受阻": 0,
    "规则缺口": 1,
    "低置信度": 2,
}


@dataclass
class BookmarkFeatures:
    topics: set[str]
    resource_types: set[str]
    domain_tokens: set[str]
    path_tokens: set[str]
    text_tokens: set[str]
    cluster_hints: set[str]
    page_type_hints: set[str]
    site_name_tokens: set[str]
    quality_signals: set[str]
    time_buckets: set[str]
    primary_topic: str
    resource_type: str
    domain: str
    registered_domain: str
    canonical_identity: str
    rule_roots: set[str]
    rule_root_weights: dict[str, float]
    rule_confidence: float
    signal_fields: set[str]
    signal_families: set[str]


class DisjointSet:
    def __init__(self, size: int):
        self.parent = list(range(size))
        self.rank = [0] * size

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: int, right: int) -> None:
        root_left = self.find(left)
        root_right = self.find(right)
        if root_left == root_right:
            return
        if self.rank[root_left] < self.rank[root_right]:
            root_left, root_right = root_right, root_left
        self.parent[root_right] = root_left
        if self.rank[root_left] == self.rank[root_right]:
            self.rank[root_left] += 1


class BookmarkClusterer:
    def __init__(
        self,
        min_cluster_size: int = 10,
        max_keywords: int = 3,
        max_depth: int = 3,
        merge_small_nodes_threshold: int | None = None,
        domain_split_min_size: int = 5,
        generic_platform_domains: set[str] | None = None,
    ):
        self.min_cluster_size = min_cluster_size
        self.max_keywords = max_keywords
        self.max_depth = max_depth
        self.merge_small_nodes_threshold = merge_small_nodes_threshold or max(2, min_cluster_size // 2)
        self.domain_split_min_size = domain_split_min_size
        self.generic_platform_domains = generic_platform_domains or set(DEFAULT_GENERIC_PLATFORM_DOMAINS)
        self.source_like_topic_tokens = set(SOURCE_LIKE_TOPIC_TOKENS)
        self._feature_cache: dict[tuple[str, str], BookmarkFeatures] = {}

    @staticmethod
    def _bookmark_identity(bookmark: dict) -> tuple[str, str]:
        return (
            str(bookmark.get("id", "")),
            str(bookmark.get("fetch_normalized_url") or bookmark.get("url", "")),
        )

    @staticmethod
    def _normalize_resource_type(value: str) -> str:
        normalized = value.strip().lower()
        return RESOURCE_TYPE_ALIASES.get(normalized, value.strip()) if normalized else "未知"

    @staticmethod
    def _choose_resource_type(candidates: set[str]) -> str:
        if not candidates:
            return "未知"
        priority = {name: index for index, name in enumerate(RESOURCE_TYPE_PRIORITY)}
        return sorted(candidates, key=lambda item: (priority.get(item, len(priority)), item))[0]

    @staticmethod
    def _registered_domain(domain: str) -> str:
        labels = [part for part in domain.lower().split(".") if part]
        if len(labels) <= 2:
            return domain.lower()
        if labels[-1] in {"cn", "uk", "jp", "au"} and len(labels) >= 3:
            return ".".join(labels[-3:])
        return ".".join(labels[-2:])

    @staticmethod
    def _normalized_label_key(value: str) -> str:
        return re.sub(r"[\s_/.-]+", "", (value or "").strip().lower())

    @staticmethod
    def _leading_title_segment(value: str) -> str:
        text = re.sub(r"\s+", " ", (value or "").strip())
        if not text:
            return ""
        segments = [segment.strip() for segment in re.split(r"\s*[|｜]\s*|\s+[—–-]\s+", text) if segment.strip()]
        return segments[0] if segments else text

    def _human_label(self, value: str) -> str:
        text = re.sub(r"\s+", " ", (value or "").strip())
        if not text:
            return ""
        segments = [segment.strip() for segment in re.split(r"\s*[|｜]\s*|\s+[—–-]\s+", text) if segment.strip()]
        if segments:
            text = segments[0]
        return text.strip(" /._-")

    @staticmethod
    def _is_domain_like(value: str) -> bool:
        return bool(re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", (value or "").strip().lower()))

    def _is_generic_label(self, value: str, category: str = "") -> bool:
        label = self._human_label(value)
        if not label:
            return True
        label_key = self._normalized_label_key(label)
        if label_key in {self._normalized_label_key(item) for item in GENERIC_FOLDER_NAMES}:
            return True
        if self._is_domain_like(label):
            return True
        if self._is_noisy_topic_label(label):
            return True
        category_keys = {self._normalized_label_key(part) for part in category.split("/") if part}
        return label_key in category_keys

    def _domain_display_name(self, domain: str) -> str:
        registered = self._registered_domain(domain or "")
        if not registered:
            return ""
        if registered in DOMAIN_DISPLAY_NAMES:
            return DOMAIN_DISPLAY_NAMES[registered]
        label = registered.split(".")[0]
        if not label:
            return registered
        if len(label) <= 4:
            return label.upper()
        return label.capitalize()

    def _is_generic_platform(self, domain: str) -> bool:
        return is_generic_platform_domain(domain, self.generic_platform_domains)

    @staticmethod
    def _is_source_like_topic(value: str) -> bool:
        normalized = (value or "").lower()
        return any(token in normalized for token in ("github", "gitlab", "gitee", "git/", "/git"))

    def _is_noisy_topic_label(self, value: str) -> bool:
        label = self._human_label(value)
        if not label:
            return False
        return is_noisy_topic_token(label, self.source_like_topic_tokens)

    @staticmethod
    def _is_weak_topic_label(value: str) -> bool:
        return is_weak_topic_token(value)

    def _category_leaf_label(self, category: str, root_category: str | None = None) -> str:
        parts = [part for part in category.split("/") if part]
        if root_category and parts and parts[0] == root_category:
            parts = parts[1:]
        if not parts:
            return ""
        cleaned_parts = [self._human_label(part) for part in parts if self._human_label(part)]
        if not cleaned_parts:
            return ""
        if len(cleaned_parts) == 1:
            return cleaned_parts[0]
        return " / ".join(cleaned_parts[-2:])

    def _folder_display_label(self, best_folder: Sequence[str], category: str) -> str:
        for part in reversed(best_folder):
            label = self._human_label(part)
            if not self._is_generic_label(label, category):
                return label
        return ""

    def _dominant_category_leaf(self, bookmarks: List[dict], category: str) -> str:
        counter: Counter[str] = Counter()
        root_category = category.split("/")[0] if category else ""
        for bookmark in bookmarks:
            confidence = float(bookmark.get("classification", {}).get("rule_confidence", 0.0) or 0.0)
            if confidence < 0.72:
                continue
            bookmark_category = bookmark.get("classification", {}).get("category", "")
            leaf = self._category_leaf_label(bookmark_category, root_category)
            label_context = root_category or category
            if leaf and not self._is_generic_label(leaf, label_context) and not self._is_weak_topic_label(leaf):
                counter[leaf] += 1
        return sorted(counter.items(), key=lambda item: (-item[1], item[0]))[0][0] if counter else ""

    def _generic_platform_share(self, bookmarks: List[dict]) -> float:
        if not bookmarks:
            return 0.0
        generic_count = 0
        for bookmark in bookmarks:
            feature = self.build_feature_set(bookmark)
            if self._is_generic_platform(feature.registered_domain or feature.domain):
                generic_count += 1
        return generic_count / len(bookmarks)

    def _generic_repo_label(self, bookmark: dict) -> str:
        feature = self.build_feature_set(bookmark)
        domain = feature.registered_domain or feature.domain
        if not self._is_generic_platform(domain) or not any(
            domain_matches in domain
            for domain_matches in ("github.com", "gitlab.com", "gitee.com", "bitbucket.org")
        ):
            return ""
        parsed = urlparse(bookmark.get("url", ""))
        segments = [segment for segment in parsed.path.split("/") if segment]
        if len(segments) < 2:
            return ""
        repo = re.sub(r"\.git$", "", segments[1], flags=re.IGNORECASE).strip()
        label = self.clean_topic_token(repo)
        if not label or self._is_generic_label(label) or self._is_weak_topic_label(label):
            return ""
        return label

    def _dominant_project_label(self, bookmarks: List[dict], category: str = "") -> str:
        counter: Counter[str] = Counter()
        for bookmark in bookmarks:
            label = self._generic_repo_label(bookmark)
            if label and not self._is_generic_label(label, category):
                counter[label] += 1
        if not counter:
            return ""
        min_support = 1 if len(bookmarks) == 1 else 2
        for label, count in sorted(counter.items(), key=lambda item: (-item[1], item[0])):
            if count >= min_support:
                return label
        return ""

    def _dominant_site_label(self, bookmarks: List[dict], category: str) -> str:
        counter: Counter[str] = Counter()
        for bookmark in bookmarks:
            feature = self.build_feature_set(bookmark)
            if self._is_generic_platform(feature.registered_domain or feature.domain):
                continue
            signal_pack = bookmark.get("signal_pack") or build_signal_pack(bookmark)
            structure = signal_pack_sections(signal_pack)["structure"]
            candidates = [
                structure.get("site_name"),
                *list(structure.get("source_facets") or [])[:2],
            ]
            for candidate in candidates:
                label = self._human_label(candidate or "")
                if label and not self._is_generic_label(label, category):
                    counter[label] += 1
                    break
        return sorted(counter.items(), key=lambda item: (-item[1], item[0]))[0][0] if counter else ""

    def _dominant_domain_label(self, bookmarks: List[dict]) -> str:
        counter: Counter[str] = Counter()
        for bookmark in bookmarks:
            domain = self.build_feature_set(bookmark).registered_domain
            if self._is_generic_platform(domain):
                continue
            label = self._domain_display_name(domain)
            if label:
                counter[label] += 1
        return sorted(counter.items(), key=lambda item: (-item[1], item[0]))[0][0] if counter else ""

    def _dominant_hint_label(self, bookmarks: List[dict], category: str = "") -> str:
        counter: Counter[str] = Counter()
        for bookmark in bookmarks:
            feature = self.build_feature_set(bookmark)
            generic_platform = self._is_generic_platform(feature.registered_domain or feature.domain)
            classification = bookmark.get("classification", {})
            for candidate in classification.get("cluster_hints", [])[:4]:
                label = self._human_label(candidate or "")
                if generic_platform and (
                    self._normalized_label_key(label) in GENERIC_PLATFORM_TOKENS
                    or self._is_source_like_topic(label)
                ):
                    continue
                if self._is_noisy_topic_label(label):
                    continue
                if label and not self._is_generic_label(label, category):
                    counter[label] += 1
        for label, count in sorted(
            counter.items(),
            key=lambda item: (
                -item[1],
                self._is_weak_topic_label(item[0]),
                -len(normalize_topic_token(item[0])),
                item[0],
            ),
        ):
            if self._is_weak_topic_label(label) and count < 2:
                continue
            return label
        return ""

    def _cluster_display_name(
        self,
        bookmarks: List[dict],
        category: str,
        best_folder: Sequence[str] | None = None,
        folder_quality: float = 0.0,
    ) -> str:
        generic_platform_share = self._generic_platform_share(bookmarks)
        leaf_label_context = category.split("/")[0] if category else category
        if generic_platform_share >= 0.45:
            for candidate in (
                self._dominant_project_label(bookmarks, leaf_label_context),
                self._dominant_hint_label(bookmarks, leaf_label_context),
                self._dominant_category_leaf(bookmarks, category),
                self._dominant_domain_label(bookmarks),
            ):
                if candidate and not self._is_generic_label(candidate, leaf_label_context):
                    return candidate
        else:
            for candidate in (
                self._dominant_hint_label(bookmarks, category),
            ):
                if candidate and not self._is_generic_label(candidate, category):
                    return candidate

        for token in self._representative_tokens(bookmarks, limit=4):
            candidate = self.clean_topic_token(token)
            if candidate and not self._is_generic_label(candidate, category) and not (
                generic_platform_share >= 0.45 and self._is_weak_topic_label(candidate)
            ):
                return candidate

        for candidate in (
            self._dominant_category_leaf(bookmarks, category),
            self._dominant_site_label(bookmarks, category),
            self._dominant_domain_label(bookmarks),
        ):
            if candidate and not self._is_generic_label(candidate, leaf_label_context):
                return candidate

        return "其他"

    def extract_keywords(self, text: str) -> List[str]:
        normalized = text.lower().replace("-", " ").replace("_", " ")
        normalized = re.sub(r"[^\w\s\u4e00-\u9fff]", " ", normalized)
        return [
            word
            for word in normalized.split()
            if word not in STOPWORDS
            and len(word) > 1
            and not is_noisy_topic_token(word, self.source_like_topic_tokens)
        ]

    def _tokenize_path(self, url: str) -> tuple[set[str], set[str]]:
        parsed = urlparse(url)
        path_tokens = set(self.extract_keywords(parsed.path.replace("/", " ")))
        hint_tokens = {URL_TYPE_HINTS[token] for token in path_tokens if token in URL_TYPE_HINTS}
        return path_tokens, hint_tokens

    def _collect_topics(self, bookmark: dict, title_tokens: Iterable[str], path_tokens: Iterable[str]) -> tuple[set[str], str]:
        classification = bookmark.get("classification", {})
        topics = set()
        category = classification.get("category", "")
        rule_confidence = float(classification.get("rule_confidence", 0.0) or 0.0)
        cluster_hints = classification.get("cluster_hints", []) or []
        rule_candidates = classification.get("rule_candidates", []) or []
        open_candidates = classification.get("open_topic_candidates", []) or []

        if cluster_hints:
            topics.update(self.extract_keywords(" ".join(cluster_hints)))

        if category and rule_confidence >= 0.75:
            topics.update(self.extract_keywords(category.replace("/", " ")))

        all_scores = classification.get("all_scores", {})
        top_scores = sorted(all_scores.items(), key=lambda item: (-item[1].get("total", 0), item[0]))[:3]
        if rule_confidence >= 0.6:
            for candidate in rule_candidates[:3]:
                topics.update(self.extract_keywords(candidate.get("category", "").replace("/", " ")))
                topics.update(self.extract_keywords(candidate.get("leaf", "")))
            for category_name, _ in top_scores:
                topics.update(self.extract_keywords(category_name.replace("/", " ")))

        for candidate in open_candidates[:4]:
            topics.update(self.extract_keywords(candidate.get("topic", "")))

        candidates = sorted(title_tokens) + sorted(path_tokens)
        topical_tokens = [token for token in candidates if token not in URL_TYPE_HINTS and token not in STOPWORDS]
        if topical_tokens:
            topical_counter = Counter(topical_tokens)
            stable_topical_tokens = sorted(topical_counter.items(), key=lambda item: (-item[1], item[0]))[:4]
            topics.update(token for token, _ in stable_topical_tokens)

        if rule_confidence >= 0.75 and rule_candidates:
            primary = rule_candidates[0]["category"]
        elif open_candidates:
            primary = open_candidates[0]["topic"]
        elif category and category not in {"待整理", "其他/未分类"}:
            primary = category
        elif cluster_hints:
            primary = cluster_hints[0]
        elif top_scores:
            primary = top_scores[0][0]
        else:
            primary = "其他/未分类"
        return topics, primary or "其他/未分类"

    def build_feature_set(self, bookmark: dict) -> BookmarkFeatures:
        cache_key = self._bookmark_identity(bookmark)
        cached = self._feature_cache.get(cache_key)
        if cached is not None:
            return cached

        classification = bookmark.get("classification", {})
        signal_pack = bookmark.get("signal_pack") or build_signal_pack(bookmark)
        signal_sections = signal_pack_sections(signal_pack)
        identity = signal_sections["identity"]
        content = signal_sections["content"]
        structure = signal_sections["structure"]
        health_access = signal_sections["health_access"]
        context_time = signal_sections["context_time"]
        domain = str(identity.get("domain") or bookmark.get("domain") or urlparse(bookmark.get("url", "")).netloc).lower()
        registered_domain = self._registered_domain(domain) if domain else ""
        generic_platform = self._is_generic_platform(registered_domain)
        path_tokens, page_type_hints = self._tokenize_path(bookmark.get("url", ""))
        preferred_title = str(content.get("preferred_title", "") or "")
        title_candidates = [" ".join(content.get("title_candidates", []) or [])]
        preferred_description = str(content.get("preferred_description", "") or "")
        headings_h1 = " ".join(structure.get("headings_h1") or [])
        keywords_text = str(content.get("keywords_text", "") or "")
        if generic_platform:
            preferred_title = self._leading_title_segment(preferred_title)
            title_candidates = [self._leading_title_segment(value) for value in title_candidates]
        title_tokens = set(self.extract_keywords(" ".join([
            preferred_title,
            *title_candidates,
            preferred_description,
            headings_h1,
            keywords_text,
        ])))
        if generic_platform:
            title_tokens.difference_update(GENERIC_PLATFORM_TOKENS)
        if generic_platform:
            site_name_tokens = set()
        else:
            site_name_tokens = set(
                self.extract_keywords(
                    " ".join(
                        part
                        for part in (
                            domain.replace(".", " "),
                            str(structure.get("site_name", "") or ""),
                            " ".join(structure.get("brand_terms") or []),
                            " ".join(structure.get("source_facets") or []),
                        )
                        if part
                    )
                )
            )
        topics, primary_topic = self._collect_topics(bookmark, title_tokens, path_tokens)
        cluster_hint_tokens = set(self.extract_keywords(" ".join(classification.get("cluster_hints", []) or [])))
        if generic_platform:
            topics.difference_update(GENERIC_PLATFORM_TOKENS)
            cluster_hint_tokens.difference_update(GENERIC_PLATFORM_TOKENS)
            if self._is_source_like_topic(primary_topic):
                topics.difference_update(self.extract_keywords(primary_topic.replace("/", " ")))

        resource_type_candidates = set()
        explicit_type = classification.get("resource_type") or bookmark.get("resource_type")
        if explicit_type:
            resource_type_candidates.add(self._normalize_resource_type(explicit_type))
        resource_type_candidates.update(self._normalize_resource_type(facet) for facet in structure.get("resource_facets", []) or [])
        resource_type_candidates.update(page_type_hints)
        if not resource_type_candidates:
            text = str(content.get("semantic_text", "") or "").lower()
            if "github" in domain or any(token in path_tokens for token in {"repo", "repos"}):
                resource_type_candidates.add("仓库")
            elif any(token in path_tokens for token in {"docs", "documentation"}):
                resource_type_candidates.add("文档")
            elif any(token in path_tokens for token in {"blog", "posts", "article", "articles"}):
                resource_type_candidates.add("博客")
            elif any(token in text for token in {"paper", "arxiv", "论文"}):
                resource_type_candidates.add("论文")
            elif any(token in text for token in {"tool", "tools", "playground", "generator"}):
                resource_type_candidates.add("工具")
        if not resource_type_candidates:
            resource_type_candidates.add("未知")

        text_tokens = title_tokens | set(self.extract_keywords(str(content.get("semantic_text", "") or "")))
        if self._is_generic_platform(registered_domain):
            text_tokens.difference_update(GENERIC_PLATFORM_TOKENS)
        domain_tokens = set(self.extract_keywords(domain.replace(".", " ")))
        if self._is_generic_platform(registered_domain):
            domain_tokens.difference_update(GENERIC_PLATFORM_TOKENS)
        quality_signals = {
            self._human_label(str(signal))
            for signal in (classification.get("quality_signals") or [])
            if self._human_label(str(signal))
        }
        quality_signals.update(
            self._human_label(str(signal))
            for signal in health_access.get("quality_facets", [])
            if self._human_label(str(signal))
        )
        time_buckets = {
            value
            for value in (context_time.get("time_bucket") or {}).values()
            if value
        }
        rule_root_weights: dict[str, float] = {}
        for item in classification.get("rule_roots", []) or []:
            root = str(item.get("root", "")).strip()
            if not root:
                continue
            support = float(item.get("support", 0.0) or 0.0)
            total = float(item.get("total", 0.0) or 0.0)
            weight = support if support > 0 else total
            rule_root_weights[root] = max(rule_root_weights.get(root, 0.0), weight)
        signal_fields = {
            "identity.domain",
            "identity.registrable_domain",
            "identity.path_segments",
            "identity.canonical_identity",
            "content.preferred_title",
            "content.title_candidates",
            "content.preferred_description",
            "content.semantic_text",
            "content.keywords_text",
            "structure.resource_facets",
            "structure.source_facets",
            "structure.site_name",
            "structure.brand_terms",
            "structure.page_type_hints",
            "health_access.quality_facets",
            "context_time.time_bucket",
        }
        if structure.get("site_type_candidates"):
            signal_fields.add("structure.site_type_candidates")
        if structure.get("schema_types"):
            signal_fields.add("structure.schema_types")
        if structure.get("headings_h2"):
            signal_fields.add("structure.headings_h2")
        if health_access.get("review_required") or health_access.get("trusted_override"):
            signal_fields.add("health_access.review_required")
        if health_access.get("fetch_context"):
            signal_fields.add("health_access.fetch_context")

        feature = BookmarkFeatures(
            topics=topics,
            resource_types=resource_type_candidates,
            domain_tokens=domain_tokens,
            path_tokens=path_tokens,
            text_tokens=text_tokens,
            cluster_hints=cluster_hint_tokens,
            page_type_hints=page_type_hints,
            site_name_tokens=site_name_tokens,
            quality_signals=quality_signals,
            time_buckets=time_buckets,
            primary_topic=primary_topic,
            resource_type=self._choose_resource_type(resource_type_candidates),
            domain=domain,
            registered_domain=registered_domain,
            canonical_identity=str(identity.get("canonical_identity", "") or bookmark.get("fetch_normalized_url") or bookmark.get("url", "")),
            rule_roots=set(rule_root_weights),
            rule_root_weights=rule_root_weights,
            rule_confidence=float(classification.get("rule_confidence", 0.0) or 0.0),
            signal_fields=signal_fields,
            signal_families={field.split(".", 1)[0] for field in signal_fields},
        )
        self._feature_cache[cache_key] = feature
        return feature

    @staticmethod
    def _jaccard(left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        return len(left & right) / len(left | right)

    def similarity_breakdown(self, left: BookmarkFeatures, right: BookmarkFeatures) -> dict:
        topic_overlap = self._jaccard(left.topics, right.topics)
        resource_type_match = 1.0 if left.resource_types & right.resource_types else 0.0
        same_domain = 1.0 if left.domain and left.domain == right.domain else 0.0
        same_registered_domain = 1.0 if left.registered_domain and left.registered_domain == right.registered_domain else 0.0
        same_canonical = 1.0 if left.canonical_identity and left.canonical_identity == right.canonical_identity else 0.0
        generic_platform_match = bool(
            same_registered_domain
            and self._is_generic_platform(left.registered_domain)
        )
        domain_similarity = max(
            same_domain,
            0.85 * same_registered_domain,
            self._jaccard(left.site_name_tokens, right.site_name_tokens) * 0.7,
            self._jaccard(left.domain_tokens, right.domain_tokens) * 0.4,
        )
        text_similarity = max(
            self._jaccard(left.text_tokens | left.topics, right.text_tokens | right.topics),
            self._jaccard(left.path_tokens | left.text_tokens | left.cluster_hints, right.path_tokens | right.text_tokens | right.cluster_hints),
        )
        hint_similarity = max(
            self._jaccard(left.cluster_hints, right.cluster_hints),
            topic_overlap * 0.7,
        )
        if generic_platform_match:
            domain_similarity = min(
                domain_similarity,
                max(
                    self._jaccard(left.site_name_tokens, right.site_name_tokens) * 0.25,
                    self._jaccard(left.domain_tokens, right.domain_tokens) * 0.15,
                    same_canonical,
                ),
            )
        rule_candidate_overlap = 0.0
        all_roots = left.rule_roots | right.rule_roots
        if all_roots:
            overlap = sum(min(left.rule_root_weights.get(root, 0.0), right.rule_root_weights.get(root, 0.0)) for root in all_roots)
            union = sum(max(left.rule_root_weights.get(root, 0.0), right.rule_root_weights.get(root, 0.0)) for root in all_roots)
            rule_candidate_overlap = overlap / union if union else 0.0
        quality_signal_bonus = self._jaccard(left.quality_signals, right.quality_signals)
        if left.quality_signals & right.quality_signals:
            quality_signal_bonus = max(
                quality_signal_bonus,
                0.7 if {"官方", "社区"} & (left.quality_signals & right.quality_signals) else 0.5,
            )
        cross_domain_primary_bonus = 0.0
        if (
            left.registered_domain
            and right.registered_domain
            and left.registered_domain != right.registered_domain
            and left.primary_topic
            and left.primary_topic == right.primary_topic
            and topic_overlap >= 0.25
        ):
            cross_domain_primary_bonus = 1.0
        conflict_penalty = 0.0
        if (
            left.rule_confidence >= 0.8
            and right.rule_confidence >= 0.8
            and not (left.rule_roots & right.rule_roots)
            and hint_similarity < 0.15
        ):
            conflict_penalty = 0.28
        strong_topic_conflict = (
            left.rule_confidence >= 0.72
            and right.rule_confidence >= 0.72
            and left.primary_topic
            and right.primary_topic
            and left.primary_topic != right.primary_topic
            and not _is_default_or_generic_category(left.primary_topic)
            and not _is_default_or_generic_category(right.primary_topic)
        )
        if generic_platform_match and strong_topic_conflict and topic_overlap < 0.35 and hint_similarity < 0.35:
            conflict_penalty = max(conflict_penalty, 0.34)
        if (
            same_registered_domain
            and not resource_type_match
            and text_similarity < 0.25
            and hint_similarity < 0.15
        ):
            conflict_penalty = max(conflict_penalty, 0.18)
        if (
            generic_platform_match
            and same_canonical == 0.0
            and topic_overlap < 0.2
            and text_similarity < 0.28
            and hint_similarity < 0.25
        ):
            conflict_penalty = max(conflict_penalty, 0.3)
        time_similarity = self._jaccard(left.time_buckets, right.time_buckets)
        return {
            "topic_overlap": topic_overlap,
            "resource_type_match": resource_type_match,
            "domain_similarity": domain_similarity,
            "text_similarity": text_similarity,
            "hint_similarity": hint_similarity,
            "time_similarity": time_similarity,
            "same_canonical": same_canonical,
            "rule_candidate_overlap": rule_candidate_overlap,
            "quality_signal_bonus": quality_signal_bonus,
            "cross_domain_primary_bonus": cross_domain_primary_bonus,
            "conflict_penalty": conflict_penalty,
        }

    def similarity_score(self, left: BookmarkFeatures, right: BookmarkFeatures) -> float:
        metrics = self.similarity_breakdown(left, right)
        score = (
            metrics["text_similarity"] * 0.35
            + metrics["hint_similarity"] * 0.20
            + metrics["domain_similarity"] * 0.15
            + metrics["rule_candidate_overlap"] * 0.10
            + metrics["resource_type_match"] * 0.05
            + metrics["quality_signal_bonus"] * 0.10
            + metrics["time_similarity"] * 0.04
            + metrics["same_canonical"] * 0.25
            + metrics["cross_domain_primary_bonus"] * 0.20
            - metrics["conflict_penalty"]
        )
        if metrics["hint_similarity"] < 0.2 and metrics["text_similarity"] < 0.15:
            score *= 0.6
        return max(0.0, min(score, 1.0))

    @staticmethod
    def _feature_overlap_tokens(feature: BookmarkFeatures) -> set[str]:
        return feature.topics | feature.cluster_hints | feature.path_tokens

    def _comparison_bucket_keys(self, feature: BookmarkFeatures) -> set[str]:
        keys = set()
        generic_platform = self._is_generic_platform(feature.registered_domain)
        if feature.primary_topic and not (generic_platform and self._is_source_like_topic(feature.primary_topic)):
            keys.add(f"primary:{self.clean_topic_token(feature.primary_topic)}")
        if feature.registered_domain and not self._is_generic_platform(feature.registered_domain):
            keys.add(f"domain:{feature.registered_domain}")
        if feature.canonical_identity:
            keys.add(f"canonical:{feature.canonical_identity}")
        for token in sorted(feature.site_name_tokens)[:2]:
            keys.add(f"site:{token}")
        for token in sorted(feature.cluster_hints)[:4]:
            keys.add(f"hint:{token}")
        for root, weight in sorted(feature.rule_root_weights.items(), key=lambda item: (-item[1], item[0]))[:2]:
            if feature.rule_confidence >= 0.75 and weight > 0 and not (generic_platform and root in {"开发工具", "开源项目", "技术博客"}):
                keys.add(f"root:{root}:{feature.resource_type}")
        for token in sorted(self._feature_overlap_tokens(feature))[:6]:
            keys.add(f"token:{token}")
        return keys

    def _candidate_pairs(self, features: List[BookmarkFeatures]) -> set[tuple[int, int]]:
        if len(features) <= 1:
            return set()

        buckets: dict[str, list[int]] = defaultdict(list)
        for index, feature in enumerate(features):
            for key in self._comparison_bucket_keys(feature):
                buckets[key].append(index)

        candidate_pairs: set[tuple[int, int]] = set()
        for indices in buckets.values():
            if len(indices) < 2:
                continue
            for left_pos, left_index in enumerate(indices):
                for right_index in indices[left_pos + 1:]:
                    if left_index < right_index:
                        candidate_pairs.add((left_index, right_index))
                    else:
                        candidate_pairs.add((right_index, left_index))

        if candidate_pairs:
            return candidate_pairs
        return {
            (left, right)
            for left in range(len(features))
            for right in range(left + 1, len(features))
        }

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
            if key in STOPWORDS or key in seen or is_noisy_topic_token(part, self.source_like_topic_tokens):
                continue
            seen.add(key)
            parts.append(DISPLAY_TOKEN_OVERRIDES.get(key, part))
        return " ".join(parts) or "其他"

    def refresh_count(self, node: dict) -> dict:
        node["count"] = len(node.get("bookmarks", [])) + sum(child.get("count", 0) for child in node.get("children", []))
        return node

    def optimize_tree(self, node: dict, *, is_root: bool = False) -> dict:
        optimized_children = [self.optimize_tree(child) for child in node.get("children", []) if child.get("count", 0) > 0]
        merged_children: dict[str, dict] = {}
        for child in optimized_children:
            key = self.normalize_name(child["name"])
            existing = merged_children.get(key)
            if existing is None:
                merged_children[key] = child
                continue
            existing["bookmarks"].extend(child.get("bookmarks", []))
            existing["children"].extend(child.get("children", []))
            existing["node_type"] = existing["node_type"] if existing["node_type"] == child.get("node_type") else "mixed"
            existing["display_order"] = min(
                existing.get("display_order", DISPLAY_ORDER_FALLBACK),
                child.get("display_order", DISPLAY_ORDER_FALLBACK),
            )
            self.refresh_count(existing)

        node["children"] = sorted(
            merged_children.values(),
            key=lambda item: (
                item.get("display_order", DISPLAY_ORDER_FALLBACK),
                -item["count"],
                item["name"],
            ),
        )

        low_value_bookmarks = []
        retained_children = []
        if not node.get("preserve_children"):
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

        while len(node["children"]) == 1 and not node.get("bookmarks") and not is_root and not node.get("preserve_children"):
            only_child = node["children"][0]
            current_type = node.get("node_type", "mixed")
            child_type = only_child.get("node_type", current_type)
            if current_type == "reference" and child_type != "reference":
                break
            node["name"] = only_child["name"]
            node["node_type"] = child_type
            node["children"] = only_child.get("children", [])
            node["bookmarks"] = only_child.get("bookmarks", [])

        return self.refresh_count(node)

    def _normalize_hierarchy_node(self, node_name: str, payload: dict) -> dict:
        node = {
            **{key: value for key, value in payload.items() if key != "subcategories"},
            "name": payload.get("name", node_name),
            "bookmarks": list(payload.get("bookmarks") or []),
            "children": [
                self._normalize_hierarchy_node(child_name, child_payload)
                for child_name, child_payload in (payload.get("subcategories") or {}).items()
            ],
            "node_type": payload.get("node_type", "mixed"),
        }
        return self.refresh_count(node)

    def _denormalize_hierarchy_node(self, node: dict) -> dict:
        payload = {
            key: value
            for key, value in node.items()
            if key != "children"
        }
        payload["subcategories"] = {
            child.get("name", "未命名目录"): self._denormalize_hierarchy_node(child)
            for child in node.get("children", [])
        }
        payload["count"] = node.get("count", len(payload.get("bookmarks", [])))
        return payload

    def optimize_hierarchy_payload(self, node_name: str, payload: dict, *, is_root: bool = False) -> dict:
        normalized = self._normalize_hierarchy_node(node_name, payload)
        optimized = self.optimize_tree(normalized, is_root=is_root)
        optimized_payload = self._denormalize_hierarchy_node(optimized)
        optimized_payload["category"] = payload.get("category", node_name)
        return optimized_payload

    def cluster_by_keywords(self, bookmarks: List[dict]) -> Dict[str, List[dict]]:
        keyword_groups = defaultdict(list)
        for bookmark in bookmarks:
            text = " ".join([bookmark.get("name", ""), bookmark_cluster_text(bookmark)])
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

    def _connected_components(self, bookmarks: List[dict], features: List[BookmarkFeatures], threshold: float = 0.34) -> List[List[dict]]:
        if not bookmarks:
            return []
        dsu = DisjointSet(len(bookmarks))
        for left, right in sorted(self._candidate_pairs(features)):
            if self.similarity_score(features[left], features[right]) >= threshold:
                dsu.union(left, right)
        grouped: dict[int, list[dict]] = defaultdict(list)
        for index, bookmark in enumerate(bookmarks):
            grouped[dsu.find(index)].append(bookmark)
        return list(grouped.values())

    def _cluster_consistency(self, features: List[BookmarkFeatures]) -> float:
        if len(features) <= 1:
            return 1.0
        topic_purity = Counter(feature.primary_topic for feature in features).most_common(1)[0][1] / len(features)
        dominant_hint_candidates = [
            sorted(feature.cluster_hints)[0]
            for feature in features
            if feature.cluster_hints
        ]
        hint_purity = Counter(dominant_hint_candidates).most_common(1)[0][1] / len(features) if dominant_hint_candidates else 0.0
        dominant_rule_roots = [
            sorted(feature.rule_root_weights.items(), key=lambda item: (-item[1], item[0]))[0][0]
            for feature in features
            if feature.rule_root_weights
        ]
        rule_root_purity = Counter(dominant_rule_roots).most_common(1)[0][1] / len(features) if dominant_rule_roots else 0.0
        topic_purity = max(topic_purity, hint_purity, rule_root_purity)
        type_purity = Counter(feature.resource_type for feature in features).most_common(1)[0][1] / len(features)
        domain_concentration = Counter(feature.registered_domain for feature in features if feature.registered_domain).most_common(1)
        domain_score = domain_concentration[0][1] / len(features) if domain_concentration else 0.0
        mean_text_overlap = 0.0
        comparisons = 0
        for left, right in self._candidate_pairs(features):
            mean_text_overlap += self._jaccard(
                features[left].text_tokens | features[left].topics | features[left].cluster_hints,
                features[right].text_tokens | features[right].topics | features[right].cluster_hints,
            )
            comparisons += 1
        pairwise = mean_text_overlap / comparisons if comparisons else 1.0
        return topic_purity * 0.35 + type_purity * 0.2 + domain_score * 0.15 + pairwise * 0.3

    def _split_if_needed(self, clusters: List[List[dict]]) -> List[List[dict]]:
        refined = []
        for cluster in clusters:
            features = [self.build_feature_set(bookmark) for bookmark in cluster]
            consistency = self._cluster_consistency(features)
            if len(cluster) >= max(4, self.min_cluster_size) and consistency < 0.52:
                subclusters = self._connected_components(cluster, features, threshold=0.5)
                if 1 < len(subclusters) < len(cluster):
                    refined.extend(subclusters)
                    continue
            refined.append(cluster)
        return refined

    def _merge_if_needed(self, clusters: List[List[dict]]) -> List[List[dict]]:
        if len(clusters) <= 1:
            return clusters
        changed = True
        while changed:
            changed = False
            best_pair = None
            best_score = 0.0
            centroids = [self._summarize_cluster(cluster) for cluster in clusters]
            for left in range(len(clusters)):
                for right in range(left + 1, len(clusters)):
                    score = self._cluster_summary_similarity(centroids[left], centroids[right])
                    if score > best_score:
                        best_score = score
                        best_pair = (left, right)
            if best_pair and best_score >= 0.68:
                left, right = best_pair
                clusters[left] = clusters[left] + clusters[right]
                del clusters[right]
                changed = True
        return clusters

    def _summarize_cluster(self, cluster: List[dict]) -> BookmarkFeatures:
        features = [self.build_feature_set(bookmark) for bookmark in cluster]
        combined_topics = set().union(*(feature.topics for feature in features)) if features else set()
        combined_types = set().union(*(feature.resource_types for feature in features)) if features else {"未知"}
        combined_domain_tokens = set().union(*(feature.domain_tokens for feature in features)) if features else set()
        combined_path_tokens = set().union(*(feature.path_tokens for feature in features)) if features else set()
        combined_text_tokens = set().union(*(feature.text_tokens for feature in features)) if features else set()
        combined_cluster_hints = set().union(*(feature.cluster_hints for feature in features)) if features else set()
        combined_hints = set().union(*(feature.page_type_hints for feature in features)) if features else set()
        combined_site_name = set().union(*(feature.site_name_tokens for feature in features)) if features else set()
        combined_quality_signals = set().union(*(feature.quality_signals for feature in features)) if features else set()
        combined_time_buckets = set().union(*(feature.time_buckets for feature in features)) if features else set()
        combined_signal_fields = set().union(*(feature.signal_fields for feature in features)) if features else set()
        combined_signal_families = set().union(*(feature.signal_families for feature in features)) if features else set()
        topic = Counter(feature.primary_topic for feature in features).most_common(1)[0][0] if features else "其他/未分类"
        resource_type = Counter(feature.resource_type for feature in features).most_common(1)[0][0] if features else "未知"
        domain = Counter(feature.domain for feature in features if feature.domain).most_common(1)
        registered = Counter(feature.registered_domain for feature in features if feature.registered_domain).most_common(1)
        rule_root_totals: Counter[str] = Counter()
        for feature in features:
            for root, weight in feature.rule_root_weights.items():
                rule_root_totals[root] += weight
        total_root_weight = sum(rule_root_totals.values())
        rule_root_weights = {
            root: round(weight / total_root_weight, 4) if total_root_weight else 0.0
            for root, weight in rule_root_totals.items()
        }
        return BookmarkFeatures(
            topics=combined_topics,
            resource_types=combined_types,
            domain_tokens=combined_domain_tokens,
            path_tokens=combined_path_tokens,
            text_tokens=combined_text_tokens,
            cluster_hints=combined_cluster_hints,
            page_type_hints=combined_hints,
            site_name_tokens=combined_site_name,
            quality_signals=combined_quality_signals,
            time_buckets=combined_time_buckets,
            primary_topic=topic,
            resource_type=resource_type,
            domain=domain[0][0] if domain else "",
            registered_domain=registered[0][0] if registered else "",
            canonical_identity="",
            rule_roots=set(rule_root_weights),
            rule_root_weights=rule_root_weights,
            rule_confidence=round(sum(feature.rule_confidence for feature in features) / len(features), 3) if features else 0.0,
            signal_fields=combined_signal_fields,
            signal_families=combined_signal_families,
        )

    def _cluster_summary_similarity(self, left: BookmarkFeatures, right: BookmarkFeatures) -> float:
        return self.similarity_score(left, right)

    def _folder_quality_score(self, bookmarks: List[dict]) -> float:
        return 0.0

    def _representative_tokens(self, bookmarks: List[dict], limit: int = 6) -> List[str]:
        counter: Counter[str] = Counter()
        for bookmark in bookmarks:
            features = self.build_feature_set(bookmark)
            counter.update(features.topics)
            counter.update(features.text_tokens)
            counter.update(features.path_tokens)
        filtered: list[str] = []
        seen = set()
        min_weak_support = 1 if len(bookmarks) <= 2 else 2
        ranked_tokens = sorted(
            counter.items(),
            key=lambda item: (-item[1], normalize_topic_token(self.clean_topic_token(item[0])), str(item[0]).lower()),
        )
        for token, count in ranked_tokens[: limit * 6]:
            cleaned = self.clean_topic_token(token)
            if not cleaned or self._is_generic_label(cleaned):
                continue
            if self._is_weak_topic_label(cleaned) and count < min_weak_support:
                continue
            marker = normalize_topic_token(cleaned)
            if marker in seen:
                continue
            seen.add(marker)
            filtered.append(token)
            if len(filtered) >= limit:
                break
        if filtered:
            return filtered
        return [token for token, _ in ranked_tokens[:limit]]

    @staticmethod
    def _clone_hierarchy_payload(payload: Dict) -> Dict:
        cloned: Dict[str, Any] = {}
        for key, value in payload.items():
            if key == "bookmarks":
                cloned[key] = list(value or [])
            elif isinstance(value, (list, dict, set)):
                cloned[key] = copy.deepcopy(value)
            else:
                cloned[key] = value
        return cloned

    def _split_strong_category_groups(self, cluster: List[dict]) -> List[List[dict]]:
        if len(cluster) < 3:
            return [cluster]

        generic_platform_share = self._generic_platform_share(cluster)
        min_group_size = 1 if generic_platform_share >= 0.45 and len(cluster) <= 5 else 2
        category_groups: dict[str, list[dict]] = defaultdict(list)
        for bookmark in cluster:
            classification = bookmark.get("classification", {})
            category = str(classification.get("category") or "").strip()
            confidence = float(classification.get("rule_confidence", 0.0) or 0.0)
            if _is_default_or_generic_category(category) or confidence < 0.72:
                continue
            category_groups[category].append(bookmark)

        eligible_groups = [
            group
            for _, group in sorted(category_groups.items())
            if len(group) >= min_group_size
        ]
        if len(eligible_groups) < 2:
            return [cluster]

        coverage = sum(len(group) for group in eligible_groups) / len(cluster)
        group_shares = sorted((len(group) / len(cluster) for group in eligible_groups), reverse=True)
        if generic_platform_share >= 0.45:
            if coverage < 0.66:
                return [cluster]
        elif coverage < 0.7 or (len(group_shares) > 1 and group_shares[1] < 0.2):
            return [cluster]

        covered_ids = {bookmark.get("id") for group in eligible_groups for bookmark in group}
        remainder = [bookmark for bookmark in cluster if bookmark.get("id") not in covered_ids]
        split_groups = eligible_groups + ([remainder] if remainder else [])
        if 1 < len(split_groups):
            return split_groups
        return [cluster]

    def _merge_named_subcategory(self, subcategories: Dict[str, Dict], name: str, payload: Dict) -> None:
        existing = subcategories.get(name)
        if existing is None:
            subcategories[name] = self._clone_hierarchy_payload(payload)
            return
        existing["name"] = existing.get("name", payload.get("name", name))
        existing["display_order"] = min(
            existing.get("display_order", DISPLAY_ORDER_FALLBACK),
            payload.get("display_order", DISPLAY_ORDER_FALLBACK),
        )
        existing["bookmarks"].extend(payload.get("bookmarks", []))
        existing["count"] = len(existing["bookmarks"])
        existing["representative_tokens"] = self._representative_tokens(existing["bookmarks"])
        existing["source_folder_reused"] = existing.get("source_folder_reused", False) and payload.get("source_folder_reused", False)
        existing["source_folder_quality_score"] = max(existing.get("source_folder_quality_score", 0.0), payload.get("source_folder_quality_score", 0.0))
        merged_categories = set(existing.get("merge_from_categories", [])) | set(payload.get("merge_from_categories", []))
        existing["merge_from_categories"] = sorted(merged_categories)
        existing["cluster_reason"] = self._build_cluster_reason(existing["bookmarks"])

    def _build_cluster_reason(self, bookmarks: List[dict]) -> str:
        features = [self.build_feature_set(bookmark) for bookmark in bookmarks]
        topic = Counter(feature.primary_topic for feature in features if feature.primary_topic).most_common(2)
        resource_type = Counter(feature.resource_type for feature in features if feature.resource_type).most_common(1)
        hints = Counter(token for feature in features for token in feature.cluster_hints).most_common(3)
        rule_roots = Counter(
            root
            for feature in features
            for root in sorted(feature.rule_root_weights, key=lambda name: (-feature.rule_root_weights[name], name))[:1]
        ).most_common(2)
        registered = Counter(feature.registered_domain for feature in features if feature.registered_domain).most_common(2)
        parts = []
        if topic:
            parts.append("主题重合集中在 " + ", ".join(name for name, _ in topic))
        if hints:
            parts.append("聚类线索来自 " + ", ".join(name for name, _ in hints))
        if rule_roots:
            parts.append("规则先验主要指向 " + ", ".join(name for name, _ in rule_roots))
        if resource_type:
            parts.append(f"资源类型以{resource_type[0][0]}为主")
        if registered:
            parts.append("站点分布参考 " + ", ".join(name for name, _ in registered))
        return "；".join(parts) or "基于综合特征相似度聚类"

    def _fallback_clusters(self, bookmarks: List[dict], category: str) -> Dict:
        domain_clusters = self.cluster_by_domain(bookmarks)
        large_domain_clusters = {
            domain: group
            for domain, group in domain_clusters.items()
            if len(group) >= self.domain_split_min_size
        }
        other_domain_bookmarks = [
            bookmark
            for domain, group in domain_clusters.items()
            if domain not in large_domain_clusters
            for bookmark in group
        ]
        if large_domain_clusters:
            subcategories: Dict[str, Dict] = {}
            for domain, group in sorted(large_domain_clusters.items()):
                display_name = self._dominant_site_label(group, category) or self._domain_display_name(domain) or self._cluster_display_name(group, category)
                payload = {
                    "name": display_name,
                    "bookmarks": group,
                    "count": len(group),
                    "cluster_reason": "fallback: domain clustering",
                    "representative_tokens": self._representative_tokens(group),
                    "source_folder_reused": False,
                    "source_folder_quality_score": 0.0,
                    "merge_from_categories": sorted({bookmark.get("classification", {}).get("category", "") for bookmark in group if bookmark.get("classification")}),
                }
                self._merge_named_subcategory(subcategories, f"{category}/{display_name}", payload)
            if other_domain_bookmarks:
                display_name = self._cluster_display_name(other_domain_bookmarks, category)
                payload = {
                    "name": display_name,
                    "bookmarks": other_domain_bookmarks,
                    "count": len(other_domain_bookmarks),
                    "cluster_reason": "fallback: remaining small domain groups",
                    "representative_tokens": self._representative_tokens(other_domain_bookmarks),
                    "source_folder_reused": False,
                    "source_folder_quality_score": 0.0,
                    "merge_from_categories": sorted({bookmark.get("classification", {}).get("category", "") for bookmark in other_domain_bookmarks if bookmark.get("classification")}),
                }
                self._merge_named_subcategory(subcategories, f"{category}/{display_name}", payload)
            return {"category": category, "subcategories": subcategories, "bookmarks": [], "count": len(bookmarks)}
        if len(domain_clusters) > 1:
            subcategories: Dict[str, Dict] = {}
            for domain, group in sorted(domain_clusters.items()):
                display_name = self._dominant_site_label(group, category) or self._domain_display_name(domain) or self._cluster_display_name(group, category)
                payload = {
                    "name": display_name,
                    "bookmarks": group,
                    "count": len(group),
                    "cluster_reason": "fallback: domain clustering",
                    "representative_tokens": self._representative_tokens(group),
                    "source_folder_reused": False,
                    "source_folder_quality_score": 0.0,
                    "merge_from_categories": sorted({bookmark.get("classification", {}).get("category", "") for bookmark in group if bookmark.get("classification")}),
                }
                self._merge_named_subcategory(subcategories, f"{category}/{display_name}", payload)
            return {"category": category, "subcategories": subcategories, "bookmarks": [], "count": len(bookmarks)}
        keyword_clusters = self.cluster_by_keywords(bookmarks)
        subcategories: Dict[str, Dict] = {}
        for name, group in keyword_clusters.items():
            display_name = self._human_label(name)
            if self._is_generic_label(display_name, category):
                display_name = self._cluster_display_name(group, category)
            payload = {
                "name": display_name,
                "bookmarks": group,
                "count": len(group),
                "cluster_reason": "fallback: keyword clustering",
                "representative_tokens": self._representative_tokens(group),
                "source_folder_reused": False,
                "source_folder_quality_score": 0.0,
                "merge_from_categories": sorted({bookmark.get("classification", {}).get("category", "") for bookmark in group if bookmark.get("classification")}),
            }
            self._merge_named_subcategory(subcategories, f"{category}/{display_name}", payload)
        return {"category": category, "subcategories": subcategories, "bookmarks": [], "count": len(bookmarks)}

    def _fallback_cluster_groups(self, bookmarks: List[dict]) -> List[List[dict]]:
        if len(bookmarks) <= 2:
            return [bookmarks]

        registered_domain_groups: dict[str, list[dict]] = defaultdict(list)
        for bookmark in bookmarks:
            feature = self.build_feature_set(bookmark)
            key = feature.registered_domain or feature.domain
            if key and not self._is_generic_platform(key):
                registered_domain_groups[key].append(bookmark)

        significant_groups = [group for _, group in sorted(registered_domain_groups.items()) if len(group) >= 2]
        covered_ids = {bookmark.get("id") for group in significant_groups for bookmark in group}
        remainder = [bookmark for bookmark in bookmarks if bookmark.get("id") not in covered_ids]
        if len(significant_groups) >= 2:
            return significant_groups + ([remainder] if remainder else [])

        keyword_clusters = self.cluster_by_keywords(bookmarks)
        if len(keyword_clusters) > 1:
            return list(keyword_clusters.values())
        return [bookmarks]

    def cluster_bookmarks(self, bookmarks: List[dict]) -> List[List[dict]]:
        if not bookmarks:
            return []
        if len(bookmarks) == 1:
            return [bookmarks]

        features = [self.build_feature_set(bookmark) for bookmark in bookmarks]
        coarse_clusters = self._connected_components(bookmarks, features)
        refined_clusters = self._split_if_needed(coarse_clusters)
        merged_clusters = self._merge_if_needed(refined_clusters)
        final_clusters: List[List[dict]] = []
        for cluster in merged_clusters:
            strong_category_splits = self._split_strong_category_groups(cluster)
            if len(strong_category_splits) > 1:
                final_clusters.extend(strong_category_splits)
                continue
            cluster_features = [self.build_feature_set(bookmark) for bookmark in cluster]
            consistency = self._cluster_consistency(cluster_features)
            if len(cluster) >= max(6, self.min_cluster_size * 2) and consistency < 0.42:
                fallback_groups = self._fallback_cluster_groups(cluster)
                if 1 < len(fallback_groups) < len(cluster):
                    final_clusters.extend(fallback_groups)
                    continue
            final_clusters.append(cluster)
        return sorted(
            final_clusters,
            key=lambda group: (
                -len(group),
                sorted(bookmark.get("id", "") for bookmark in group)[0] if group else "",
            ),
        )

    def build_hierarchy(self, bookmarks: List[dict], category: str, threshold: int = 20) -> Dict:
        if len(bookmarks) <= threshold:
            return {
                "category": category,
                "subcategories": {},
                "bookmarks": bookmarks,
                "count": len(bookmarks),
                "cluster_reason": "数量较少，保留在上层",
                "representative_tokens": self._representative_tokens(bookmarks),
                "source_folder_reused": False,
                "source_folder_quality_score": 0.0,
                "merge_from_categories": sorted({bookmark.get("classification", {}).get("category", "") for bookmark in bookmarks if bookmark.get("classification")}),
            }

        features = [self.build_feature_set(bookmark) for bookmark in bookmarks]
        merged_clusters = self.cluster_bookmarks(bookmarks)
        overall_consistency = self._cluster_consistency(features) if features else 1.0
        if len(merged_clusters) == 1 and len(bookmarks) >= max(4, threshold * 2) and overall_consistency < 0.5:
            return self._fallback_clusters(bookmarks, category)

        subcategories = {}
        ungrouped = []
        for cluster in merged_clusters:
            if len(cluster) < 2:
                ungrouped.extend(cluster)
                continue
            folder_counter = Counter(tuple(bookmark.get("original_folder_path", [])) for bookmark in cluster if bookmark.get("original_folder_path"))
            best_folder = list(folder_counter.most_common(1)[0][0]) if folder_counter else []
            folder_quality = self._folder_quality_score(cluster)
            source_folder_reused = bool(best_folder and folder_quality >= 0.68)
            display_name = self._cluster_display_name(cluster, category, best_folder, folder_quality)
            subcategory_name = f"{category}/{display_name}"
            payload = {
                "name": display_name,
                "bookmarks": cluster,
                "count": len(cluster),
                "cluster_reason": self._build_cluster_reason(cluster),
                "representative_tokens": self._representative_tokens(cluster),
                "source_folder_reused": source_folder_reused,
                "source_folder_quality_score": folder_quality,
                "merge_from_categories": sorted({bookmark.get("classification", {}).get("category", "") for bookmark in cluster if bookmark.get("classification")}),
            }
            self._merge_named_subcategory(subcategories, subcategory_name, payload)
        if not subcategories:
            return self._fallback_clusters(bookmarks, category)
        return {
            "category": category,
            "subcategories": subcategories,
            "bookmarks": ungrouped,
            "count": len(bookmarks),
            "cluster_reason": "先全量粗聚类，再按簇内一致性拆分并按簇间相似性合并",
            "representative_tokens": self._representative_tokens(bookmarks),
            "source_folder_reused": False,
            "source_folder_quality_score": 0.0,
            "merge_from_categories": sorted({bookmark.get("classification", {}).get("category", "") for bookmark in bookmarks if bookmark.get("classification")}),
        }


def _cluster_root_distribution(bookmarks: List[dict]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    category_counter: Counter[str] = Counter()
    root_weights: Counter[str] = Counter()
    hint_counter: Counter[str] = Counter()
    domain_counter: Counter[str] = Counter()

    for bookmark in bookmarks:
        classification = bookmark.get("classification", {})
        category = classification.get("display_category") or classification.get("category", "")
        if category:
            category_counter[category] += 1

        rule_roots = classification.get("rule_roots", []) or []
        if rule_roots:
            for item in rule_roots[:3]:
                root = str(item.get("root", "")).strip()
                if not root:
                    continue
                support = float(item.get("support", 0.0) or 0.0)
                total = float(item.get("total", 0.0) or 0.0)
                root_weights[root] += support or total or 1.0
        elif category:
            root_weights[category.split("/")[0]] += 1.0

        for hint in classification.get("cluster_hints", [])[:4]:
            hint_text = str(hint).strip()
            if hint_text:
                hint_counter[hint_text] += 1

        feature_domain = bookmark.get("domain") or urlparse(bookmark.get("url", "")).netloc
        if feature_domain:
            labels = [part for part in feature_domain.lower().split(".") if part]
            if len(labels) > 2:
                feature_domain = ".".join(labels[-2:])
            domain_counter[feature_domain] += 1

    total_root_weight = sum(root_weights.values())
    dominant_rule_roots = [
        {
            "root": root,
            "score": round(score, 2),
            "support": round(score / total_root_weight, 4) if total_root_weight else 0.0,
        }
        for root, score in root_weights.most_common(4)
    ]
    total_categories = max(len(bookmarks), 1)
    dominant_categories = [
        {
            "category": category,
            "root": category.split("/")[0],
            "count": count,
            "share": round(count / total_categories, 4),
        }
        for category, count in category_counter.most_common(4)
    ]
    discovered_topics = [topic for topic, _ in hint_counter.most_common(8)]
    top_domains = [
        {
            "domain": domain,
            "count": count,
            "share": round(count / total_categories, 4),
        }
        for domain, count in domain_counter.most_common(4)
    ]
    return dominant_rule_roots, dominant_categories, discovered_topics, top_domains


def _cluster_confirmation_distribution(bookmarks: List[dict]) -> dict[str, Any]:
    confirmation_bucket_counts: Counter[str] = Counter()
    fetch_status_counts: Counter[str] = Counter()
    review_required_count = 0
    for bookmark in bookmarks:
        classification = bookmark.get("classification", {})
        bucket = str(classification.get("confirmation_bucket") or "unknown")
        confirmation_bucket_counts[bucket] += 1
        signal_pack = bookmark.get("signal_pack") or build_signal_pack(bookmark)
        fetch_status = str(signal_pack_sections(signal_pack)["health_access"].get("fetch_status") or "unknown")
        fetch_status_counts[fetch_status] += 1
        if classification.get("review_required"):
            review_required_count += 1
    total = max(len(bookmarks), 1)
    return {
        "confirmation_bucket_counts": dict(confirmation_bucket_counts),
        "fetch_status_counts": dict(fetch_status_counts),
        "review_required_share": round(review_required_count / total, 4),
        "fetch_blocked_share": round(confirmation_bucket_counts.get("fetch_blocked", 0) / total, 4),
        "rule_gap_share": round(confirmation_bucket_counts.get("rule_gap", 0) / total, 4),
        "low_confidence_share": round(confirmation_bucket_counts.get("low_confidence", 0) / total, 4),
    }


def _cluster_feature_provenance(clusterer: BookmarkClusterer, bookmarks: List[dict]) -> dict[str, Any]:
    family_counts: Counter[str] = Counter()
    field_counts: Counter[str] = Counter()
    for bookmark in bookmarks:
        feature = clusterer.build_feature_set(bookmark)
        family_counts.update(feature.signal_families)
        field_counts.update(feature.signal_fields)
    return {
        "signal_families": [
            {"family": family, "bookmark_count": count}
            for family, count in family_counts.most_common()
        ],
        "signal_fields": [
            {"field": field, "bookmark_count": count}
            for field, count in field_counts.most_common(24)
        ],
    }


def _is_default_or_generic_category(category: str, tidy_root_name: str = "待整理") -> bool:
    return (
        not category
        or category in {tidy_root_name, "其他/未分类", "其他"}
        or category.endswith("/其他")
        or category.endswith("/未分类")
    )


def stable_cluster_id(bookmarks: List[dict]) -> str:
    identities = []
    for bookmark in bookmarks:
        signal_pack = bookmark.get("signal_pack") or build_signal_pack(bookmark)
        sections = signal_pack_sections(signal_pack)
        identities.append(str(sections["identity"].get("canonical_identity") or bookmark.get("url") or bookmark.get("id") or ""))
    digest = hashlib.sha1("\n".join(sorted(identities)).encode("utf-8")).hexdigest()[:12]
    return f"bc_{digest}"


def stable_tidy_bundle_id(bookmarks: List[dict]) -> str:
    identities = []
    for bookmark in bookmarks:
        signal_pack = bookmark.get("signal_pack") or build_signal_pack(bookmark)
        sections = signal_pack_sections(signal_pack)
        identities.append(str(sections["identity"].get("canonical_identity") or bookmark.get("url") or bookmark.get("id") or ""))
    digest = hashlib.sha1("\n".join(sorted(identities)).encode("utf-8")).hexdigest()[:12]
    return f"ts_{digest}"


def build_cluster_payloads(
    clusterer: BookmarkClusterer,
    bookmarks: List[dict],
    threshold: int,
    discovery_root_name: str = "发现主题",
    tidy_root_name: str = "待整理",
) -> list[dict[str, Any]]:
    cluster_groups = clusterer.cluster_bookmarks(bookmarks)
    payloads: list[dict[str, Any]] = []
    for cluster in cluster_groups:
        dominant_rule_roots, dominant_categories, discovered_topics, top_domains = _cluster_root_distribution(cluster)
        confirmation_distribution = _cluster_confirmation_distribution(cluster)
        feature_provenance = _cluster_feature_provenance(clusterer, cluster)
        folder_counter = Counter(tuple(bookmark.get("original_folder_path", [])) for bookmark in cluster if bookmark.get("original_folder_path"))
        best_folder = list(folder_counter.most_common(1)[0][0]) if folder_counter else []
        folder_quality = clusterer._folder_quality_score(cluster)
        category_hint = (
            dominant_categories[0]["category"]
            if dominant_categories else
            (dominant_rule_roots[0]["root"] if dominant_rule_roots else discovery_root_name)
        )
        cluster_label = clusterer._cluster_display_name(cluster, category_hint, best_folder, folder_quality)
        cluster_label_basis = "hint_or_token"
        dominant_leaf = (
            clusterer._category_leaf_label(dominant_categories[0]["category"], dominant_categories[0]["root"])
            if dominant_categories else ""
        )
        label_context = dominant_categories[0]["root"] if dominant_categories else category_hint.split("/")[0]
        if dominant_leaf and dominant_categories and dominant_categories[0]["share"] >= 0.6:
            cluster_label = dominant_leaf
            cluster_label_basis = "dominant_leaf"
        if len(cluster) == 1 and dominant_categories:
            if dominant_leaf:
                cluster_label = dominant_leaf
                cluster_label_basis = "single_leaf"
        if clusterer._is_generic_label(cluster_label, label_context) and dominant_leaf:
            cluster_label = dominant_leaf
            cluster_label_basis = "dominant_leaf_fallback"
        if clusterer._is_generic_label(cluster_label, label_context):
            for topic in discovered_topics:
                candidate = clusterer._human_label(topic)
                if candidate and not clusterer._is_generic_label(candidate, label_context):
                    cluster_label = candidate
                    cluster_label_basis = "discovered_topic_fallback"
                    break

        top_support = dominant_rule_roots[0]["support"] if dominant_rule_roots else 0.0
        second_support = dominant_rule_roots[1]["support"] if len(dominant_rule_roots) > 1 else 0.0
        fallback_root = dominant_categories[0]["root"] if dominant_categories else discovery_root_name
        rule_purity = round(
            top_support if dominant_rule_roots else (dominant_categories[0]["share"] if dominant_categories else 0.0),
            4,
        )
        has_discovered_topic = bool(discovered_topics)
        dominant_category = dominant_categories[0]["category"] if dominant_categories else ""
        average_rule_confidence = round(
            sum(float(bookmark.get("classification", {}).get("rule_confidence", 0.0) or 0.0) for bookmark in cluster) / len(cluster),
            4,
        ) if cluster else 0.0
        top_rule_root = dominant_rule_roots[0]["root"] if dominant_rule_roots else ""
        generic_platform_share = max(
            (
                float(item.get("share", 0.0) or 0.0)
                for item in top_domains
                if item.get("domain") and clusterer._is_generic_platform(item["domain"])
            ),
            default=0.0,
        )
        fetch_blocked_share = float(confirmation_distribution.get("fetch_blocked_share", 0.0) or 0.0)
        review_required_share = float(confirmation_distribution.get("review_required_share", 0.0) or 0.0)
        discovery_allowed = not (fetch_blocked_share >= 0.5 or review_required_share >= 0.75)
        normal_category_support = sum(
            item.get("share", 0.0)
            for item in dominant_categories
            if not _is_default_or_generic_category(item.get("category", ""), tidy_root_name)
            and (not top_rule_root or item.get("root") == top_rule_root)
        )
        if generic_platform_share >= 0.8 and normal_category_support < 0.25:
            discovery_allowed = False
        if len(cluster) == 1 and dominant_categories and not _is_default_or_generic_category(dominant_category, tidy_root_name):
            destination_root = dominant_categories[0]["root"]
        elif _is_default_or_generic_category(dominant_category, tidy_root_name) and average_rule_confidence < 0.55:
            destination_root = discovery_root_name if discovery_allowed and has_discovered_topic and len(cluster) >= 2 else tidy_root_name
        elif not dominant_rule_roots:
            if discovery_allowed and has_discovered_topic and len(cluster) >= 2:
                destination_root = discovery_root_name
            elif dominant_categories and dominant_categories[0]["share"] >= 0.5 and not _is_default_or_generic_category(dominant_category, tidy_root_name):
                destination_root = fallback_root
            else:
                destination_root = tidy_root_name
        elif normal_category_support < 0.25 and average_rule_confidence < 0.65:
            destination_root = discovery_root_name if discovery_allowed and has_discovered_topic and len(cluster) >= 2 else tidy_root_name
        elif rule_purity < 0.45 or top_support - second_support < 0.1:
            destination_root = discovery_root_name if discovery_allowed and has_discovered_topic and len(cluster) >= 2 else tidy_root_name
        else:
            destination_root = dominant_rule_roots[0]["root"]
        route_reasons = []
        if fetch_blocked_share >= 0.5:
            route_reasons.append("fetch_blocked_guard")
        if review_required_share >= 0.75:
            route_reasons.append("review_required_guard")
        if generic_platform_share >= 0.8 and normal_category_support < 0.25:
            route_reasons.append("generic_platform_guard")
        if destination_root == discovery_root_name:
            route_reasons.append("routed_to_discovery")
        elif destination_root == tidy_root_name:
            route_reasons.append("routed_to_tidy")
        else:
            route_reasons.append("routed_to_normal_root")

        payloads.append(
            {
                "cluster_id": "",
                "cluster_label": cluster_label,
                "cluster_label_basis": cluster_label_basis,
                "cluster_reason": clusterer._build_cluster_reason(cluster),
                "rule_purity": rule_purity,
                "average_rule_confidence": average_rule_confidence,
                "normal_category_support": round(normal_category_support, 4),
                "dominant_rule_roots": dominant_rule_roots,
                "dominant_categories": dominant_categories,
                "discovered_topics": discovered_topics,
                "top_domains": top_domains,
                "destination_root": destination_root,
                "representative_tokens": clusterer._representative_tokens(cluster),
                "source_folder_reused": bool(best_folder and folder_quality >= 0.68),
                "source_folder_quality_score": folder_quality,
                "confirmation_bucket_counts": confirmation_distribution["confirmation_bucket_counts"],
                "fetch_status_counts": confirmation_distribution["fetch_status_counts"],
                "review_required_share": confirmation_distribution["review_required_share"],
                "fetch_blocked_share": confirmation_distribution["fetch_blocked_share"],
                "rule_gap_share": confirmation_distribution["rule_gap_share"],
                "low_confidence_share": confirmation_distribution["low_confidence_share"],
                "feature_provenance": feature_provenance,
                "decision_trace": {
                    "route_reasons": route_reasons,
                    "cluster_label_basis": cluster_label_basis,
                    "discovery_allowed": discovery_allowed,
                    "routing_factors": {
                        "rule_purity": rule_purity,
                        "average_rule_confidence": average_rule_confidence,
                        "normal_category_support": round(normal_category_support, 4),
                        "generic_platform_share": round(generic_platform_share, 4),
                        "fetch_blocked_share": round(fetch_blocked_share, 4),
                        "review_required_share": round(review_required_share, 4),
                    },
                },
                "size_below_threshold": len(cluster) <= threshold,
                "bookmarks": cluster,
            }
        )

    ordered_payloads = sorted(
        payloads,
        key=lambda item: (
            item["destination_root"] == discovery_root_name,
            item["destination_root"],
            -len(item["bookmarks"]),
            item["cluster_label"],
        ),
    )
    for index, item in enumerate(ordered_payloads, start=1):
        item["cluster_id"] = stable_cluster_id(item.get("bookmarks", []))
        item["cluster_order"] = index
    return ordered_payloads


def build_clustered_root_hierarchy(
    clusterer: BookmarkClusterer,
    cluster_profiles: list[dict[str, Any]],
    threshold: int,
    discovery_root_name: str = "发现主题",
    tidy_root_name: str = "待整理",
) -> dict[str, dict]:
    root_payloads: dict[str, dict[str, Any]] = {}
    for profile in cluster_profiles:
        root_name = profile.get("destination_root") or discovery_root_name
        root_payload = root_payloads.setdefault(
            root_name,
            {
                "name": root_name,
                "category": root_name,
                "subcategories": {},
                "bookmarks": [],
                "cluster_reason": "先全局聚类，再按簇的主导根分类决定落点",
                "representative_tokens": [],
                "source_folder_reused": False,
                "source_folder_quality_score": 0.0,
                "merge_from_categories": [],
                "preserve_children": True,
            },
        )
        dominant_category = profile["dominant_categories"][0]["category"] if profile.get("dominant_categories") else ""
        dominant_share = profile["dominant_categories"][0]["share"] if profile.get("dominant_categories") else 0.0
        leaf_name = clusterer._category_leaf_label(dominant_category, root_name) if dominant_category else ""
        display_name = profile.get("cluster_label") or leaf_name or root_name
        has_specific_label = bool(
            (leaf_name and not clusterer._is_generic_label(leaf_name, root_name))
            or (display_name and not clusterer._is_generic_label(display_name, root_name))
        )
        should_inline_small_pure_cluster = (
            root_name not in {discovery_root_name, tidy_root_name}
            and profile.get("size_below_threshold", False)
            and dominant_share >= 0.8
            and len(profile["bookmarks"]) == 1
            and not has_specific_label
        )
        use_subcategory = (
            not should_inline_small_pure_cluster
            and (
                root_name in {discovery_root_name, tidy_root_name}
                or len(profile["bookmarks"]) > 1
                or (leaf_name and not clusterer._is_generic_label(leaf_name, root_name))
            )
        )
        if use_subcategory:
            subcategory_name = leaf_name if len(profile["bookmarks"]) == 1 and leaf_name else display_name
            payload = {
                "name": subcategory_name,
                "bookmarks": list(profile.get("bookmarks") or []),
                "count": len(profile["bookmarks"]),
                "cluster_reason": profile["cluster_reason"],
                "representative_tokens": list(profile.get("representative_tokens") or []),
                "source_folder_reused": profile["source_folder_reused"],
                "source_folder_quality_score": profile["source_folder_quality_score"],
                "merge_from_categories": sorted(
                    {
                        item["category"]
                        for item in profile.get("dominant_categories", [])
                        if item.get("category")
                    }
                ),
                "dominant_rule_roots": copy.deepcopy(profile.get("dominant_rule_roots", [])),
                "discovered_topics": list(profile.get("discovered_topics", [])),
            }
            clusterer._merge_named_subcategory(root_payload["subcategories"], subcategory_name, payload)
        else:
            root_payload["bookmarks"].extend(profile["bookmarks"])
        root_payload["merge_from_categories"].extend(
            item["category"]
            for item in profile.get("dominant_categories", [])
            if item.get("category")
        )

    hierarchy: dict[str, dict] = {}
    for root_name in sorted(root_payloads, key=lambda item: (item == discovery_root_name, item)):
        payload = root_payloads[root_name]
        payload["merge_from_categories"] = sorted(set(payload.get("merge_from_categories", [])))
        payload["count"] = len(payload.get("bookmarks", [])) + sum(item.get("count", 0) for item in payload["subcategories"].values())
        payload["representative_tokens"] = clusterer._representative_tokens(
            list(payload.get("bookmarks", []))
            + [bookmark for child in payload["subcategories"].values() for bookmark in child.get("bookmarks", [])]
        )
        hierarchy[root_name] = clusterer.optimize_hierarchy_payload(root_name, payload, is_root=True)
    return hierarchy


def build_root_hierarchy(
    clusterer: BookmarkClusterer,
    bookmarks: List[dict],
    threshold: int,
    discovery_root_name: str = "发现主题",
    tidy_root_name: str = "待整理",
) -> dict[str, dict]:
    cluster_profiles = build_cluster_payloads(
        clusterer,
        bookmarks,
        threshold=threshold,
        discovery_root_name=discovery_root_name,
        tidy_root_name=tidy_root_name,
    )
    return build_clustered_root_hierarchy(
        clusterer,
        cluster_profiles,
        threshold=threshold,
        discovery_root_name=discovery_root_name,
        tidy_root_name=tidy_root_name,
    )


def _collect_payload_bookmarks(payload: dict) -> list[dict]:
    bookmarks = list(payload.get("bookmarks") or [])
    for child in (payload.get("subcategories") or {}).values():
        bookmarks.extend(_collect_payload_bookmarks(child))
    return bookmarks


def limit_payload_depth(payload: dict, max_depth: int, *, depth: int = 1) -> dict:
    limited = copy.deepcopy(payload)
    limited["bookmarks"] = list(limited.get("bookmarks") or [])
    limited["subcategories"] = dict(limited.get("subcategories") or {})
    if max_depth <= 0:
        return limited
    if depth >= max_depth:
        limited["bookmarks"].extend(
            bookmark
            for child in limited["subcategories"].values()
            for bookmark in _collect_payload_bookmarks(child)
        )
        limited["subcategories"] = {}
        limited["count"] = len(limited["bookmarks"])
        return limited

    limited["subcategories"] = {
        name: limit_payload_depth(child, max_depth, depth=depth + 1)
        for name, child in limited["subcategories"].items()
    }
    limited["count"] = len(limited["bookmarks"]) + sum(child.get("count", 0) for child in limited["subcategories"].values())
    return limited


def compact_bookmark_payload(bookmark: dict) -> dict:
    compact = {
        "id": bookmark.get("id"),
        "name": bookmark.get("name"),
        "url": bookmark.get("url"),
    }
    for field in ("add_date", "icon", "domain", "fetch_normalized_url"):
        value = bookmark.get(field)
        if value not in {None, ""}:
            compact[field] = value
    classification = bookmark.get("classification", {})
    classification_summary = {
        "category": classification.get("category"),
        "resource_type": classification.get("resource_type"),
        "review_required": classification.get("review_required"),
        "review_category": classification.get("review_category"),
    }
    classification_summary = {key: value for key, value in classification_summary.items() if value not in {None, ""}}
    if classification_summary:
        compact["classification"] = classification_summary
    return compact


def compact_hierarchy_payload(payload: dict) -> dict:
    compact = {
        key: value
        for key, value in payload.items()
        if key not in {"bookmarks", "subcategories"}
    }
    compact["bookmarks"] = [compact_bookmark_payload(bookmark) for bookmark in payload.get("bookmarks", [])]
    compact["subcategories"] = {
        name: compact_hierarchy_payload(child)
        for name, child in (payload.get("subcategories") or {}).items()
    }
    return compact


def compact_hierarchy_map(hierarchy: dict[str, dict]) -> dict[str, dict]:
    return {
        name: compact_hierarchy_payload(payload)
        for name, payload in hierarchy.items()
    }


def _bookmark_confirmation_bucket(bookmark: dict) -> str:
    classification = bookmark.get("classification", {}) if isinstance(bookmark.get("classification"), dict) else {}
    bucket = str(classification.get("confirmation_bucket") or "low_confidence").strip().lower()
    return bucket if bucket in TIDY_BUCKET_DISPLAY_NAMES else "low_confidence"


def _tidy_bucket_name(bucket: str) -> str:
    return TIDY_BUCKET_DISPLAY_NAMES.get(bucket, TIDY_BUCKET_DISPLAY_NAMES["low_confidence"])


def _pick_dominant_tidy_bucket(bookmarks: list[dict]) -> str:
    counts: Counter[str] = Counter(_bookmark_confirmation_bucket(bookmark) for bookmark in bookmarks)
    if not counts:
        return "low_confidence"
    return sorted(
        counts.items(),
        key=lambda item: (-item[1], TIDY_BUCKET_DISPLAY_ORDER.get(_tidy_bucket_name(item[0]), 10**6), item[0]),
    )[0][0]


def _is_high_quality_tidy_label(clusterer: BookmarkClusterer, label: str, bucket_name: str, payload: dict) -> bool:
    cleaned = clusterer._human_label(label)
    if not cleaned or int(payload.get("count", 0) or 0) < 2:
        return False
    if clusterer._is_generic_label(cleaned, bucket_name):
        return False
    if clusterer._is_weak_topic_label(cleaned) or clusterer._is_noisy_topic_label(cleaned):
        return False
    if normalize_topic_token(cleaned) in {
        normalize_topic_token(bucket_name),
        normalize_topic_token("待整理"),
        normalize_topic_token(payload.get("category", "")),
    }:
        return False
    if cleaned.lower().endswith(("-csdn", "-zhihu", "-github")):
        return False
    return True


def _bookmark_tidy_root_hint(
    bookmark: dict,
    *,
    tidy_root_name: str = "待整理",
) -> str:
    classification = bookmark.get("classification", {}) if isinstance(bookmark.get("classification"), dict) else {}
    category = str(classification.get("category") or "").strip()
    if category and not _is_default_or_generic_category(category, tidy_root_name):
        return category.split("/")[0]

    for candidate in classification.get("rule_candidates", [])[:3]:
        candidate_category = str(candidate.get("category") or "").strip()
        root = str(candidate.get("root") or "").strip()
        total = float(candidate.get("total", 0.0) or 0.0)
        if root and not _is_default_or_generic_category(candidate_category, tidy_root_name):
            if candidate.get("strong_evidence") or total >= 18:
                return root

    for item in classification.get("rule_roots", [])[:2]:
        root = str(item.get("root") or "").strip()
        support = float(item.get("support", 0.0) or 0.0)
        total = float(item.get("total", 0.0) or 0.0)
        if root and (support >= 0.45 or total >= 18):
            return root
    return ""


def _tidy_label_candidates(
    clusterer: BookmarkClusterer,
    bookmark: dict,
    *,
    bucket_name: str,
    tidy_root_name: str = "待整理",
) -> list[dict[str, Any]]:
    classification = bookmark.get("classification", {}) if isinstance(bookmark.get("classification"), dict) else {}
    feature = clusterer.build_feature_set(bookmark)
    generic_platform = clusterer._is_generic_platform(feature.registered_domain or feature.domain)
    root_hint = _bookmark_tidy_root_hint(bookmark, tidy_root_name=tidy_root_name)
    label_context = root_hint or bucket_name
    minimum_open_topic_score = 3 if bucket_name == "低置信度" else 2
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(label: str, source: str, priority: int) -> None:
        cleaned = clusterer.clean_topic_token(clusterer._human_label(label))
        if not cleaned:
            return
        marker = normalize_topic_token(cleaned)
        if not marker or marker in seen:
            return
        if clusterer._is_generic_label(cleaned, label_context):
            return
        if clusterer._is_weak_topic_label(cleaned) or clusterer._is_noisy_topic_label(cleaned):
            return
        if marker in {
            normalize_topic_token(bucket_name),
            normalize_topic_token(tidy_root_name),
            normalize_topic_token(root_hint),
        }:
            return
        seen.add(marker)
        candidates.append(
            {
                "label": cleaned,
                "key": marker,
                "root_hint": root_hint,
                "source": source,
                "priority": priority,
            }
        )

    for item in classification.get("rule_candidates", [])[:2]:
        category = str(item.get("category") or "").strip()
        total = float(item.get("total", 0.0) or 0.0)
        if _is_default_or_generic_category(category, tidy_root_name):
            continue
        if bool(item.get("strong_evidence")) or total >= 20:
            add(clusterer._category_leaf_label(category, str(item.get("root") or "").strip()), "rule_leaf", 5)

    for item in classification.get("open_topic_candidates", [])[:2]:
        score = float(item.get("score", 0.0) or 0.0)
        if score >= minimum_open_topic_score:
            add(str(item.get("topic") or ""), "open_topic", 4)

    for hint in classification.get("cluster_hints", [])[:4]:
        add(str(hint or ""), "cluster_hint", 3)

    domain = feature.registered_domain or feature.domain
    if domain and not generic_platform:
        add(clusterer._domain_display_name(domain), "domain", 2)

    return candidates


def build_tidy_semantic_bundles(
    clusterer: BookmarkClusterer,
    bookmarks: list[dict],
    *,
    bucket_name: str,
    tidy_root_name: str = "待整理",
    min_support: int = 2,
    root_fallback_min_count: int = 3,
) -> tuple[list[dict[str, Any]], list[dict]]:
    if not bookmarks:
        return [], []

    candidate_support: dict[tuple[str, str], set[str]] = defaultdict(set)
    candidate_labels: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    candidate_sources: dict[tuple[str, str], set[str]] = defaultdict(set)
    candidate_priorities: dict[tuple[str, str], int] = defaultdict(int)
    bookmark_candidates: dict[str, list[dict[str, Any]]] = {}
    identity_to_bookmark: dict[str, dict] = {}

    for bookmark in bookmarks:
        feature = clusterer.build_feature_set(bookmark)
        identity = feature.canonical_identity or str(bookmark.get("id") or bookmark.get("url") or "")
        if not identity:
            continue
        identity_to_bookmark[identity] = bookmark
        candidates = _tidy_label_candidates(
            clusterer,
            bookmark,
            bucket_name=bucket_name,
            tidy_root_name=tidy_root_name,
        )
        bookmark_candidates[identity] = candidates
        for item in candidates:
            key = (item["root_hint"], item["key"])
            candidate_support[key].add(identity)
            candidate_labels[key][item["label"]] += 1
            candidate_sources[key].add(item["source"])
            candidate_priorities[key] = max(candidate_priorities[key], int(item["priority"]))

    selected_keys = {
        key
        for key, identities in candidate_support.items()
        if len(identities) >= min_support
    }
    grouped_identities: dict[tuple[str, str], list[str]] = defaultdict(list)
    assigned_identities: set[str] = set()
    for identity, candidates in bookmark_candidates.items():
        for item in candidates:
            key = (item["root_hint"], item["key"])
            if key in selected_keys:
                grouped_identities[key].append(identity)
                assigned_identities.add(identity)
                break

    bundles: list[dict[str, Any]] = []
    for key, identities in grouped_identities.items():
        bookmarks_in_group = [identity_to_bookmark[item] for item in identities if item in identity_to_bookmark]
        if len(bookmarks_in_group) < min_support:
            continue
        label = sorted(
            candidate_labels[key].items(),
            key=lambda item: (-item[1], -len(normalize_topic_token(item[0])), item[0]),
        )[0][0]
        bundles.append(
            {
                "bundle_id": stable_tidy_bundle_id(bookmarks_in_group),
                "bundle_label": label,
                "bucket_name": bucket_name,
                "root_hint": key[0],
                "support_count": len(bookmarks_in_group),
                "source_types": sorted(candidate_sources[key]),
                "bookmarks": bookmarks_in_group,
                "representative_tokens": clusterer._representative_tokens(bookmarks_in_group),
                "merge_from_categories": sorted(
                    {
                        str(bookmark.get("classification", {}).get("category") or "").strip()
                        for bookmark in bookmarks_in_group
                        if bookmark.get("classification")
                    }
                ),
                "cluster_reason": f"按待整理语义信号二次聚合: {bucket_name}",
                "priority": candidate_priorities[key],
            }
        )

    leftovers = [
        identity_to_bookmark[identity]
        for identity in identity_to_bookmark
        if identity not in assigned_identities
    ]
    root_fallback_groups: dict[str, list[dict]] = defaultdict(list)
    for bookmark in leftovers:
        root_hint = _bookmark_tidy_root_hint(bookmark, tidy_root_name=tidy_root_name)
        if root_hint:
            root_fallback_groups[root_hint].append(bookmark)

    root_fallback_assigned: set[str] = set()
    for root_hint, group in sorted(
        root_fallback_groups.items(),
        key=lambda item: (-len(item[1]), item[0]),
    ):
        if len(group) < root_fallback_min_count:
            continue
        bundles.append(
            {
                "bundle_id": stable_tidy_bundle_id(group),
                "bundle_label": f"{root_hint}相关",
                "bucket_name": bucket_name,
                "root_hint": root_hint,
                "support_count": len(group),
                "source_types": ["root_hint_fallback"],
                "bookmarks": group,
                "representative_tokens": clusterer._representative_tokens(group),
                "merge_from_categories": sorted(
                    {
                        str(bookmark.get("classification", {}).get("category") or "").strip()
                        for bookmark in group
                        if bookmark.get("classification")
                    }
                ),
                "cluster_reason": f"按最接近的现有 root 聚合待整理书签: {root_hint}",
                "priority": 1,
            }
        )
        for bookmark in group:
            feature = clusterer.build_feature_set(bookmark)
            identity = feature.canonical_identity or str(bookmark.get("id") or bookmark.get("url") or "")
            if identity:
                root_fallback_assigned.add(identity)

    leftovers = [
        bookmark
        for bookmark in leftovers
        if (clusterer.build_feature_set(bookmark).canonical_identity or str(bookmark.get("id") or bookmark.get("url") or "")) not in root_fallback_assigned
    ]
    bundles.sort(
        key=lambda item: (-item["support_count"], -int(item.get("priority", 0) or 0), item["bundle_label"]),
    )
    return bundles, leftovers


def restructure_tidy_root_for_display(
    clusterer: BookmarkClusterer,
    tidy_payload: dict,
    *,
    tidy_root_name: str = "待整理",
    display_options: dict | None = None,
) -> dict:
    display_options = display_options or {}
    semantic_min_support = int(display_options.get("tidy_semantic_min_support", 2))
    root_fallback_min_count = int(display_options.get("tidy_root_fallback_min_count", 3))
    rewritten = copy.deepcopy(tidy_payload)
    bucket_nodes = {
        name: {
            "name": name,
            "category": f"{tidy_root_name}/{name}",
            "subcategories": {},
            "bookmarks": [],
            "count": 0,
            "cluster_reason": f"按待整理原因分组显示: {name}",
            "representative_tokens": [],
            "source_folder_reused": False,
            "source_folder_quality_score": 0.0,
            "merge_from_categories": [],
            "preserve_children": True,
            "display_order": TIDY_BUCKET_DISPLAY_ORDER.get(name, 10**6),
        }
        for name in TIDY_BUCKET_DISPLAY_ORDER
    }
    bucket_pending_bookmarks: dict[str, list[dict]] = defaultdict(list)

    def add_bookmarks_to_bucket(bucket_name: str, bookmarks: list[dict], *, merge_from: list[str] | None = None) -> None:
        bucket_pending_bookmarks[bucket_name].extend(bookmarks)
        if merge_from:
            bucket_nodes[bucket_name]["merge_from_categories"].extend(merge_from)

    for bookmark in rewritten.get("bookmarks", []):
        bucket_name = _tidy_bucket_name(_bookmark_confirmation_bucket(bookmark))
        add_bookmarks_to_bucket(bucket_name, [bookmark], merge_from=[bookmark.get("classification", {}).get("category", "")])

    for child_name, child_payload in (rewritten.get("subcategories") or {}).items():
        child_bookmarks = _collect_payload_bookmarks(child_payload)
        if not child_bookmarks:
            continue
        bucket_name = _tidy_bucket_name(_pick_dominant_tidy_bucket(child_bookmarks))
        merge_from = list(child_payload.get("merge_from_categories") or [])
        if len(child_bookmarks) == 1 or not _is_high_quality_tidy_label(clusterer, child_name, bucket_name, child_payload):
            add_bookmarks_to_bucket(bucket_name, child_bookmarks, merge_from=merge_from)
            continue

        bucket_nodes[bucket_name]["subcategories"][child_name] = copy.deepcopy(child_payload)
        bucket_nodes[bucket_name]["merge_from_categories"].extend(merge_from)

    for bucket_name, pending_bookmarks in bucket_pending_bookmarks.items():
        bundles, leftovers = build_tidy_semantic_bundles(
            clusterer,
            pending_bookmarks,
            bucket_name=bucket_name,
            tidy_root_name=tidy_root_name,
            min_support=semantic_min_support,
            root_fallback_min_count=root_fallback_min_count,
        )
        bucket_node = bucket_nodes[bucket_name]
        for bundle in bundles:
            label = str(bundle.get("bundle_label") or "其他").strip() or "其他"
            payload = {
                "name": label,
                "category": f"{tidy_root_name}/{bucket_name}/{label}",
                "subcategories": {},
                "bookmarks": list(bundle.get("bookmarks") or []),
                "count": int(bundle.get("support_count", len(bundle.get("bookmarks", []))) or 0),
                "cluster_reason": bundle.get("cluster_reason") or f"按待整理语义信号聚合: {bucket_name}",
                "representative_tokens": list(bundle.get("representative_tokens") or []),
                "source_folder_reused": False,
                "source_folder_quality_score": 0.0,
                "merge_from_categories": list(bundle.get("merge_from_categories") or []),
                "preserve_children": True,
            }
            clusterer._merge_named_subcategory(bucket_node["subcategories"], label, payload)
        bucket_node["bookmarks"].extend(leftovers)

    final_subcategories = {}
    total_count = 0
    for bucket_name in sorted(bucket_nodes, key=lambda item: TIDY_BUCKET_DISPLAY_ORDER.get(item, 10**6)):
        bucket_node = bucket_nodes[bucket_name]
        bucket_node["merge_from_categories"] = sorted(set(filter(None, bucket_node.get("merge_from_categories", []))))
        bucket_node["count"] = len(bucket_node.get("bookmarks", [])) + sum(
            child.get("count", 0) for child in bucket_node.get("subcategories", {}).values()
        )
        if bucket_node["count"] <= 0:
            continue
        bucket_bookmarks = list(bucket_node.get("bookmarks", []))
        for child in bucket_node.get("subcategories", {}).values():
            bucket_bookmarks.extend(_collect_payload_bookmarks(child))
        bucket_node["representative_tokens"] = clusterer._representative_tokens(bucket_bookmarks)
        final_subcategories[bucket_name] = bucket_node
        total_count += bucket_node["count"]

    rewritten["bookmarks"] = []
    rewritten["subcategories"] = final_subcategories
    rewritten["count"] = total_count
    rewritten["cluster_reason"] = "按待整理原因重组显示层目录"
    rewritten["representative_tokens"] = clusterer._representative_tokens(
        [bookmark for bucket in final_subcategories.values() for bookmark in _collect_payload_bookmarks(bucket)]
    )
    rewritten["preserve_children"] = True
    return rewritten


def build_auto_root_groups(root_hierarchy: dict[str, dict], display_options: dict) -> list[dict[str, list[str] | str]]:
    discovery_root_name = display_options.get("discovery_root_name", "发现主题")
    tidy_root_name = display_options.get("tidy_root_name", "待整理")
    main_group_name = display_options.get("main_group_name", "主要主题")
    max_direct_normal_roots = int(display_options.get("max_direct_normal_roots", 10))
    standalone_discovery_min_count = int(display_options.get("standalone_discovery_min_count", 3))

    normal_roots = [
        root
        for root in root_hierarchy
        if root not in {discovery_root_name, tidy_root_name, "其他/未分类"}
    ]
    specs: list[dict[str, list[str] | str]] = []
    if normal_roots:
        if len(normal_roots) > max_direct_normal_roots:
            specs.append({"name": main_group_name, "roots": normal_roots})
        else:
            specs.extend({"name": root, "roots": [root]} for root in normal_roots)

    tidy_roots = [
        root
        for root in (tidy_root_name, "其他/未分类")
        if root in root_hierarchy
    ]
    discovery_count = int(root_hierarchy.get(discovery_root_name, {}).get("count", 0) or 0)
    discovery_grouped_with_tidy = (
        discovery_root_name in root_hierarchy
        and tidy_roots
        and discovery_count < standalone_discovery_min_count
    )
    if discovery_grouped_with_tidy:
        tidy_roots.append(discovery_root_name)
    if tidy_roots:
        specs.append({"name": tidy_root_name, "roots": tidy_roots})

    if discovery_root_name in root_hierarchy and not discovery_grouped_with_tidy:
        specs.append({"name": discovery_root_name, "roots": [discovery_root_name]})

    return specs


def build_display_hierarchy(
    clusterer: BookmarkClusterer,
    root_hierarchy: dict[str, dict],
    root_groups: List[dict],
    display_options: dict,
) -> dict[str, dict]:
    fallback_group_name = display_options.get("fallback_group_name", "实验与杂项")
    discovery_root_name = display_options.get("discovery_root_name", "发现主题")
    tidy_root_name = display_options.get("tidy_root_name", "待整理")
    collapse_single_child = bool(display_options.get("collapse_single_child", True))
    max_depth = int(display_options.get("max_depth", 3))
    display_root_hierarchy = copy.deepcopy(root_hierarchy)
    if tidy_root_name in display_root_hierarchy:
        display_root_hierarchy[tidy_root_name] = restructure_tidy_root_for_display(
            clusterer,
            display_root_hierarchy[tidy_root_name],
            tidy_root_name=tidy_root_name,
            display_options=display_options,
        )
    normal_roots = {
        root
        for root in display_root_hierarchy
        if root not in {discovery_root_name, tidy_root_name, "其他/未分类"}
    }
    legacy_main_group_only = (
        bool(root_groups)
        and display_options.get("grouping_mode") == "auto"
        and len(root_groups) == 1
        and str(root_groups[0].get("name") or "").strip() == str(display_options.get("main_group_name", "主要主题"))
        and set(root_groups[0].get("roots") or []) == normal_roots
    )
    if not root_groups or legacy_main_group_only:
        root_groups = build_auto_root_groups(display_root_hierarchy, display_options)

    specs = [
        {
            "name": item.get("name") or "未命名分组",
            "roots": list(dict.fromkeys(item.get("roots") or [])),
        }
        for item in root_groups
    ]
    assigned_roots = {root for spec in specs for root in spec["roots"]}
    remaining_roots = [
        root
        for root in display_root_hierarchy
        if root not in assigned_roots and root != discovery_root_name
    ]
    if remaining_roots:
        fallback_spec = next((spec for spec in specs if spec["name"] == fallback_group_name), None)
        if fallback_spec is None:
            specs.append({"name": fallback_group_name, "roots": remaining_roots})
        else:
            fallback_spec["roots"] = list(dict.fromkeys(fallback_spec["roots"] + remaining_roots))
    if discovery_root_name in display_root_hierarchy and discovery_root_name not in assigned_roots:
        specs.append({"name": discovery_root_name, "roots": [discovery_root_name]})

    display_hierarchy: dict[str, dict] = {}
    for order, spec in enumerate(specs):
        group_name = spec["name"]
        roots = [root for root in spec["roots"] if root in display_root_hierarchy]
        if not roots:
            continue

        if collapse_single_child and len(roots) == 1:
            payload = copy.deepcopy(display_root_hierarchy[roots[0]])
            payload["name"] = group_name
            payload["category"] = group_name
            payload["display_order"] = order
        elif group_name == tidy_root_name and tidy_root_name in roots:
            payload = copy.deepcopy(display_root_hierarchy[tidy_root_name])
            payload["name"] = group_name
            payload["category"] = group_name
            payload["display_order"] = order
            payload["subcategories"] = dict(payload.get("subcategories") or {})
            for child_order, root_name in enumerate(roots):
                if root_name == tidy_root_name:
                    continue
                child_payload = copy.deepcopy(display_root_hierarchy[root_name])
                child_payload["name"] = root_name
                child_payload["category"] = root_name
                child_payload["display_order"] = child_order
                payload["subcategories"][root_name] = child_payload
            payload["count"] = len(payload.get("bookmarks", [])) + sum(
                item.get("count", 0) for item in payload.get("subcategories", {}).values()
            )
            payload["merge_from_categories"] = list(
                dict.fromkeys(list(payload.get("merge_from_categories") or []) + roots)
            )
            payload["preserve_children"] = True
        else:
            subcategories = {}
            for child_order, root_name in enumerate(roots):
                child_payload = copy.deepcopy(display_root_hierarchy[root_name])
                child_payload["name"] = root_name
                child_payload["category"] = root_name
                child_payload["display_order"] = child_order
                subcategories[root_name] = child_payload
            payload = {
                "name": group_name,
                "category": group_name,
                "subcategories": subcategories,
                "bookmarks": [],
                "count": sum(item.get("count", 0) for item in subcategories.values()),
                "cluster_reason": "按展示分组组合一级目录",
                "representative_tokens": [],
                "source_folder_reused": False,
                "source_folder_quality_score": 0.0,
                "merge_from_categories": roots,
                "display_order": order,
                "preserve_children": True,
            }

        optimized = clusterer.optimize_hierarchy_payload(group_name, payload, is_root=True)
        optimized["display_order"] = order
        display_hierarchy[group_name] = limit_payload_depth(optimized, max_depth)

    return display_hierarchy


def generate_rule_suggestions(
    cluster_profiles: list[dict[str, Any]],
    discovery_root_name: str = "发现主题",
    tidy_root_name: str = "待整理",
    generic_platform_domains: set[str] | None = None,
) -> dict[str, Any]:
    generic_platform_domains = generic_platform_domains or set(DEFAULT_GENERIC_PLATFORM_DOMAINS)
    suggestions: list[dict[str, Any]] = []

    def is_specific_domain_candidate(domain: str) -> bool:
        normalized = (domain or "").strip().strip(".").lower()
        if not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", normalized):
            return False
        labels = normalized.split(".")
        if len(labels) == 2 and labels[1] in {"cn", "uk", "jp", "au"} and labels[0] in {"ac", "co", "com", "edu", "gov", "net", "org"}:
            return False
        return True

    for profile in cluster_profiles:
        size = len(profile.get("bookmarks", []))
        if size < 4:
            continue

        rule_purity = float(profile.get("rule_purity", 0.0) or 0.0)
        destination_root = profile.get("destination_root") or discovery_root_name
        dominant_categories = profile.get("dominant_categories", [])
        target_category = dominant_categories[0]["category"] if dominant_categories else ""
        representative_tokens = profile.get("representative_tokens", []) or profile.get("discovered_topics", [])
        proposed_domains = [
            item["domain"]
            for item in profile.get("top_domains", [])[:3]
            if item.get("domain") and not is_generic_platform_domain(item["domain"], generic_platform_domains)
            and is_specific_domain_candidate(item["domain"])
        ]
        generic_platform_share = max(
            (
                float(item.get("share", 0.0) or 0.0)
                for item in profile.get("top_domains", [])
                if item.get("domain") and is_generic_platform_domain(item["domain"], generic_platform_domains)
            ),
            default=0.0,
        )
        reasons = []
        if generic_platform_share >= 0.45:
            reasons.append("generic_platform_cluster")
        if destination_root == discovery_root_name:
            reasons.append("cluster_fell_back_to_discovery_root")
        if destination_root == tidy_root_name:
            reasons.append("cluster_needs_manual_tidy")
        if float(profile.get("fetch_blocked_share", 0.0) or 0.0) >= 0.5:
            reasons.append("fetch_blocked_cluster")
        if rule_purity < 0.45:
            reasons.append("low_rule_purity")
        if _is_default_or_generic_category(target_category, tidy_root_name):
            reasons.append("generic_existing_category")
        if len(dominant_categories) > 1 and dominant_categories[0].get("share", 0.0) < 0.7:
            reasons.append("mixed_existing_categories")
        if not reasons:
            continue

        if "fetch_blocked_cluster" in reasons:
            suggestion_type = "investigate_fetch_failures"
            target_root = ""
        elif "generic_platform_cluster" in reasons and "mixed_existing_categories" in reasons:
            suggestion_type = "split_mixed_cluster"
            target_root = destination_root if destination_root not in {discovery_root_name, tidy_root_name} else ""
        elif "mixed_existing_categories" in reasons and rule_purity < 0.45:
            suggestion_type = "split_mixed_cluster"
            target_root = destination_root if destination_root not in {discovery_root_name, tidy_root_name} else ""
        elif _is_default_or_generic_category(target_category, tidy_root_name) and representative_tokens:
            suggestion_type = "create_topic"
            target_root = ""
        elif proposed_domains and rule_purity >= 0.45:
            suggestion_type = "add_specific_domain"
            target_root = target_category.split("/")[0] if target_category else ""
        elif target_category and representative_tokens:
            suggestion_type = "add_alias"
            target_root = target_category.split("/")[0]
        else:
            suggestion_type = "demote_noisy_keyword_or_folder_signal"
            target_root = destination_root if destination_root != discovery_root_name else ""

        suggestions.append(
            {
                "cluster_id": profile.get("cluster_id"),
                "cluster_label": profile.get("cluster_label"),
                "size": size,
                "type": suggestion_type,
                "target_category": target_category,
                "target_root": target_root,
                "proposed_keywords": representative_tokens[:6],
                "proposed_domains": proposed_domains,
                "trigger_reasons": reasons,
                "representative_bookmarks": [
                    {
                        "id": bookmark.get("id"),
                        "name": bookmark.get("name"),
                        "url": bookmark.get("url"),
                        "domain": bookmark.get("domain"),
                        "category": bookmark.get("classification", {}).get("category"),
                    }
                    for bookmark in profile.get("bookmarks", [])[:5]
                ],
            }
        )

    suggestions.sort(key=lambda item: (-item["size"], item["cluster_label"]))
    return {
        "count": len(suggestions),
        "summary": dict(Counter(item["type"] for item in suggestions)),
        "suggestions": suggestions,
    }


def generate_quality_report(
    bookmarks: list[dict],
    cluster_profiles: list[dict[str, Any]],
    rule_suggestions: dict[str, Any],
    *,
    root_hierarchy: dict[str, dict] | None = None,
    display_hierarchy: dict[str, dict] | None = None,
    discovery_root_name: str = "发现主题",
    tidy_root_name: str = "待整理",
    generic_platform_domains: set[str] | None = None,
    auto_assign_confidence: float = 0.55,
) -> dict[str, Any]:
    generic_platform_domains = generic_platform_domains or set(DEFAULT_GENERIC_PLATFORM_DOMAINS)
    folder_only_count = 0
    low_confidence_normal_count = 0
    for bookmark in bookmarks:
        classification = bookmark.get("classification", {})
        topic_scores = classification.get("classification_evidence", {}).get("topic_scores", [])
        if topic_scores and all(evidence.get("signal") == "folder" for evidence in topic_scores[0].get("evidence", [])):
            folder_only_count += 1
        if (
            classification.get("category") not in {tidy_root_name, "其他/未分类"}
            and float(classification.get("rule_confidence", 0.0) or 0.0) < auto_assign_confidence
        ):
            low_confidence_normal_count += 1

    generic_suggestion_count = sum(
        1
        for suggestion in rule_suggestions.get("suggestions", [])
        for domain in suggestion.get("proposed_domains", [])
        if is_generic_platform_domain(domain, generic_platform_domains)
    )
    discovery_clusters = [
        profile for profile in cluster_profiles
        if profile.get("destination_root") == discovery_root_name
    ]
    tidy_clusters = [
        profile for profile in cluster_profiles
        if profile.get("destination_root") == tidy_root_name
    ]
    mixed_clusters = [
        profile for profile in cluster_profiles
        if len(profile.get("dominant_categories", [])) > 1
        and profile["dominant_categories"][0].get("share", 0.0) < 0.7
    ]
    generic_platform_clusters = []
    for profile in cluster_profiles:
        generic_share = max(
            (
                float(item.get("share", 0.0) or 0.0)
                for item in profile.get("top_domains", [])
                if item.get("domain") and is_generic_platform_domain(item["domain"], generic_platform_domains)
            ),
            default=0.0,
        )
        if generic_share >= 0.45:
            generic_platform_clusters.append((profile, generic_share))
    review_reason_counts: Counter[str] = Counter()
    review_domain_counts: Counter[str] = Counter()
    review_required_count = 0
    review_required_normal_category_count = 0
    review_required_default_category_count = 0
    for bookmark in bookmarks:
        signal_pack = bookmark.get("signal_pack") or build_signal_pack(bookmark)
        link_health = signal_pack_sections(signal_pack)["health_access"].get("link_health", {})
        if not link_health.get("review_required", False):
            continue
        review_required_count += 1
        reason_code = str(link_health.get("reason_code") or "unknown")
        domain = str(bookmark.get("domain") or urlparse(bookmark.get("url", "")).netloc or "unknown")
        review_reason_counts[reason_code] += 1
        review_domain_counts[domain] += 1
        category = str(bookmark.get("classification", {}).get("category") or "").strip()
        if category and category not in {tidy_root_name, "其他/未分类"}:
            review_required_normal_category_count += 1
        else:
            review_required_default_category_count += 1

    normal_root_direct_bookmark_count = 0
    normal_root_bookmark_count = 0
    flat_normal_roots = []
    for root_name, payload in (root_hierarchy or {}).items():
        if root_name in {discovery_root_name, tidy_root_name}:
            continue
        total = int(payload.get("count", 0) or 0)
        direct = len(payload.get("bookmarks") or [])
        if total <= 0:
            continue
        normal_root_direct_bookmark_count += direct
        normal_root_bookmark_count += total
        direct_share = direct / total
        if total >= 5 and direct_share >= 0.5:
            flat_normal_roots.append(
                {
                    "root": root_name,
                    "count": total,
                    "direct_bookmarks": direct,
                    "direct_share": round(direct_share, 4),
                    "subcategories_count": len(payload.get("subcategories", {})),
                }
            )
    display_root_count = len(display_hierarchy or {})
    normal_top_level_root_count = sum(
        1
        for root_name in (display_hierarchy or {})
        if root_name not in {discovery_root_name, tidy_root_name}
    )
    tidy_visible_group_count = 0
    tidy_small_visible_group_count = 0
    tidy_direct_bookmark_count_after_display = 0
    discovery_grouped_under_tidy = False
    small_discovery_standalone = False
    if display_hierarchy:
        tidy_payload = display_hierarchy.get(tidy_root_name)
        if tidy_payload:
            for bucket_payload in (tidy_payload.get("subcategories") or {}).values():
                tidy_visible_group_count += len(bucket_payload.get("subcategories") or {})
                tidy_direct_bookmark_count_after_display += len(bucket_payload.get("bookmarks") or [])
                tidy_small_visible_group_count += sum(
                    1
                    for child in (bucket_payload.get("subcategories") or {}).values()
                    if int(child.get("count", 0) or 0) <= 2
                )
                if discovery_root_name in (bucket_payload.get("subcategories") or {}):
                    discovery_grouped_under_tidy = True
            if discovery_root_name in (tidy_payload.get("subcategories") or {}):
                discovery_grouped_under_tidy = True
        discovery_payload = display_hierarchy.get(discovery_root_name)
        if discovery_payload and int(discovery_payload.get("count", 0) or 0) < 3:
            small_discovery_standalone = True
    return {
        "metrics": {
            "total_bookmarks": len(bookmarks),
            "folder_only_classification_count": folder_only_count,
            "low_confidence_normal_category_count": low_confidence_normal_count,
            "generic_platform_domain_suggestion_count": generic_suggestion_count,
            "discovery_cluster_count": len(discovery_clusters),
            "tidy_cluster_count": len(tidy_clusters),
            "mixed_cluster_count": len(mixed_clusters),
            "generic_platform_cluster_count": len(generic_platform_clusters),
            "largest_generic_platform_cluster_size": max(
                (len(profile.get("bookmarks", [])) for profile, _ in generic_platform_clusters),
                default=0,
            ),
            "fetch_blocked_discovery_cluster_count": sum(
                1
                for profile in discovery_clusters
                if float(profile.get("fetch_blocked_share", 0.0) or 0.0) >= 0.5
            ),
            "normal_root_direct_bookmark_count": normal_root_direct_bookmark_count,
            "normal_root_direct_bookmark_share": round(
                normal_root_direct_bookmark_count / normal_root_bookmark_count,
                4,
            ) if normal_root_bookmark_count else 0.0,
            "flat_normal_root_count": len(flat_normal_roots),
            "display_top_level_root_count": display_root_count,
            "display_normal_top_level_root_count": normal_top_level_root_count,
            "tidy_visible_group_count": tidy_visible_group_count,
            "tidy_small_visible_group_count": tidy_small_visible_group_count,
            "tidy_direct_bookmark_count_after_display": tidy_direct_bookmark_count_after_display,
            "discovery_grouped_under_tidy": discovery_grouped_under_tidy,
            "small_discovery_root_standalone": small_discovery_standalone,
            "review_required_count": review_required_count,
            "review_required_normal_category_count": review_required_normal_category_count,
            "review_required_default_category_count": review_required_default_category_count,
        },
        "largest_discovery_clusters": [
            {
                "cluster_id": profile.get("cluster_id"),
                "cluster_label": profile.get("cluster_label"),
                "size": len(profile.get("bookmarks", [])),
                "representative_tokens": profile.get("representative_tokens", [])[:6],
            }
            for profile in sorted(discovery_clusters, key=lambda item: -len(item.get("bookmarks", [])))[:10]
        ],
        "largest_tidy_clusters": [
            {
                "cluster_id": profile.get("cluster_id"),
                "cluster_label": profile.get("cluster_label"),
                "size": len(profile.get("bookmarks", [])),
                "representative_tokens": profile.get("representative_tokens", [])[:6],
            }
            for profile in sorted(tidy_clusters, key=lambda item: -len(item.get("bookmarks", [])))[:10]
        ],
        "largest_mixed_clusters": [
            {
                "cluster_id": profile.get("cluster_id"),
                "cluster_label": profile.get("cluster_label"),
                "size": len(profile.get("bookmarks", [])),
                "dominant_categories": profile.get("dominant_categories", [])[:4],
            }
            for profile in sorted(mixed_clusters, key=lambda item: -len(item.get("bookmarks", [])))[:10]
        ],
        "largest_generic_platform_clusters": [
            {
                "cluster_id": profile.get("cluster_id"),
                "cluster_label": profile.get("cluster_label"),
                "size": len(profile.get("bookmarks", [])),
                "generic_platform_share": round(generic_share, 4),
                "top_domains": profile.get("top_domains", [])[:4],
                "dominant_categories": profile.get("dominant_categories", [])[:4],
            }
            for profile, generic_share in sorted(
                generic_platform_clusters,
                key=lambda item: -len(item[0].get("bookmarks", [])),
            )[:10]
        ],
        "largest_fetch_blocked_clusters": [
            {
                "cluster_id": profile.get("cluster_id"),
                "cluster_label": profile.get("cluster_label"),
                "size": len(profile.get("bookmarks", [])),
                "fetch_blocked_share": float(profile.get("fetch_blocked_share", 0.0) or 0.0),
                "top_domains": profile.get("top_domains", [])[:4],
            }
            for profile in sorted(
                (
                    item
                    for item in cluster_profiles
                    if float(item.get("fetch_blocked_share", 0.0) or 0.0) >= 0.5
                ),
                key=lambda item: -len(item.get("bookmarks", [])),
            )[:10]
        ],
        "largest_flat_normal_roots": sorted(
            flat_normal_roots,
            key=lambda item: (-item["direct_bookmarks"], -item["direct_share"], item["root"]),
        )[:10],
        "review_hotspots": {
            "top_reason_codes": [
                {"reason_code": reason_code, "count": count}
                for reason_code, count in review_reason_counts.most_common(10)
            ],
            "top_domains": [
                {"domain": domain, "count": count}
                for domain, count in review_domain_counts.most_common(20)
            ],
        },
    }


def generate_signal_audit(bookmarks: list[dict], clusterer: BookmarkClusterer) -> dict[str, Any]:
    field_stats: dict[str, dict[str, Any]] = {}
    family_stats: dict[str, dict[str, Any]] = {}
    rule_gap_domains: Counter[str] = Counter()
    fetch_route_hotspots: Counter[tuple[str, str]] = Counter()

    for bookmark in bookmarks:
        signal_pack = bookmark.get("signal_pack") or build_signal_pack(bookmark)
        flattened = flatten_signal_pack(signal_pack)
        classification = bookmark.get("classification", {})
        classification_fields = set(classification.get("used_signal_fields", []))
        clustering_fields = set(clusterer.build_feature_set(bookmark).signal_fields)
        impacted_fields = classification_fields | clustering_fields
        domain = str(bookmark.get("domain") or urlparse(bookmark.get("url", "")).netloc or "unknown")
        health_access = signal_pack_sections(signal_pack)["health_access"]
        route = str((health_access.get("fetch_context") or {}).get("route") or "unknown")
        link_health = health_access.get("link_health", {})

        for field in flattened:
            stat = field_stats.setdefault(
                field,
                {
                    "family": field.split(".", 1)[0],
                    "collected_bookmark_count": 0,
                    "classification_used_bookmark_count": 0,
                    "clustering_used_bookmark_count": 0,
                    "decision_impact_bookmark_count": 0,
                },
            )
            stat["collected_bookmark_count"] += 1
        for field in classification_fields:
            stat = field_stats.setdefault(
                field,
                {
                    "family": field.split(".", 1)[0],
                    "collected_bookmark_count": 0,
                    "classification_used_bookmark_count": 0,
                    "clustering_used_bookmark_count": 0,
                    "decision_impact_bookmark_count": 0,
                },
            )
            stat["classification_used_bookmark_count"] += 1
        for field in clustering_fields:
            stat = field_stats.setdefault(
                field,
                {
                    "family": field.split(".", 1)[0],
                    "collected_bookmark_count": 0,
                    "classification_used_bookmark_count": 0,
                    "clustering_used_bookmark_count": 0,
                    "decision_impact_bookmark_count": 0,
                },
            )
            stat["clustering_used_bookmark_count"] += 1
        for field in impacted_fields:
            field_stats[field]["decision_impact_bookmark_count"] += 1

        for family in {item.split(".", 1)[0] for item in flattened}:
            family_stats.setdefault(
                family,
                {
                    "family": family,
                    "collected_bookmark_count": 0,
                    "classification_used_bookmark_count": 0,
                    "clustering_used_bookmark_count": 0,
                    "decision_impact_bookmark_count": 0,
                },
            )["collected_bookmark_count"] += 1
        for family in {item.split(".", 1)[0] for item in classification_fields}:
            family_stats.setdefault(
                family,
                {
                    "family": family,
                    "collected_bookmark_count": 0,
                    "classification_used_bookmark_count": 0,
                    "clustering_used_bookmark_count": 0,
                    "decision_impact_bookmark_count": 0,
                },
            )["classification_used_bookmark_count"] += 1
        for family in {item.split(".", 1)[0] for item in clustering_fields}:
            family_stats.setdefault(
                family,
                {
                    "family": family,
                    "collected_bookmark_count": 0,
                    "classification_used_bookmark_count": 0,
                    "clustering_used_bookmark_count": 0,
                    "decision_impact_bookmark_count": 0,
                },
            )["clustering_used_bookmark_count"] += 1
        for family in {item.split(".", 1)[0] for item in impacted_fields}:
            family_stats[family]["decision_impact_bookmark_count"] += 1

        if classification.get("confirmation_bucket") == "rule_gap":
            rule_gap_domains[domain] += 1
        if link_health.get("review_required"):
            fetch_route_hotspots[(route, domain)] += 1

    unused_high_value_signals = [
        {
            "field": field,
            "collected_bookmark_count": stat["collected_bookmark_count"],
        }
        for field, stat in sorted(
            field_stats.items(),
            key=lambda item: (-item[1]["collected_bookmark_count"], item[0]),
        )
        if stat["collected_bookmark_count"] > 0 and stat["decision_impact_bookmark_count"] == 0
    ][:15]

    return {
        "schema_version": SIGNAL_AUDIT_SCHEMA_VERSION,
        "summary": {
            "total_bookmarks": len(bookmarks),
            "collected_field_count": sum(1 for stat in field_stats.values() if stat["collected_bookmark_count"] > 0),
            "classification_used_field_count": sum(1 for stat in field_stats.values() if stat["classification_used_bookmark_count"] > 0),
            "clustering_used_field_count": sum(1 for stat in field_stats.values() if stat["clustering_used_bookmark_count"] > 0),
            "decision_impact_field_count": sum(1 for stat in field_stats.values() if stat["decision_impact_bookmark_count"] > 0),
            "unused_high_value_signal_count": len(unused_high_value_signals),
            "stale_schema_detected": False,
        },
        "families": sorted(
            family_stats.values(),
            key=lambda item: (-item["decision_impact_bookmark_count"], item["family"]),
        ),
        "fields": sorted(
            [
                {
                    "field": field,
                    **stat,
                }
                for field, stat in field_stats.items()
            ],
            key=lambda item: (-item["decision_impact_bookmark_count"], -item["collected_bookmark_count"], item["field"]),
        ),
        "unused_high_value_signals": unused_high_value_signals,
        "hotspots": {
            "rule_gap_domains": [
                {"domain": domain, "count": count}
                for domain, count in rule_gap_domains.most_common(15)
            ],
            "fetch_route_hotspots": [
                {"route": route, "domain": domain, "count": count}
                for (route, domain), count in fetch_route_hotspots.most_common(20)
            ],
        },
    }


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

    try:
        data = require_payload_schema(
            json.loads(input_file.read_text(encoding="utf-8")),
            CLASSIFIED_OUTPUT_SCHEMA_VERSION,
            "步骤5输入",
            input_file,
        )
    except ValueError as exc:
        print(f"错误: {exc}")
        return 1
    bookmarks = data["bookmarks"]

    options = config.clustering_options
    generic_platform_domains = {
        item.lower()
        for item in options.get("generic_platform_domains", DEFAULT_GENERIC_PLATFORM_DOMAINS)
    }
    clusterer = BookmarkClusterer(
        min_cluster_size=options.get("min_cluster_size", 10),
        max_keywords=options.get("max_keywords", 3),
        max_depth=options.get("max_depth", 3),
        merge_small_nodes_threshold=options.get("merge_small_nodes_threshold"),
        domain_split_min_size=options.get("domain_split_min_size", 5),
        generic_platform_domains=generic_platform_domains,
    )
    discovery_root_name = options.get("discovery_root_name", "发现主题")
    tidy_root_name = options.get("tidy_root_name", options.get("display", {}).get("tidy_root_name", "待整理"))

    cluster_profiles = build_cluster_payloads(
        clusterer,
        bookmarks,
        threshold=options.get("max_bookmarks_without_clustering", 20),
        discovery_root_name=discovery_root_name,
        tidy_root_name=tidy_root_name,
    )
    raw_hierarchy = build_clustered_root_hierarchy(
        clusterer,
        cluster_profiles,
        threshold=options.get("max_bookmarks_without_clustering", 20),
        discovery_root_name=discovery_root_name,
        tidy_root_name=tidy_root_name,
    )

    hierarchy = build_display_hierarchy(
        clusterer,
        raw_hierarchy,
        options.get("root_groups", []),
        options.get("display", {}),
    )

    review_groups: dict[str, list[dict]] = defaultdict(list)
    for bookmark in bookmarks:
        classification = bookmark.get("classification", {})
        if classification.get("review_required"):
            review_groups[classification.get("review_category") or "其他抓取异常"].append(bookmark)

    review_hierarchy = {}
    if review_groups:
        review_hierarchy["待审阅"] = {
            "category": "待审阅",
            "subcategories": {
                f"待审阅/{reason}": {
                    "bookmarks": group,
                    "count": len(group),
                    "cluster_reason": f"按异常原因归档: {reason}",
                    "representative_tokens": clusterer._representative_tokens(group),
                    "source_folder_reused": False,
                    "source_folder_quality_score": 0.0,
                    "merge_from_categories": sorted({bookmark.get("classification", {}).get("category", "") for bookmark in group if bookmark.get("classification")}),
                }
                for reason, group in sorted(review_groups.items())
            },
            "bookmarks": [],
            "count": sum(len(group) for group in review_groups.values()),
            "cluster_reason": "将抓取异常书签镜像到统一待审阅目录",
            "representative_tokens": [],
            "source_folder_reused": False,
            "source_folder_quality_score": 0.0,
            "merge_from_categories": sorted({bookmark.get("classification", {}).get("category", "") for group in review_groups.values() for bookmark in group if bookmark.get("classification")}),
        }

    rule_suggestions = generate_rule_suggestions(
        cluster_profiles,
        discovery_root_name=discovery_root_name,
        tidy_root_name=tidy_root_name,
        generic_platform_domains=generic_platform_domains,
    )
    ensure_parent(config.paths.rule_suggestions_report_file)
    config.paths.rule_suggestions_report_file.write_text(
        json.dumps(rule_suggestions, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    quality_report = generate_quality_report(
        bookmarks,
        cluster_profiles,
        rule_suggestions,
        root_hierarchy=raw_hierarchy,
        display_hierarchy=hierarchy,
        discovery_root_name=discovery_root_name,
        tidy_root_name=tidy_root_name,
        generic_platform_domains=generic_platform_domains,
        auto_assign_confidence=float(config.classification_options.get("auto_assign_confidence", 0.55)),
    )
    ensure_parent(config.paths.quality_report_file)
    config.paths.quality_report_file.write_text(
        json.dumps(quality_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    signal_audit = generate_signal_audit(bookmarks, clusterer)
    ensure_parent(config.paths.signal_audit_report_file)
    config.paths.signal_audit_report_file.write_text(
        json.dumps(signal_audit, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    compact_hierarchy = compact_hierarchy_map(hierarchy)
    compact_raw_hierarchy = compact_hierarchy_map(raw_hierarchy)
    compact_review_hierarchy = compact_hierarchy_map(review_hierarchy)
    output = {
        "schema_version": CLUSTERING_OUTPUT_SCHEMA_VERSION,
        "hierarchy": compact_hierarchy,
        "raw_hierarchy": compact_raw_hierarchy,
        "review_hierarchy": compact_review_hierarchy,
        "bookmark_payload_mode": "compact",
        "stats": {
            "total_categories": len(compact_hierarchy),
            "category_sizes": {category: item["count"] for category, item in compact_hierarchy.items()},
            "subcategories_count": sum(len(item["subcategories"]) for item in compact_hierarchy.values()),
            "cluster_count": len(cluster_profiles),
            "discovery_cluster_count": sum(1 for item in cluster_profiles if item.get("destination_root") == discovery_root_name),
            "tidy_cluster_count": sum(1 for item in cluster_profiles if item.get("destination_root") == tidy_root_name),
            "review_categories": {category: item["count"] for category, item in compact_review_hierarchy.items()},
            "rule_suggestions_count": rule_suggestions["count"],
            "quality_metrics": quality_report["metrics"],
        },
    }
    ensure_parent(output_file)
    output_file.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info("步骤5完成: %s -> %s", input_file, output_file)
    print(f"✓ 聚类完成: {output_file}")
    print(f"  分类数: {output['stats']['total_categories']}")
    print(f"  含子分类的分类数: {output['stats']['subcategories_count']}")
    print(f"  聚类簇数: {output['stats']['cluster_count']}")
    print(f"  发现主题簇数: {output['stats']['discovery_cluster_count']}")
    print(f"  待整理簇数: {output['stats']['tidy_cluster_count']}")
    print(f"  规则建议数: {output['stats']['rule_suggestions_count']}")
    print(f"  质量报告: {config.paths.quality_report_file}")
    print(f"  信号审计: {config.paths.signal_audit_report_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

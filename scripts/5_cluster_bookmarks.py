#!/usr/bin/env python3
"""步骤5: 聚类分析与层级构建。"""
from __future__ import annotations

import copy
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence
from urllib.parse import urlparse

from common import build_parser, configure_logging, ensure_parent, load_config_from_args


def metadata_cluster_text(metadata: dict) -> str:
    page = metadata.get("page_signals", {})
    site = metadata.get("site_signals", {})
    profile = metadata.get("site_profile", {})
    page_profile = profile.get("page", {}) if isinstance(profile, dict) else {}
    site_profile = profile.get("site", {}) if isinstance(profile, dict) else {}
    parts = [
        metadata.get("title") or page.get("title") or page_profile.get("title") or "",
        metadata.get("description") or page.get("description") or page_profile.get("description") or "",
        metadata.get("keywords") or page.get("keywords") or page_profile.get("keywords") or "",
        " ".join(page.get("page_type_hints") or page_profile.get("page_type_hints") or []),
        site.get("site_name") or site_profile.get("site_name") or "",
        " ".join(site.get("brand_terms") or site_profile.get("brand_terms") or []),
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


@dataclass
class BookmarkFeatures:
    topics: set[str]
    resource_types: set[str]
    domain_tokens: set[str]
    path_tokens: set[str]
    text_tokens: set[str]
    cluster_hints: set[str]
    folder_tokens: set[str]
    page_type_hints: set[str]
    site_name_tokens: set[str]
    quality_signals: set[str]
    primary_topic: str
    resource_type: str
    domain: str
    registered_domain: str
    rule_roots: set[str]
    rule_root_weights: dict[str, float]
    rule_confidence: float
    original_folders: Sequence[str]


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
    ):
        self.min_cluster_size = min_cluster_size
        self.max_keywords = max_keywords
        self.max_depth = max_depth
        self.merge_small_nodes_threshold = merge_small_nodes_threshold or max(2, min_cluster_size // 2)
        self.domain_split_min_size = domain_split_min_size
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
            bookmark_category = bookmark.get("classification", {}).get("category", "")
            leaf = self._category_leaf_label(bookmark_category, root_category)
            if leaf and not self._is_generic_label(leaf, category):
                counter[leaf] += 1
        return counter.most_common(1)[0][0] if counter else ""

    def _dominant_site_label(self, bookmarks: List[dict], category: str) -> str:
        counter: Counter[str] = Counter()
        for bookmark in bookmarks:
            metadata = bookmark.get("metadata", {})
            candidates = [
                metadata.get("site_signals", {}).get("site_name"),
                metadata.get("page_signals", {}).get("og:site_name"),
                metadata.get("site_profile", {}).get("site", {}).get("site_name"),
            ]
            for candidate in candidates:
                label = self._human_label(candidate or "")
                if label and not self._is_generic_label(label, category):
                    counter[label] += 1
                    break
        return counter.most_common(1)[0][0] if counter else ""

    def _dominant_domain_label(self, bookmarks: List[dict]) -> str:
        counter: Counter[str] = Counter()
        for bookmark in bookmarks:
            domain = self.build_feature_set(bookmark).registered_domain
            label = self._domain_display_name(domain)
            if label:
                counter[label] += 1
        return counter.most_common(1)[0][0] if counter else ""

    def _dominant_hint_label(self, bookmarks: List[dict], category: str = "") -> str:
        counter: Counter[str] = Counter()
        for bookmark in bookmarks:
            classification = bookmark.get("classification", {})
            for candidate in classification.get("cluster_hints", [])[:4]:
                label = self._human_label(candidate or "")
                if label and not self._is_generic_label(label, category):
                    counter[label] += 1
        return counter.most_common(1)[0][0] if counter else ""

    def _cluster_display_name(
        self,
        bookmarks: List[dict],
        category: str,
        best_folder: Sequence[str] | None = None,
        folder_quality: float = 0.0,
    ) -> str:
        if best_folder and folder_quality >= 0.68:
            folder_label = self._folder_display_label(best_folder, category)
            if folder_label:
                return folder_label

        for candidate in (
            self._dominant_hint_label(bookmarks, category),
            self._dominant_site_label(bookmarks, category),
            self._dominant_domain_label(bookmarks),
            self._dominant_category_leaf(bookmarks, category),
        ):
            if candidate and not self._is_generic_label(candidate, category):
                return candidate

        for token in self._representative_tokens(bookmarks, limit=4):
            candidate = self.clean_topic_token(token)
            if candidate and not self._is_generic_label(candidate, category):
                return candidate

        return "其他"

    def extract_keywords(self, text: str) -> List[str]:
        normalized = text.lower().replace("-", " ").replace("_", " ")
        normalized = re.sub(r"[^\w\s\u4e00-\u9fff]", " ", normalized)
        return [word for word in normalized.split() if word not in STOPWORDS and len(word) > 1]

    def _tokenize_path(self, url: str) -> tuple[set[str], set[str]]:
        parsed = urlparse(url)
        path_tokens = set(self.extract_keywords(parsed.path.replace("/", " ")))
        hint_tokens = {URL_TYPE_HINTS[token] for token in path_tokens if token in URL_TYPE_HINTS}
        return path_tokens, hint_tokens

    def _collect_topics(self, bookmark: dict, title_tokens: Iterable[str], folder_tokens: Iterable[str], path_tokens: Iterable[str]) -> tuple[set[str], str]:
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

        candidates = sorted(title_tokens) + sorted(folder_tokens) + sorted(path_tokens)
        topical_tokens = [token for token in candidates if token not in URL_TYPE_HINTS and token not in STOPWORDS]
        if topical_tokens:
            topical_counter = Counter(topical_tokens)
            stable_topical_tokens = sorted(topical_counter.items(), key=lambda item: (-item[1], item[0]))[:4]
            topics.update(token for token, _ in stable_topical_tokens)

        if rule_confidence >= 0.75 and rule_candidates:
            primary = rule_candidates[0]["category"]
        elif open_candidates:
            primary = open_candidates[0]["topic"]
        elif category:
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
        metadata = bookmark.get("metadata", {})
        domain = (bookmark.get("domain") or urlparse(bookmark.get("url", "")).netloc).lower()
        registered_domain = self._registered_domain(domain) if domain else ""
        path_tokens, page_type_hints = self._tokenize_path(bookmark.get("url", ""))
        title_tokens = set(self.extract_keywords(" ".join([
            bookmark.get("name", ""),
            metadata.get("title", ""),
            metadata.get("h1", ""),
            metadata.get("description", ""),
            metadata.get("keywords", ""),
        ])))
        folder_tokens = set(self.extract_keywords(" ".join(bookmark.get("original_folder_path", []))))
        site_signals = metadata.get("site_signals", {})
        site_profile = metadata.get("site_profile", {})
        site_profile_block = site_profile.get("site", {}) if isinstance(site_profile, dict) else {}
        site_name_tokens = set(
            self.extract_keywords(
                " ".join(
                    part
                    for part in (
                        domain.replace(".", " "),
                        site_signals.get("site_name", ""),
                        " ".join(site_signals.get("brand_terms") or []),
                        site_profile_block.get("site_name", ""),
                        " ".join(site_profile_block.get("brand_terms") or []),
                    )
                    if part
                )
            )
        )
        topics, primary_topic = self._collect_topics(bookmark, title_tokens, folder_tokens, path_tokens)
        cluster_hint_tokens = set(self.extract_keywords(" ".join(classification.get("cluster_hints", []) or [])))

        resource_type_candidates = set()
        explicit_type = classification.get("resource_type") or metadata.get("resource_type") or bookmark.get("resource_type")
        if explicit_type:
            resource_type_candidates.add(self._normalize_resource_type(explicit_type))
        resource_type_candidates.update(page_type_hints)
        if not resource_type_candidates:
            text = " ".join([bookmark.get("name", ""), metadata.get("title", ""), metadata.get("description", "")]).lower()
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

        text_tokens = title_tokens | set(self.extract_keywords(metadata.get("content_preview", "")))
        domain_tokens = set(self.extract_keywords(domain.replace(".", " ")))
        quality_signals = {
            self._human_label(str(signal))
            for signal in (classification.get("quality_signals") or [])
            if self._human_label(str(signal))
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

        feature = BookmarkFeatures(
            topics=topics,
            resource_types=resource_type_candidates,
            domain_tokens=domain_tokens,
            path_tokens=path_tokens,
            text_tokens=text_tokens,
            cluster_hints=cluster_hint_tokens,
            folder_tokens=folder_tokens,
            page_type_hints=page_type_hints,
            site_name_tokens=site_name_tokens,
            quality_signals=quality_signals,
            primary_topic=primary_topic,
            resource_type=Counter(resource_type_candidates).most_common(1)[0][0],
            domain=domain,
            registered_domain=registered_domain,
            rule_roots=set(rule_root_weights),
            rule_root_weights=rule_root_weights,
            rule_confidence=float(classification.get("rule_confidence", 0.0) or 0.0),
            original_folders=bookmark.get("original_folder_path", []),
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
        rule_candidate_overlap = 0.0
        all_roots = left.rule_roots | right.rule_roots
        if all_roots:
            overlap = sum(min(left.rule_root_weights.get(root, 0.0), right.rule_root_weights.get(root, 0.0)) for root in all_roots)
            union = sum(max(left.rule_root_weights.get(root, 0.0), right.rule_root_weights.get(root, 0.0)) for root in all_roots)
            rule_candidate_overlap = overlap / union if union else 0.0
        folder_similarity = 1.0 if left.folder_tokens and left.folder_tokens == right.folder_tokens else self._jaccard(left.folder_tokens, right.folder_tokens)
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
            conflict_penalty = 0.15
        if (
            same_registered_domain
            and not resource_type_match
            and text_similarity < 0.25
            and hint_similarity < 0.15
        ):
            conflict_penalty = max(conflict_penalty, 0.18)
        return {
            "topic_overlap": topic_overlap,
            "resource_type_match": resource_type_match,
            "domain_similarity": domain_similarity,
            "text_similarity": text_similarity,
            "hint_similarity": hint_similarity,
            "rule_candidate_overlap": rule_candidate_overlap,
            "folder_similarity": folder_similarity,
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
            + metrics["folder_similarity"] * 0.05
            + metrics["quality_signal_bonus"] * 0.10
            + metrics["cross_domain_primary_bonus"] * 0.20
            - metrics["conflict_penalty"]
        )
        if metrics["hint_similarity"] < 0.2 and metrics["text_similarity"] < 0.15:
            score *= 0.6
        return max(0.0, min(score, 1.0))

    @staticmethod
    def _feature_overlap_tokens(feature: BookmarkFeatures) -> set[str]:
        return feature.topics | feature.cluster_hints | feature.path_tokens | feature.folder_tokens

    def _comparison_bucket_keys(self, feature: BookmarkFeatures) -> set[str]:
        keys = set()
        if feature.primary_topic:
            keys.add(f"primary:{self.clean_topic_token(feature.primary_topic)}")
        if feature.registered_domain:
            keys.add(f"domain:{feature.registered_domain}")
        for token in sorted(feature.site_name_tokens)[:2]:
            keys.add(f"site:{token}")
        for token in sorted(feature.cluster_hints)[:4]:
            keys.add(f"hint:{token}")
        for root, weight in sorted(feature.rule_root_weights.items(), key=lambda item: (-item[1], item[0]))[:2]:
            if feature.rule_confidence >= 0.75 and weight > 0:
                keys.add(f"root:{root}:{feature.resource_type}")
        if feature.resource_type:
            keys.add(f"type:{feature.resource_type}")
        for token in sorted(feature.folder_tokens)[:2]:
            keys.add(f"folder:{token}")
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
            if key in STOPWORDS or key in seen:
                continue
            seen.add(key)
            parts.append(part)
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

        while len(node["children"]) == 1 and not node.get("bookmarks") and not is_root:
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
            text = " ".join([bookmark.get("name", ""), metadata_cluster_text(bookmark.get("metadata", {}))])
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
        combined_folder_tokens = set().union(*(feature.folder_tokens for feature in features)) if features else set()
        combined_hints = set().union(*(feature.page_type_hints for feature in features)) if features else set()
        combined_site_name = set().union(*(feature.site_name_tokens for feature in features)) if features else set()
        combined_quality_signals = set().union(*(feature.quality_signals for feature in features)) if features else set()
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
            folder_tokens=combined_folder_tokens,
            page_type_hints=combined_hints,
            site_name_tokens=combined_site_name,
            quality_signals=combined_quality_signals,
            primary_topic=topic,
            resource_type=resource_type,
            domain=domain[0][0] if domain else "",
            registered_domain=registered[0][0] if registered else "",
            rule_roots=set(rule_root_weights),
            rule_root_weights=rule_root_weights,
            rule_confidence=round(sum(feature.rule_confidence for feature in features) / len(features), 3) if features else 0.0,
            original_folders=[],
        )

    def _cluster_summary_similarity(self, left: BookmarkFeatures, right: BookmarkFeatures) -> float:
        return self.similarity_score(left, right)

    def _folder_quality_score(self, bookmarks: List[dict]) -> float:
        if not bookmarks:
            return 0.0
        features = [self.build_feature_set(bookmark) for bookmark in bookmarks]
        consistency = self._cluster_consistency(features)
        folder_paths = [tuple(bookmark.get("original_folder_path", [])) for bookmark in bookmarks if bookmark.get("original_folder_path")]
        folder_purity = Counter(folder_paths).most_common(1)[0][1] / len(bookmarks) if folder_paths else 0.0
        return round(consistency * 0.8 + folder_purity * 0.2, 3)

    def _representative_tokens(self, bookmarks: List[dict], limit: int = 6) -> List[str]:
        counter: Counter[str] = Counter()
        for bookmark in bookmarks:
            features = self.build_feature_set(bookmark)
            counter.update(features.topics)
            counter.update(features.text_tokens)
            counter.update(features.path_tokens)
        return [token for token, _ in counter.most_common(limit)]

    def _merge_named_subcategory(self, subcategories: Dict[str, Dict], name: str, payload: Dict) -> None:
        existing = subcategories.get(name)
        if existing is None:
            subcategories[name] = payload
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
            if key:
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


def build_cluster_payloads(
    clusterer: BookmarkClusterer,
    bookmarks: List[dict],
    threshold: int,
    discovery_root_name: str = "发现主题",
) -> list[dict[str, Any]]:
    cluster_groups = clusterer.cluster_bookmarks(bookmarks)
    payloads: list[dict[str, Any]] = []
    for cluster in cluster_groups:
        dominant_rule_roots, dominant_categories, discovered_topics, top_domains = _cluster_root_distribution(cluster)
        folder_counter = Counter(tuple(bookmark.get("original_folder_path", [])) for bookmark in cluster if bookmark.get("original_folder_path"))
        best_folder = list(folder_counter.most_common(1)[0][0]) if folder_counter else []
        folder_quality = clusterer._folder_quality_score(cluster)
        category_hint = (
            dominant_categories[0]["category"]
            if dominant_categories else
            (dominant_rule_roots[0]["root"] if dominant_rule_roots else discovery_root_name)
        )
        cluster_label = clusterer._cluster_display_name(cluster, category_hint, best_folder, folder_quality)
        dominant_leaf = (
            clusterer._category_leaf_label(dominant_categories[0]["category"], dominant_categories[0]["root"])
            if dominant_categories else ""
        )
        if dominant_leaf and dominant_categories and dominant_categories[0]["share"] >= 0.6:
            cluster_label = dominant_leaf
        if len(cluster) == 1 and dominant_categories:
            if dominant_leaf:
                cluster_label = dominant_leaf
        if clusterer._is_generic_label(cluster_label, category_hint) and discovered_topics:
            cluster_label = clusterer._human_label(discovered_topics[0]) or cluster_label

        top_support = dominant_rule_roots[0]["support"] if dominant_rule_roots else 0.0
        second_support = dominant_rule_roots[1]["support"] if len(dominant_rule_roots) > 1 else 0.0
        fallback_root = dominant_categories[0]["root"] if dominant_categories else discovery_root_name
        rule_purity = round(
            top_support if dominant_rule_roots else (dominant_categories[0]["share"] if dominant_categories else 0.0),
            4,
        )
        if len(cluster) == 1 and dominant_categories:
            destination_root = dominant_categories[0]["root"]
        elif not dominant_rule_roots:
            destination_root = fallback_root if dominant_categories and dominant_categories[0]["share"] >= 0.5 else discovery_root_name
        elif rule_purity < 0.45 or top_support - second_support < 0.1:
            destination_root = discovery_root_name
        else:
            destination_root = dominant_rule_roots[0]["root"]

        payloads.append(
            {
                "cluster_id": "",
                "cluster_label": cluster_label,
                "cluster_reason": clusterer._build_cluster_reason(cluster),
                "rule_purity": rule_purity,
                "dominant_rule_roots": dominant_rule_roots,
                "dominant_categories": dominant_categories,
                "discovered_topics": discovered_topics,
                "top_domains": top_domains,
                "destination_root": destination_root,
                "representative_tokens": clusterer._representative_tokens(cluster),
                "source_folder_reused": bool(best_folder and folder_quality >= 0.68),
                "source_folder_quality_score": folder_quality,
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
        item["cluster_id"] = f"cluster_{index:04d}"
    return ordered_payloads


def build_clustered_root_hierarchy(
    clusterer: BookmarkClusterer,
    cluster_profiles: list[dict[str, Any]],
    threshold: int,
    discovery_root_name: str = "发现主题",
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
        should_inline_small_pure_cluster = (
            root_name != discovery_root_name
            and profile.get("size_below_threshold", False)
            and dominant_share >= 0.8
            and len(profile["bookmarks"]) <= threshold
        )
        use_subcategory = (
            not should_inline_small_pure_cluster
            and (
                root_name == discovery_root_name
                or len(profile["bookmarks"]) > 1
                or (leaf_name and not clusterer._is_generic_label(leaf_name, root_name))
            )
        )
        if use_subcategory:
            subcategory_name = leaf_name if len(profile["bookmarks"]) == 1 and leaf_name else display_name
            payload = {
                "name": subcategory_name,
                "bookmarks": profile["bookmarks"],
                "count": len(profile["bookmarks"]),
                "cluster_reason": profile["cluster_reason"],
                "representative_tokens": profile["representative_tokens"],
                "source_folder_reused": profile["source_folder_reused"],
                "source_folder_quality_score": profile["source_folder_quality_score"],
                "merge_from_categories": sorted(
                    {
                        item["category"]
                        for item in profile.get("dominant_categories", [])
                        if item.get("category")
                    }
                ),
                "dominant_rule_roots": profile.get("dominant_rule_roots", []),
                "discovered_topics": profile.get("discovered_topics", []),
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
) -> dict[str, dict]:
    cluster_profiles = build_cluster_payloads(
        clusterer,
        bookmarks,
        threshold=threshold,
        discovery_root_name=discovery_root_name,
    )
    return build_clustered_root_hierarchy(
        clusterer,
        cluster_profiles,
        threshold=threshold,
        discovery_root_name=discovery_root_name,
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


def build_display_hierarchy(
    clusterer: BookmarkClusterer,
    root_hierarchy: dict[str, dict],
    root_groups: List[dict],
    display_options: dict,
) -> dict[str, dict]:
    fallback_group_name = display_options.get("fallback_group_name", "实验与杂项")
    discovery_root_name = display_options.get("discovery_root_name", "发现主题")
    collapse_single_child = bool(display_options.get("collapse_single_child", True))
    max_depth = int(display_options.get("max_depth", 3))

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
        for root in root_hierarchy
        if root not in assigned_roots and root != discovery_root_name
    ]
    if remaining_roots:
        fallback_spec = next((spec for spec in specs if spec["name"] == fallback_group_name), None)
        if fallback_spec is None:
            specs.append({"name": fallback_group_name, "roots": remaining_roots})
        else:
            fallback_spec["roots"] = list(dict.fromkeys(fallback_spec["roots"] + remaining_roots))
    if discovery_root_name in root_hierarchy and discovery_root_name not in assigned_roots:
        specs.append({"name": discovery_root_name, "roots": [discovery_root_name]})

    display_hierarchy: dict[str, dict] = {}
    for order, spec in enumerate(specs):
        group_name = spec["name"]
        roots = [root for root in spec["roots"] if root in root_hierarchy]
        if not roots:
            continue

        if collapse_single_child and len(roots) == 1:
            payload = copy.deepcopy(root_hierarchy[roots[0]])
            payload["name"] = group_name
            payload["category"] = group_name
            payload["display_order"] = order
        else:
            subcategories = {}
            for child_order, root_name in enumerate(roots):
                child_payload = copy.deepcopy(root_hierarchy[root_name])
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
) -> dict[str, Any]:
    suggestions: list[dict[str, Any]] = []
    for profile in cluster_profiles:
        size = len(profile.get("bookmarks", []))
        if size < 4:
            continue

        rule_purity = float(profile.get("rule_purity", 0.0) or 0.0)
        destination_root = profile.get("destination_root") or discovery_root_name
        dominant_categories = profile.get("dominant_categories", [])
        target_category = dominant_categories[0]["category"] if dominant_categories else ""
        representative_tokens = profile.get("representative_tokens", []) or profile.get("discovered_topics", [])
        proposed_domains = [item["domain"] for item in profile.get("top_domains", [])[:3] if item.get("domain")]
        reasons = []
        if destination_root == discovery_root_name:
            reasons.append("cluster_fell_back_to_discovery_root")
        if rule_purity < 0.45:
            reasons.append("low_rule_purity")
        if target_category in {"其他/未分类", "其他"} or target_category.endswith("/其他") or target_category.endswith("/未分类"):
            reasons.append("generic_existing_category")
        if not reasons:
            continue

        if target_category and proposed_domains and rule_purity >= 0.25:
            suggestion_type = "add_domain_to_existing_category"
            target_root = target_category.split("/")[0]
        elif target_category:
            suggestion_type = "add_keywords_to_existing_category"
            target_root = target_category.split("/")[0]
        elif proposed_domains or representative_tokens:
            suggestion_type = "create_new_leaf_category"
            target_root = destination_root if destination_root != discovery_root_name else ""
        else:
            suggestion_type = "demote_noisy_keyword_or_folder_signal"
            target_root = destination_root

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
    bookmarks = data["bookmarks"]

    options = config.clustering_options
    clusterer = BookmarkClusterer(
        min_cluster_size=options.get("min_cluster_size", 10),
        max_keywords=options.get("max_keywords", 3),
        max_depth=options.get("max_depth", 3),
        merge_small_nodes_threshold=options.get("merge_small_nodes_threshold"),
        domain_split_min_size=options.get("domain_split_min_size", 5),
    )
    discovery_root_name = options.get("discovery_root_name", "发现主题")

    cluster_profiles = build_cluster_payloads(
        clusterer,
        bookmarks,
        threshold=options.get("max_bookmarks_without_clustering", 20),
        discovery_root_name=discovery_root_name,
    )
    raw_hierarchy = build_clustered_root_hierarchy(
        clusterer,
        cluster_profiles,
        threshold=options.get("max_bookmarks_without_clustering", 20),
        discovery_root_name=discovery_root_name,
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
    )
    ensure_parent(config.paths.rule_suggestions_report_file)
    config.paths.rule_suggestions_report_file.write_text(
        json.dumps(rule_suggestions, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    output = {
        "hierarchy": hierarchy,
        "raw_hierarchy": raw_hierarchy,
        "review_hierarchy": review_hierarchy,
        "stats": {
            "total_categories": len(hierarchy),
            "category_sizes": {category: item["count"] for category, item in hierarchy.items()},
            "subcategories_count": sum(len(item["subcategories"]) for item in hierarchy.values()),
            "cluster_count": len(cluster_profiles),
            "discovery_cluster_count": sum(1 for item in cluster_profiles if item.get("destination_root") == discovery_root_name),
            "review_categories": {category: item["count"] for category, item in review_hierarchy.items()},
            "rule_suggestions_count": rule_suggestions["count"],
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
    print(f"  规则建议数: {output['stats']['rule_suggestions_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

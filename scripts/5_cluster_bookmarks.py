#!/usr/bin/env python3
"""步骤5: 聚类分析与层级构建。"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence
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


@dataclass
class BookmarkFeatures:
    topics: set[str]
    resource_types: set[str]
    domain_tokens: set[str]
    path_tokens: set[str]
    text_tokens: set[str]
    folder_tokens: set[str]
    page_type_hints: set[str]
    site_name_tokens: set[str]
    primary_topic: str
    resource_type: str
    domain: str
    registered_domain: str
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
        if category:
            topics.update(self.extract_keywords(category.replace("/", " ")))
        all_scores = classification.get("all_scores", {})
        top_scores = sorted(all_scores.items(), key=lambda item: (-item[1].get("total", 0), item[0]))[:2]
        for category_name, _ in top_scores:
            topics.update(self.extract_keywords(category_name.replace("/", " ")))
        candidates = sorted(title_tokens) + sorted(folder_tokens) + sorted(path_tokens)
        topical_tokens = [token for token in candidates if token not in URL_TYPE_HINTS and token not in STOPWORDS]
        if topical_tokens:
            topical_counter = Counter(topical_tokens)
            stable_topical_tokens = sorted(topical_counter.items(), key=lambda item: (-item[1], item[0]))[:4]
            topics.update(token for token, _ in stable_topical_tokens)
        primary = top_scores[0][0] if top_scores else category
        return topics, primary or "其他/未分类"

    def build_feature_set(self, bookmark: dict) -> BookmarkFeatures:
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
        site_name_tokens = set(self.extract_keywords(domain.replace(".", " ")))
        topics, primary_topic = self._collect_topics(bookmark, title_tokens, folder_tokens, path_tokens)

        resource_type_candidates = set()
        explicit_type = metadata.get("resource_type") or bookmark.get("resource_type")
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
        return BookmarkFeatures(
            topics=topics,
            resource_types=resource_type_candidates,
            domain_tokens=domain_tokens,
            path_tokens=path_tokens,
            text_tokens=text_tokens,
            folder_tokens=folder_tokens,
            page_type_hints=page_type_hints,
            site_name_tokens=site_name_tokens,
            primary_topic=primary_topic,
            resource_type=Counter(resource_type_candidates).most_common(1)[0][0],
            domain=domain,
            registered_domain=registered_domain,
            original_folders=bookmark.get("original_folder_path", []),
        )

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
        domain_similarity = max(same_domain, 0.7 * same_registered_domain, self._jaccard(left.site_name_tokens, right.site_name_tokens) * 0.6)
        text_similarity = max(
            self._jaccard(left.text_tokens, right.text_tokens),
            self._jaccard(left.path_tokens | left.text_tokens, right.path_tokens | right.text_tokens),
        )
        folder_bonus = 0.2 if left.folder_tokens and left.folder_tokens == right.folder_tokens else 0.0
        return {
            "topic_overlap": topic_overlap,
            "resource_type_match": resource_type_match,
            "domain_similarity": domain_similarity,
            "text_similarity": text_similarity,
            "folder_bonus": folder_bonus,
        }

    def similarity_score(self, left: BookmarkFeatures, right: BookmarkFeatures) -> float:
        metrics = self.similarity_breakdown(left, right)
        score = (
            metrics["topic_overlap"] * 0.45
            + metrics["resource_type_match"] * 0.05
            + metrics["domain_similarity"] * 0.10
            + metrics["text_similarity"] * 0.35
            + metrics["folder_bonus"] * 0.05
        )
        if metrics["topic_overlap"] < 0.2 and metrics["text_similarity"] < 0.15:
            score *= 0.6
        return score

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
            current_type = node.get("node_type", "mixed")
            child_type = only_child.get("node_type", current_type)
            if current_type == "reference" and child_type != "reference":
                break
            node["name"] = only_child["name"]
            node["node_type"] = child_type
            node["children"] = only_child.get("children", [])
            node["bookmarks"] = only_child.get("bookmarks", [])

        return self.refresh_count(node)

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
        for left in range(len(bookmarks)):
            for right in range(left + 1, len(bookmarks)):
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
        type_purity = Counter(feature.resource_type for feature in features).most_common(1)[0][1] / len(features)
        domain_concentration = Counter(feature.registered_domain for feature in features if feature.registered_domain).most_common(1)
        domain_score = domain_concentration[0][1] / len(features) if domain_concentration else 0.0
        mean_text_overlap = 0.0
        comparisons = 0
        for left in range(len(features)):
            for right in range(left + 1, len(features)):
                mean_text_overlap += self._jaccard(features[left].text_tokens | features[left].topics, features[right].text_tokens | features[right].topics)
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
        combined_folder_tokens = set().union(*(feature.folder_tokens for feature in features)) if features else set()
        combined_hints = set().union(*(feature.page_type_hints for feature in features)) if features else set()
        combined_site_name = set().union(*(feature.site_name_tokens for feature in features)) if features else set()
        topic = Counter(feature.primary_topic for feature in features).most_common(1)[0][0] if features else "其他/未分类"
        resource_type = Counter(feature.resource_type for feature in features).most_common(1)[0][0] if features else "未知"
        domain = Counter(feature.domain for feature in features if feature.domain).most_common(1)
        registered = Counter(feature.registered_domain for feature in features if feature.registered_domain).most_common(1)
        return BookmarkFeatures(
            topics=combined_topics,
            resource_types=combined_types,
            domain_tokens=combined_domain_tokens,
            path_tokens=combined_path_tokens,
            text_tokens=combined_text_tokens,
            folder_tokens=combined_folder_tokens,
            page_type_hints=combined_hints,
            site_name_tokens=combined_site_name,
            primary_topic=topic,
            resource_type=resource_type,
            domain=domain[0][0] if domain else "",
            registered_domain=registered[0][0] if registered else "",
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

    def _derive_cluster_label(self, bookmarks: List[dict], fallback_category: str) -> str:
        features = [self.build_feature_set(bookmark) for bookmark in bookmarks]
        topic = Counter(feature.primary_topic for feature in features if feature.primary_topic).most_common(1)
        if topic and topic[0][0]:
            return topic[0][0]
        tokens = self._representative_tokens(bookmarks, limit=2)
        if tokens:
            return f"{fallback_category}/{'-'.join(tokens[:2])}"
        return fallback_category

    def _build_cluster_reason(self, bookmarks: List[dict]) -> str:
        features = [self.build_feature_set(bookmark) for bookmark in bookmarks]
        topic = Counter(feature.primary_topic for feature in features if feature.primary_topic).most_common(2)
        resource_type = Counter(feature.resource_type for feature in features if feature.resource_type).most_common(1)
        registered = Counter(feature.registered_domain for feature in features if feature.registered_domain).most_common(2)
        parts = []
        if topic:
            parts.append("主题重合集中在 " + ", ".join(name for name, _ in topic))
        if resource_type:
            parts.append(f"资源类型以{resource_type[0][0]}为主")
        if registered:
            parts.append("站点分布参考 " + ", ".join(name for name, _ in registered))
        return "；".join(parts) or "基于综合特征相似度聚类"

    @staticmethod
    def _unique_subcategory_name(subcategories: Dict[str, Dict], base_name: str) -> str:
        if base_name not in subcategories:
            return base_name
        suffix = 2
        while f"{base_name} ({suffix})" in subcategories:
            suffix += 1
        return f"{base_name} ({suffix})"

    def _fallback_clusters(self, bookmarks: List[dict], category: str) -> Dict:
        domain_clusters = self.cluster_by_domain(bookmarks)
        if len(domain_clusters) > 1:
            subcategories = {
                f"{category}/{domain}": {
                    "bookmarks": group,
                    "count": len(group),
                    "cluster_reason": "fallback: domain clustering",
                    "representative_tokens": self._representative_tokens(group),
                    "source_folder_reused": False,
                    "source_folder_quality_score": 0.0,
                    "merge_from_categories": sorted({bookmark.get("classification", {}).get("category", "") for bookmark in group if bookmark.get("classification")}),
                }
                for domain, group in domain_clusters.items()
            }
            return {"category": category, "subcategories": subcategories, "bookmarks": [], "count": len(bookmarks)}
        keyword_clusters = self.cluster_by_keywords(bookmarks)
        subcategories = {
            f"{category}/{name}": {
                "bookmarks": group,
                "count": len(group),
                "cluster_reason": "fallback: keyword clustering",
                "representative_tokens": self._representative_tokens(group),
                "source_folder_reused": False,
                "source_folder_quality_score": 0.0,
                "merge_from_categories": sorted({bookmark.get("classification", {}).get("category", "") for bookmark in group if bookmark.get("classification")}),
            }
            for name, group in keyword_clusters.items()
        }
        return {"category": category, "subcategories": subcategories, "bookmarks": [], "count": len(bookmarks)}

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
        coarse_clusters = self._connected_components(bookmarks, features)
        refined_clusters = self._split_if_needed(coarse_clusters)
        merged_clusters = self._merge_if_needed(refined_clusters)
        overall_consistency = self._cluster_consistency(features) if features else 1.0
        if len(merged_clusters) == 1 and len(bookmarks) >= max(4, threshold * 2) and overall_consistency < 0.5:
            return self._fallback_clusters(bookmarks, category)

        subcategories = {}
        ungrouped = []
        for cluster in merged_clusters:
            if len(cluster) < 2:
                ungrouped.extend(cluster)
                continue
            label = self._derive_cluster_label(cluster, category)
            folder_counter = Counter(tuple(bookmark.get("original_folder_path", [])) for bookmark in cluster if bookmark.get("original_folder_path"))
            best_folder = list(folder_counter.most_common(1)[0][0]) if folder_counter else []
            folder_quality = self._folder_quality_score(cluster)
            source_folder_reused = bool(best_folder and folder_quality >= 0.68)
            if source_folder_reused:
                display_name = "/".join(best_folder[-2:]) if best_folder else label.split("/")[-1]
                subcategory_name = f"{category}/{display_name}"
            else:
                subcategory_name = label if "/" in label else f"{category}/{label}"
            subcategory_name = self._unique_subcategory_name(subcategories, subcategory_name)
            subcategories[subcategory_name] = {
                "bookmarks": cluster,
                "count": len(cluster),
                "cluster_reason": self._build_cluster_reason(cluster),
                "representative_tokens": self._representative_tokens(cluster),
                "source_folder_reused": source_folder_reused,
                "source_folder_quality_score": folder_quality,
                "merge_from_categories": sorted({bookmark.get("classification", {}).get("category", "") for bookmark in cluster if bookmark.get("classification")}),
            }
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

    hierarchy_clusters = clusterer._connected_components(
        bookmarks,
        [clusterer.build_feature_set(bookmark) for bookmark in bookmarks],
    )
    hierarchy_clusters = clusterer._merge_if_needed(clusterer._split_if_needed(hierarchy_clusters))

    hierarchy: dict[str, dict] = {}
    for cluster in hierarchy_clusters:
        cluster_category = clusterer._derive_cluster_label(cluster, "其他/未分类")
        root_category = cluster_category.split("/")[0] if "/" in cluster_category else cluster_category
        if root_category not in hierarchy:
            hierarchy[root_category] = clusterer.build_hierarchy([], root_category, threshold=options.get("max_bookmarks_without_clustering", 20))
            hierarchy[root_category]["bookmarks"] = []
            hierarchy[root_category]["subcategories"] = {}
            hierarchy[root_category]["count"] = 0
        category_bucket = hierarchy[root_category]
        category_bucket["count"] += len(cluster)
        built = clusterer.build_hierarchy(cluster, cluster_category, threshold=options.get("max_bookmarks_without_clustering", 20))
        if built.get("subcategories"):
            for name, item in built["subcategories"].items():
                unique_name = clusterer._unique_subcategory_name(category_bucket["subcategories"], name)
                category_bucket["subcategories"][unique_name] = item
            category_bucket["bookmarks"].extend(built.get("bookmarks", []))
        else:
            unique_name = clusterer._unique_subcategory_name(category_bucket["subcategories"], built["category"])
            category_bucket["subcategories"][unique_name] = {
                **built,
                "category": unique_name,
            }

    output = {
        "hierarchy": hierarchy,
        "stats": {
            "total_categories": len(hierarchy),
            "category_sizes": {category: item["count"] for category, item in hierarchy.items()},
            "subcategories_count": sum(len(item["subcategories"]) for item in hierarchy.values()),
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

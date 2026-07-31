#!/usr/bin/env python3
"""Shared helpers for the bookmarks pipeline."""
from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlsplit, urlunsplit

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT_DIR / "skill_config.json"
FETCH_OUTPUT_SCHEMA_VERSION = "fetch_output/v2"
CLASSIFIED_OUTPUT_SCHEMA_VERSION = "classified_output/v2"
CLUSTERING_OUTPUT_SCHEMA_VERSION = "clustering_output/v2"
SIGNAL_AUDIT_SCHEMA_VERSION = "signal_audit/v1"
QUALITY_REPORT_SCHEMA_VERSION = "quality_report/v2"
SIGNAL_PACK_SCHEMA_VERSION = "signal_pack/v2"
TAXONOMY_BOOTSTRAP_CLUSTERS_SCHEMA_VERSION = "taxonomy_bootstrap_clusters/v1"
TAXONOMY_FOLLOWUP_CANDIDATES_SCHEMA_VERSION = "taxonomy_followup_candidates/v1"
USER_TAXONOMY_RESPONSE_SCHEMA_VERSION = "user_taxonomy_response/v1"
USER_TAXONOMY_SCHEMA_VERSION = "user_taxonomy/v1"
BOOKMARK_TAXONOMY_ASSIGNMENTS_SCHEMA_VERSION = "bookmark_taxonomy_assignments/v1"
FETCH_HOTSPOTS_SCHEMA_VERSION = "fetch_hotspots/v1"
USER_ACTION_REASON_LABELS = {
    "not_found": "可能已失效",
    "invalid_url": "地址格式异常",
    "certificate": "安全证书异常",
    "http_error": "访问状态异常",
}
DEFAULT_TRUSTED_ACCESS_POLICY = {
    "enabled": False,
    "domain_suffixes": [],
    "http_statuses": [],
    "allow_reason_codes": [],
    "domain_rules": [],
}
DEFAULT_ROOT_GROUPS: list[dict[str, Any]] = []
DEFAULT_DISPLAY_OPTIONS = {
    "max_depth": 3,
    "collapse_single_child": True,
    "prefer_human_labels": True,
    "grouping_mode": "auto",
    "main_group_name": "主要主题",
    "fallback_group_name": "待整理",
    "discovery_root_name": "发现主题",
    "tidy_root_name": "待整理",
    "max_direct_normal_roots": 10,
    "standalone_discovery_min_count": 3,
    "tidy_semantic_min_support": 3,
    "tidy_root_fallback_min_count": 3,
    "tidy_resource_group_min_count": 5,
    "tidy_resource_group_max_count": 40,
    "oversized_leaf_threshold": 40,
}
DEFAULT_GENERIC_PLATFORM_DOMAINS = {
    "github.com",
    "github.io",
    "gitlab.com",
    "gitee.com",
    "bitbucket.org",
    "stackoverflow.com",
    "stackexchange.com",
    "medium.com",
    "zhihu.com",
    "csdn.net",
    "51cto.com",
    "jianshu.com",
    "cnblogs.com",
    "docs.qq.com",
    "qq.com",
    "tencent.com",
    "docs.google.com",
    "google.com",
    "notion.so",
    "youtube.com",
    "bilibili.com",
    "feishu.cn",
    "feishu.com",
    "larksuite.com",
}
GENERIC_PLATFORM_TOKENS = {
    "github",
    "gitlab",
    "gitee",
    "bitbucket",
    "stackoverflow",
    "stack",
    "overflow",
    "medium",
    "zhihu",
    "zhuanlan",
    "csdn",
    "51cto",
    "jianshu",
    "qq",
    "tencent",
    "google",
    "notion",
    "youtube",
    "bilibili",
    "repository",
    "repositories",
    "repo",
    "code",
    "search",
    "users",
    "issues",
    "pull",
    "requests",
    "source",
    "master",
    "main",
    "branch",
    "branches",
    "commit",
    "commits",
    "stars",
    "forks",
    "watch",
    "actions",
    "projects",
    "insights",
    "releases",
    "details",
    "article",
    "weixin",
    "feishu",
    "larksuite",
}
NOISY_TOPIC_TOKENS = {
    re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.lower())
    for value in {
        "about",
        "also",
        "article",
        "articles",
        "blog",
        "blogs",
        "book",
        "books",
        "chapter",
        "code",
        "community",
        "details",
        "developers",
        "documentation",
        "download",
        "downloads",
        "feedback",
        "gitcode",
        "guide",
        "home",
        "homepage",
        "homepages",
        "index",
        "latest",
        "management",
        "official",
        "overview",
        "page",
        "pages",
        "please",
        "post",
        "posts",
        "provide",
        "reference",
        "repo",
        "repository",
        "research",
        "saved",
        "search",
        "searches",
        "source",
        "standard",
        "standards",
        "stable",
        "tool",
        "tools",
        "tutorial",
        "user",
        "webpage",
        "wiki",
        "question",
        "questions",
        "breadcrumblist",
        "indexhtml",
        "githubio",
        "飞书",
        "官方文档",
        "官方网站",
        "文档中心",
        "社区",
        "在线文档",
    }
}
WEAK_TOPIC_TOKENS = {
    re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.lower())
    for value in {
        "ai",
        "api",
        "client",
        "create",
        "data",
        "database",
        "display",
        "distributed",
        "efficient",
        "example",
        "examples",
        "fast",
        "language",
        "management",
        "method",
        "native",
        "phone",
        "programming",
        "server",
        "storage",
        "system",
        "systems",
        "website",
        "welcome",
        "week",
    }
}
SOURCE_LIKE_TOPIC_TOKENS = {
    re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.lower())
    for value in {
        "official",
        "documentation",
        "docs",
        "doc",
        "guide",
        "manual",
        "reference",
        "intro",
        "introduction",
        "community",
        "wiki",
        "blog",
        "blogs",
        "article",
        "articles",
        "repository",
        "repositories",
        "repo",
        "book",
        "books",
        "bookstack",
        "page",
        "pages",
        "home",
        "index",
        "stable",
        "latest",
        "action",
        "awesome",
        "ahead",
        "research",
        "session",
        "sessions",
        "chapter",
        "part",
        "html",
        "pdf",
        "online",
        "website",
        "excel",
        "word",
        "ppt",
        "slide",
        "slides",
        "sheet",
        "performance",
        "developers",
        "developer",
        "segmentfault",
        "v2ex",
        "amazonaws",
        "personal",
        "service",
        "services",
        "just",
        "news",
        "center",
        "官方网站",
        "在线文档",
        "腾讯文档",
        "腾讯云",
        "云启未来",
        "文档中心",
        "官方文档",
        "开发者",
        "社区",
        "技术团队",
        "稳定版",
        "githubio",
    }
}


@dataclass
class PipelinePaths:
    bookmark_file: Path
    copied_bookmark_file: Path
    parsed_file: Path
    enriched_file: Path
    classified_file: Path
    clustering_file: Path
    html_output: Path
    user_taxonomy_file: Optional[Path]
    bookmark_assignment_file: Optional[Path]
    reports_dir: Path
    log_file: Path
    duplicate_report_file: Path
    broken_links_report_file: Path
    confirmation_report_file: Path
    review_report_file: Path
    fetch_hotspots_report_file: Path
    rule_suggestions_report_file: Path
    quality_report_file: Path
    signal_audit_report_file: Path
    taxonomy_bootstrap_prompt_file: Path
    taxonomy_bootstrap_clusters_file: Path
    taxonomy_followup_prompt_file: Path
    taxonomy_followup_candidates_file: Path


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return f"[{record.levelname}] {record.getMessage()}"


DEFAULT_PATHS = {
    "bookmark_file": ROOT_DIR / "data" / "bookmarks.html",
    "copied_bookmark_file": ROOT_DIR / "data" / "bookmarks.html",
    "parsed_file": ROOT_DIR / "data" / "parsed_bookmarks.json",
    "enriched_file": ROOT_DIR / "data" / "bookmarks_with_info.json",
    "classified_file": ROOT_DIR / "data" / "classified_bookmarks.json",
    "clustering_file": ROOT_DIR / "data" / "clustering_result.json",
    "html_output": ROOT_DIR / "output" / "organized_bookmarks.html",
    "user_taxonomy_file": ROOT_DIR / "data" / "generated" / "user_taxonomy.json",
    "bookmark_assignment_file": ROOT_DIR / "data" / "generated" / "bookmark_taxonomy_assignments.json",
    "reports_dir": ROOT_DIR / "output" / "reports",
    "log_file": ROOT_DIR / "logs" / "bookmarks_organizer.log",
    "duplicate_report_file": ROOT_DIR / "output" / "reports" / "duplicates.json",
    "broken_links_report_file": ROOT_DIR / "output" / "reports" / "broken_links.json",
    "confirmation_report_file": ROOT_DIR / "output" / "reports" / "needs_confirmation.json",
    "review_report_file": ROOT_DIR / "output" / "reports" / "review_queue.json",
    "fetch_hotspots_report_file": ROOT_DIR / "output" / "reports" / "fetch_hotspots.json",
    "rule_suggestions_report_file": ROOT_DIR / "output" / "reports" / "rule_suggestions.json",
    "quality_report_file": ROOT_DIR / "output" / "reports" / "quality_report.json",
    "signal_audit_report_file": ROOT_DIR / "output" / "reports" / "signal_audit.json",
    "taxonomy_bootstrap_prompt_file": ROOT_DIR / "output" / "reports" / "taxonomy_bootstrap_prompt.md",
    "taxonomy_bootstrap_clusters_file": ROOT_DIR / "output" / "reports" / "taxonomy_bootstrap_clusters.json",
    "taxonomy_followup_prompt_file": ROOT_DIR / "output" / "reports" / "taxonomy_followup_prompt.md",
    "taxonomy_followup_candidates_file": ROOT_DIR / "output" / "reports" / "taxonomy_followup_candidates.json",
}


def _resolve_path(value: Optional[str | Path], fallback: Path, base_dir: Path) -> Path:
    if value is None:
        return fallback
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = (base_dir / candidate).resolve()
    return candidate


def normalize_url(url: str, *, keep_fragment: bool = False) -> str:
    """Normalize bookmark URLs consistently across parse/fetch/cache stages."""
    candidate = (url or "").strip()
    if not candidate:
        return ""

    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return candidate

    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError:
        return candidate
    if hostname:
        netloc = hostname
        if parsed.username:
            userinfo = parsed.username
            if parsed.password:
                userinfo = f"{userinfo}:{parsed.password}"
            netloc = f"{userinfo}@{netloc}"
        default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        if port and not default_port:
            netloc = f"{netloc}:{port}"
    else:
        netloc = parsed.netloc.lower()

    path = parsed.path or "/"
    fragment = parsed.fragment if keep_fragment else ""
    return urlunsplit((scheme, netloc, path, parsed.query, fragment))


def normalize_bookmark_url(url: str) -> str:
    return normalize_url(url, keep_fragment=True)


def normalize_fetch_url(url: str) -> str:
    return normalize_url(url, keep_fragment=False)


def domain_matches_suffix(domain: str, suffix: str) -> bool:
    domain = (domain or "").lower().strip(".")
    suffix = (suffix or "").lower().strip(".")
    return bool(domain and suffix and (domain == suffix or domain.endswith(f".{suffix}")))


def is_generic_platform_domain(domain: str, generic_domains: set[str] | None = None) -> bool:
    domains = generic_domains or DEFAULT_GENERIC_PLATFORM_DOMAINS
    return any(domain_matches_suffix(domain, suffix) for suffix in domains)


def normalize_topic_token(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", (value or "").strip().lower())


def is_source_like_topic_token(value: str, blocked_tokens: set[str] | None = None) -> bool:
    token = normalize_topic_token(value)
    if not token:
        return False
    tokens = blocked_tokens or SOURCE_LIKE_TOPIC_TOKENS
    return token in tokens or bool(re.fullmatch(r"(part|session|chapter)\d+", token))


def _looks_like_generated_identifier(token: str) -> bool:
    if len(token) >= 12 and re.fullmatch(r"[a-f0-9]+", token):
        return True
    if len(token) >= 8:
        digit_count = sum(ch.isdigit() for ch in token)
        if digit_count / len(token) >= 0.45:
            return True
    return False


def _looks_like_platform_slogan(value: str) -> bool:
    text = re.sub(r"\s+", "", value or "")
    if not text:
        return False
    if re.fullmatch(r"([\u4e00-\u9fff]{2,4})你的\1", text):
        return True
    return bool(
        len(text) >= 5
        and any(marker in text for marker in ("平台", "社区", "官网", "文档中心", "官方网站", "在线文档", "下载app"))
        and any(marker in text for marker in ("开发者", "开源", "技术", "代码", "创作", "用户", "文档"))
    )


def _saved_title_is_low_signal(value: str) -> bool:
    text = _clean_signal_text(value)
    if not text:
        return False
    collapsed = re.sub(r"[^\w\u4e00-\u9fff]+", "", text)
    if not collapsed:
        return True
    if _looks_like_platform_slogan(text):
        return True
    if _looks_like_generated_identifier(normalize_topic_token(text)):
        return True
    digit_count = sum(ch.isdigit() for ch in collapsed)
    if len(collapsed) >= 6 and digit_count / len(collapsed) >= 0.6:
        return True
    return False


def is_noisy_topic_token(value: str, blocked_tokens: set[str] | None = None) -> bool:
    token = normalize_topic_token(value)
    if not token:
        return True
    if token in NOISY_TOPIC_TOKENS:
        return True
    if token in GENERIC_PLATFORM_TOKENS:
        return True
    if is_source_like_topic_token(value, blocked_tokens):
        return True
    if _looks_like_generated_identifier(token):
        return True
    if _looks_like_platform_slogan(value):
        return True
    return False


def is_weak_topic_token(value: str) -> bool:
    token = normalize_topic_token(value)
    return bool(token and token in WEAK_TOPIC_TOKENS)


SCHEMA_TYPE_RESOURCE_FACETS = {
    "article": "博客",
    "blogposting": "博客",
    "techarticle": "博客",
    "newsarticle": "博客",
    "creativework": "阅读资料",
    "book": "阅读资料",
    "course": "教育课程",
    "learningresource": "教育课程",
    "softwareapplication": "工具",
    "webapplication": "工具",
    "product": "产品",
    "organization": "组织",
    "scholarlyarticle": "论文",
    "report": "论文",
}

PAGE_TYPE_RESOURCE_FACETS = {
    "documentation": "文档",
    "blog": "博客",
    "product": "产品",
    "tool": "工具",
    "repository": "仓库",
    "research": "论文",
}


def _clean_signal_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _as_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_values = value
    elif isinstance(value, tuple):
        raw_values = list(value)
    else:
        raw_values = [value]
    result: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        normalized = _clean_signal_text(item)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _first_non_empty(*values: Any) -> str:
    for value in values:
        normalized = _clean_signal_text(value)
        if normalized:
            return normalized
    return ""


def signal_field_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def signal_pack_sections(signal_pack: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    signal_pack = signal_pack or {}
    return {
        "identity": signal_pack.get("identity", {}) if isinstance(signal_pack.get("identity"), dict) else {},
        "content": signal_pack.get("content", {}) if isinstance(signal_pack.get("content"), dict) else {},
        "structure": signal_pack.get("structure", {}) if isinstance(signal_pack.get("structure"), dict) else {},
        "health_access": signal_pack.get("health_access", {}) if isinstance(signal_pack.get("health_access"), dict) else {},
        "context_time": signal_pack.get("context_time", {}) if isinstance(signal_pack.get("context_time"), dict) else {},
    }


def flatten_signal_pack(signal_pack: dict[str, Any] | None, *, include_empty: bool = False) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for family, fields in signal_pack_sections(signal_pack).items():
        for field, value in fields.items():
            if include_empty or signal_field_present(value):
                flattened[f"{family}.{field}"] = value
    return flattened


def signal_family_names(signal_pack: dict[str, Any] | None, *, include_empty: bool = False) -> set[str]:
    return {
        family
        for family, fields in signal_pack_sections(signal_pack).items()
        if include_empty or any(signal_field_present(value) for value in fields.values())
    }


def signal_field_names(signal_pack: dict[str, Any] | None, *, include_empty: bool = False) -> set[str]:
    return set(flatten_signal_pack(signal_pack, include_empty=include_empty))


def require_payload_schema(
    payload: dict[str, Any],
    expected_version: str,
    stage_name: str,
    path: Path | None = None,
) -> dict[str, Any]:
    actual = payload.get("schema_version")
    if actual != expected_version:
        location = f" ({path})" if path else ""
        raise ValueError(
            f"{stage_name}{location} schema_version 期望为 {expected_version}，实际为 {actual or 'missing'}。请从上游阶段重新生成产物。"
        )
    return payload


def _metadata_profile_blocks(metadata: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    page = metadata.get("page_signals", {}) if isinstance(metadata.get("page_signals"), dict) else {}
    site = metadata.get("site_signals", {}) if isinstance(metadata.get("site_signals"), dict) else {}
    profile = metadata.get("site_profile", {}) if isinstance(metadata.get("site_profile"), dict) else {}
    page_profile = profile.get("page", {}) if isinstance(profile.get("page"), dict) else {}
    site_profile = profile.get("site", {}) if isinstance(profile.get("site"), dict) else {}
    return page, site, page_profile, site_profile


def _bookmark_time_bucket(add_date: Any) -> dict[str, str]:
    raw = _clean_signal_text(add_date)
    if not raw:
        return {}
    try:
        timestamp = int(float(raw))
    except ValueError:
        return {}
    if timestamp <= 0:
        return {}
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    iso_year, iso_week, _ = dt.isocalendar()
    return {
        "year": f"{dt.year:04d}",
        "year_month": f"{dt.year:04d}-{dt.month:02d}",
        "year_week": f"{iso_year:04d}-W{iso_week:02d}",
    }


def build_signal_pack(bookmark: dict[str, Any]) -> dict[str, Any]:
    """Build stable classification/clustering signals from raw bookmark data."""
    metadata = bookmark.get("metadata", {}) if isinstance(bookmark.get("metadata"), dict) else {}
    page, site, page_profile, site_profile = _metadata_profile_blocks(metadata)

    saved_name = _clean_signal_text(bookmark.get("name"))
    saved_title_low_signal = _saved_title_is_low_signal(saved_name)
    og_title = _first_non_empty(page.get("og:title"), page_profile.get("og:title"))
    twitter_title = _first_non_empty(page.get("twitter:title"), page_profile.get("twitter:title"))
    jsonld_title = _first_non_empty(page.get("jsonld_title"), page_profile.get("jsonld_title"))
    h1 = _first_non_empty(metadata.get("h1"), page.get("h1"), page_profile.get("h1"))
    html_title = _first_non_empty(metadata.get("title"), page.get("title"), page_profile.get("title"))
    preferred_title = _first_non_empty(
        og_title,
        twitter_title,
        jsonld_title,
        h1,
        html_title,
    ) if saved_title_low_signal else _first_non_empty(saved_name, og_title, twitter_title, jsonld_title, h1, html_title)
    title_candidates = _as_text_list([saved_name, og_title, twitter_title, jsonld_title, h1, html_title])

    user_description = _first_non_empty(
        bookmark.get("description"),
        bookmark.get("notes"),
        bookmark.get("description_attr"),
        bookmark.get("notes_attr"),
    )
    og_description = _first_non_empty(page.get("og:description"), page_profile.get("og:description"))
    twitter_description = _first_non_empty(page.get("twitter:description"), page_profile.get("twitter:description"))
    jsonld_description = _first_non_empty(page.get("jsonld_description"), page_profile.get("jsonld_description"))
    meta_description = _first_non_empty(metadata.get("description"), page.get("description"), page_profile.get("description"))
    main_text = _first_non_empty(page.get("main_text_preview"), page_profile.get("main_text_preview"), metadata.get("content_preview"), page.get("content_preview"), page_profile.get("content_preview"))
    preferred_description = _first_non_empty(user_description, og_description, twitter_description, jsonld_description, meta_description, main_text)
    keywords_text = _clean_signal_text(metadata.get("keywords") or page.get("keywords") or page.get("jsonld_keywords") or page_profile.get("keywords") or page_profile.get("jsonld_keywords"))

    page_type_hints = _as_text_list(page.get("page_type_hints") or page_profile.get("page_type_hints"))
    site_type_candidates = _as_text_list(site.get("site_type_candidates") or site_profile.get("site_type_candidates"))
    schema_types = _as_text_list(page.get("schema_types") or page_profile.get("schema_types"))
    code_languages = _as_text_list(page.get("code_languages") or page_profile.get("code_languages"))
    headings = page.get("headings") or page_profile.get("headings") or {}
    headings_h1 = _as_text_list((headings.get("h1") if isinstance(headings, dict) else []) or [h1])
    headings_h2 = _as_text_list((headings.get("h2") if isinstance(headings, dict) else []) or [])
    nav_text = _as_text_list(page.get("nav_text") or page_profile.get("nav_text"))
    resource_facets: list[str] = []
    for hint in page_type_hints + site_type_candidates:
        mapped = PAGE_TYPE_RESOURCE_FACETS.get(hint.lower())
        if mapped:
            resource_facets.append(mapped)
    for schema_type in schema_types:
        normalized_schema = schema_type.rsplit("/", 1)[-1].lower()
        mapped = SCHEMA_TYPE_RESOURCE_FACETS.get(normalized_schema)
        if mapped:
            resource_facets.append(mapped)
    resource_facets = _as_text_list(resource_facets)

    site_name = _first_non_empty(site.get("site_name"), site_profile.get("site_name"), page.get("og:site_name"), page_profile.get("og:site_name"))
    brand_terms = _as_text_list(site.get("brand_terms") or site_profile.get("brand_terms"))
    source_facets = _as_text_list([site_name, metadata.get("registrable_domain"), bookmark.get("domain"), *brand_terms])
    generator = _first_non_empty(page.get("generator"), page_profile.get("generator"))

    language = _first_non_empty(page.get("lang"), page_profile.get("lang"), site.get("content_language"), site_profile.get("content_language"))
    link_health = dict(metadata.get("link_health", {})) if isinstance(metadata.get("link_health"), dict) else {}
    reason_code = str(link_health.get("reason_code") or "")
    if "user_action_required" not in link_health:
        link_health["user_action_required"] = reason_code in USER_ACTION_REASON_LABELS
    if link_health.get("user_action_required") and not link_health.get("user_action_label"):
        link_health["user_action_label"] = USER_ACTION_REASON_LABELS.get(reason_code, "链接需要复查")
    quality_facets = []
    if metadata.get("fetch_status") == "success":
        quality_facets.append("抓取成功")
    if link_health.get("user_action_required"):
        quality_facets.append("待审阅")
    if link_health.get("trusted_override"):
        quality_facets.append("受信任访问")

    canonical_identity = _first_non_empty(
        page.get("canonical_url"),
        page_profile.get("canonical_url"),
        metadata.get("canonical_url"),
        metadata.get("normalized_url"),
        bookmark.get("fetch_normalized_url"),
        normalize_fetch_url(bookmark.get("url", "")),
    )
    normalized_url = _first_non_empty(metadata.get("normalized_url"), bookmark.get("fetch_normalized_url"), normalize_fetch_url(bookmark.get("url", "")))
    semantic_parts = [
        " ".join(title_candidates),
        preferred_description,
        keywords_text,
        h1,
        main_text,
        " ".join(page_type_hints),
        " ".join(site_type_candidates),
        " ".join(schema_types),
        generator,
        " ".join(code_languages),
        site_name,
        " ".join(brand_terms),
        " ".join(headings_h2),
    ]
    time_bucket = _bookmark_time_bucket(bookmark.get("add_date"))
    identity = {
        "saved_title": saved_name,
        "normalized_url": normalized_url,
        "canonical_identity": canonical_identity,
        "domain": _clean_signal_text(bookmark.get("domain")),
        "registrable_domain": _clean_signal_text(metadata.get("registrable_domain")),
        "subdomain": _clean_signal_text(metadata.get("subdomain")),
        "path_segments": _as_text_list(metadata.get("path_segments")),
        "query_keys": _as_text_list(metadata.get("query_keys")),
    }
    content = {
        "preferred_title": preferred_title,
        "preferred_description": preferred_description,
        "semantic_text": " ".join(part for part in semantic_parts if part),
        "main_text": main_text,
        "title_candidates": title_candidates,
        "description_candidates": _as_text_list([user_description, og_description, twitter_description, jsonld_description, meta_description, main_text]),
        "keywords_text": keywords_text,
        "language": language,
        "code_languages": code_languages,
    }
    structure = {
        "page_type_hints": page_type_hints,
        "site_type_candidates": site_type_candidates,
        "schema_types": schema_types,
        "resource_facets": resource_facets,
        "source_facets": source_facets,
        "site_name": site_name,
        "brand_terms": brand_terms,
        "generator": generator,
        "headings_h1": headings_h1,
        "headings_h2": headings_h2,
        "nav_text": nav_text,
        "homepage_fetch_status": _clean_signal_text(site.get("homepage_fetch_status") or site_profile.get("homepage_fetch_status")),
        "homepage_source": _clean_signal_text(site.get("homepage_source") or site_profile.get("homepage_source")),
    }
    health_access = {
        "fetch_status": _clean_signal_text(metadata.get("fetch_status")),
        "link_health": link_health,
        "quality_facets": _as_text_list(quality_facets),
        "fetch_context": metadata.get("fetch_context", {}) if isinstance(metadata.get("fetch_context"), dict) else {},
        "trusted_override": bool(link_health.get("trusted_override")),
        "review_required": bool(link_health.get("review_required", False)),
        "user_action_required": bool(link_health.get("user_action_required", False)),
        "status_code": metadata.get("status_code"),
    }
    context_time = {
        "time_bucket": time_bucket,
        "original_folder_path": list(bookmark.get("original_folder_path", []) or []),
    }

    return {
        "schema_version": SIGNAL_PACK_SCHEMA_VERSION,
        "identity": identity,
        "content": content,
        "structure": structure,
        "health_access": health_access,
        "context_time": context_time,
        "preferred_title": content["preferred_title"],
        "preferred_description": content["preferred_description"],
        "semantic_text": content["semantic_text"],
        "main_text": content["main_text"],
        "title_candidates": content["title_candidates"],
        "description_candidates": content["description_candidates"],
        "keywords_text": content["keywords_text"],
        "page_type_hints": structure["page_type_hints"],
        "site_type_candidates": structure["site_type_candidates"],
        "schema_types": structure["schema_types"],
        "generator": structure["generator"],
        "code_languages": content["code_languages"],
        "resource_facets": structure["resource_facets"],
        "source_facets": structure["source_facets"],
        "quality_facets": health_access["quality_facets"],
        "language": content["language"],
        "time_bucket": context_time["time_bucket"],
        "canonical_identity": identity["canonical_identity"],
        "link_health": health_access["link_health"],
        "original_folder_path": context_time["original_folder_path"],
    }


def pipeline_generated_paths(config: "PipelineConfig") -> dict[str, list[Path]]:
    paths = config.paths
    files = [
        paths.copied_bookmark_file,
        paths.parsed_file,
        paths.enriched_file,
        paths.classified_file,
        paths.clustering_file,
        paths.html_output,
        paths.log_file,
        paths.signal_audit_report_file,
        paths.fetch_hotspots_report_file,
        paths.taxonomy_bootstrap_prompt_file,
        paths.taxonomy_bootstrap_clusters_file,
        paths.taxonomy_followup_prompt_file,
        paths.taxonomy_followup_candidates_file,
    ]
    directories = [paths.reports_dir]
    return {
        "files": list(dict.fromkeys(files)),
        "directories": list(dict.fromkeys(directories)),
    }


class PipelineConfig:
    def __init__(self, raw: Dict[str, Any], config_path: Path):
        self.raw = raw
        self.config_path = config_path
        self.base_dir = config_path.resolve().parent if config_path.exists() else ROOT_DIR
        reports_dir = _resolve_path(raw.get("output", {}).get("reports_directory"), DEFAULT_PATHS["reports_dir"], self.base_dir)
        self.paths = PipelinePaths(
            bookmark_file=_resolve_path(raw.get("input", {}).get("bookmark_file"), DEFAULT_PATHS["bookmark_file"], self.base_dir),
            copied_bookmark_file=_resolve_path(raw.get("pipeline", {}).get("copied_bookmark_file"), DEFAULT_PATHS["copied_bookmark_file"], self.base_dir),
            parsed_file=_resolve_path(raw.get("pipeline", {}).get("parsed_file"), DEFAULT_PATHS["parsed_file"], self.base_dir),
            enriched_file=_resolve_path(raw.get("fetch_options", {}).get("cache_file"), DEFAULT_PATHS["enriched_file"], self.base_dir),
            classified_file=_resolve_path(raw.get("pipeline", {}).get("classified_file"), DEFAULT_PATHS["classified_file"], self.base_dir),
            clustering_file=_resolve_path(raw.get("pipeline", {}).get("clustering_file"), DEFAULT_PATHS["clustering_file"], self.base_dir),
            html_output=_resolve_path(raw.get("output", {}).get("html_file"), DEFAULT_PATHS["html_output"], self.base_dir),
            user_taxonomy_file=_resolve_path(raw.get("input", {}).get("user_taxonomy_file"), DEFAULT_PATHS["user_taxonomy_file"], self.base_dir),
            bookmark_assignment_file=_resolve_path(raw.get("input", {}).get("bookmark_assignment_file"), DEFAULT_PATHS["bookmark_assignment_file"], self.base_dir),
            reports_dir=reports_dir,
            log_file=_resolve_path(raw.get("logging", {}).get("file"), DEFAULT_PATHS["log_file"], self.base_dir),
            duplicate_report_file=_resolve_path(raw.get("output", {}).get("duplicate_report_file"), reports_dir / "duplicates.json", self.base_dir),
            broken_links_report_file=_resolve_path(raw.get("output", {}).get("broken_links_report_file"), reports_dir / "broken_links.json", self.base_dir),
            confirmation_report_file=_resolve_path(raw.get("output", {}).get("confirmation_report_file"), reports_dir / "needs_confirmation.json", self.base_dir),
            review_report_file=_resolve_path(raw.get("output", {}).get("review_report_file"), reports_dir / "review_queue.json", self.base_dir),
            fetch_hotspots_report_file=_resolve_path(raw.get("output", {}).get("fetch_hotspots_report_file"), reports_dir / "fetch_hotspots.json", self.base_dir),
            rule_suggestions_report_file=_resolve_path(raw.get("output", {}).get("rule_suggestions_report_file"), reports_dir / "rule_suggestions.json", self.base_dir),
            quality_report_file=_resolve_path(raw.get("output", {}).get("quality_report_file"), reports_dir / "quality_report.json", self.base_dir),
            signal_audit_report_file=_resolve_path(raw.get("output", {}).get("signal_audit_report_file"), reports_dir / "signal_audit.json", self.base_dir),
            taxonomy_bootstrap_prompt_file=_resolve_path(raw.get("output", {}).get("taxonomy_bootstrap_prompt_file"), reports_dir / "taxonomy_bootstrap_prompt.md", self.base_dir),
            taxonomy_bootstrap_clusters_file=_resolve_path(raw.get("output", {}).get("taxonomy_bootstrap_clusters_file"), reports_dir / "taxonomy_bootstrap_clusters.json", self.base_dir),
            taxonomy_followup_prompt_file=_resolve_path(raw.get("output", {}).get("taxonomy_followup_prompt_file"), reports_dir / "taxonomy_followup_prompt.md", self.base_dir),
            taxonomy_followup_candidates_file=_resolve_path(raw.get("output", {}).get("taxonomy_followup_candidates_file"), reports_dir / "taxonomy_followup_candidates.json", self.base_dir),
        )
        proxy_options = raw.get("fetch_options", {}).get("proxy", {})
        review_policy = raw.get("fetch_options", {}).get("review_policy", {})
        trusted_access = dict(DEFAULT_TRUSTED_ACCESS_POLICY)
        trusted_access.update(review_policy.get("trusted_access", {}))
        self.fetch_options = {
            "concurrent_limit": raw.get("fetch_options", {}).get("concurrent_limit", 15),
            "per_host_limit": raw.get("fetch_options", {}).get("per_host_limit", 4),
            "timeout": raw.get("fetch_options", {}).get("timeout", 15),
            "delay": raw.get("fetch_options", {}).get("delay", 0.8),
            "batch_size": raw.get("fetch_options", {}).get("batch_size", 50),
            "max_retries": raw.get("fetch_options", {}).get("max_retries", 2),
            "force_refetch": raw.get("fetch_options", {}).get("force_refetch", False),
            "origin_warmup_retry": raw.get("fetch_options", {}).get("origin_warmup_retry", True),
            "homepage_on_failure": raw.get("fetch_options", {}).get("homepage_on_failure", True),
            "user_agent": raw.get("fetch_options", {}).get(
                "user_agent",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
            ),
            "proxy": {
                "enabled": proxy_options.get("enabled", False),
                "trust_env": proxy_options.get("trust_env", False),
                "http_proxy": proxy_options.get("http_proxy"),
                "https_proxy": proxy_options.get("https_proxy"),
                "all_proxy": proxy_options.get("all_proxy"),
            },
            "review_policy": {
                "trusted_access": trusted_access,
            },
            "domain_overrides": raw.get("fetch_options", {}).get("domain_overrides", {}),
            "external_sources": raw.get("fetch_options", {}).get(
                "external_sources",
                {
                    "enabled": False,
                    "openalex": {
                        "enabled": True,
                        "timeout": 6,
                    },
                },
            ),
        }
        self.classification_options = raw.get("classification_options", {})
        self.clustering_options = dict(raw.get("clustering_options", {}))
        self.clustering_options.pop("mode", None)
        self.clustering_options.setdefault("discovery_root_name", DEFAULT_DISPLAY_OPTIONS["discovery_root_name"])
        self.clustering_options.setdefault("root_groups", DEFAULT_ROOT_GROUPS)
        if not self.clustering_options.get("root_groups") and self.paths.user_taxonomy_file and self.paths.user_taxonomy_file.exists():
            try:
                user_taxonomy = json.loads(self.paths.user_taxonomy_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                user_taxonomy = {}
            if isinstance(user_taxonomy.get("root_groups"), list):
                self.clustering_options["root_groups"] = user_taxonomy["root_groups"]
        display_options = dict(DEFAULT_DISPLAY_OPTIONS)
        display_options.update(self.clustering_options.get("display", {}))
        self.clustering_options["display"] = display_options
        self.logging_options = raw.get("logging", {})

    @classmethod
    def load(cls, config_path: Path = DEFAULT_CONFIG_PATH) -> "PipelineConfig":
        config_path = config_path.resolve()
        raw: Dict[str, Any] = {}
        if config_path.exists():
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        return cls(raw, config_path)


def build_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="配置文件路径")
    parser.add_argument("--log-level", default=None, help="日志级别，例如 INFO/DEBUG")
    return parser


def configure_logging(config: PipelineConfig, level_override: Optional[str] = None) -> logging.Logger:
    log_file = config.paths.log_file
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("bookmarks")
    logger.handlers.clear()
    logger.setLevel(getattr(logging, (level_override or config.logging_options.get("level", "INFO")).upper(), logging.INFO))
    logger.propagate = False

    formatter = JsonFormatter()

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if config.logging_options.get("console", True):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    return logger


def load_config_from_args(args: argparse.Namespace) -> PipelineConfig:
    return PipelineConfig.load(args.config)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

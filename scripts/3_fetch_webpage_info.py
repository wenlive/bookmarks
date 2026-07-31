#!/usr/bin/env python3
"""步骤3: 异步获取网页信息并检查失效链接。"""
from __future__ import annotations

import asyncio
import inspect
import json
import re
import warnings
from collections import Counter
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qsl, unquote, urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from common import (
    FETCH_OUTPUT_SCHEMA_VERSION,
    FETCH_HOTSPOTS_SCHEMA_VERSION,
    USER_ACTION_REASON_LABELS,
    build_parser,
    configure_logging,
    ensure_parent,
    load_config_from_args,
    normalize_fetch_url,
)

TEXT_PREVIEW_LIMIT = 1500
LOW_SIGNAL_TEXT_LIMIT = 120
ACCESS_LIMITED_HTTP_STATUSES = {401, 403, 429}
GENERIC_TITLE_TOKENS = {
    "home", "index", "welcome", "untitled", "首页", "主页", "documentation", "docs", "untitled page"
}
MULTIPART_SUFFIXES = {
    "co.uk", "org.uk", "gov.uk", "ac.uk", "com.cn", "com.au", "com.hk", "co.jp", "com.br"
}
PAGE_TYPE_HINT_PATTERNS = {
    "documentation": ("docs", "documentation", "manual", "reference", "api", "guide", "sdk"),
    "blog": ("article", "post", "blog", "news", "archive"),
    "product": ("pricing", "features", "about", "solutions", "customers", "contact"),
    "tool": ("dashboard", "app", "tool", "editor", "workspace", "console"),
    "repository": ("github", "gitlab", "bitbucket", "repo", "repository", "issues", "pull"),
    "research": ("paper", "research", "publication", "arxiv", "doi", "abstract"),
}
RETRYABLE_FETCH_STATUSES = {"timeout", "error", "broken", "skipped"}
REVIEW_LABELS = {
    "ok": "可访问",
    "timeout": "访问超时",
    "dns_connection": "DNS/连接失败",
    "certificate": "证书异常",
    "access_denied": "访问受限/疑似反爬",
    "rate_limited": "访问受限/频率限制",
    "not_found": "链接不存在",
    "server_error": "站点服务异常",
    "http_error": "HTTP 4xx/5xx",
    "invalid_url": "无效链接/非HTTP",
    "other_error": "其他抓取异常",
    "trusted_access": "受信任站点/疑似反爬",
}
DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def first_non_empty(*values: Any) -> str:
    for value in values:
        cleaned = clean_text(str(value or ""))
        if cleaned:
            return cleaned
    return ""


def classify_http_status_reason(status_code: Any) -> str:
    try:
        status = int(status_code)
    except (TypeError, ValueError):
        return "http_error"
    if status in {401, 403}:
        return "access_denied"
    if status == 429:
        return "rate_limited"
    if status in {404, 410}:
        return "not_found"
    if 500 <= status <= 599:
        return "server_error"
    return "http_error"


def access_pattern_for_reason(status: str, reason_code: str) -> str:
    if status == "success":
        return "public"
    if reason_code in {"access_denied", "rate_limited", "trusted_access"}:
        return "access_limited"
    if reason_code == "not_found":
        return "missing"
    if reason_code == "server_error":
        return "server_error"
    if status in {"timeout", "error"}:
        return "transport_error"
    return "unknown"


def counter_rows(counter: Counter[str], *, key_name: str, limit: int = 10) -> list[dict[str, int | str]]:
    return [
        {key_name: key, "count": count}
        for key, count in counter.most_common(limit)
        if key
    ]


def dedupe_preserve_order(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = clean_text(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def split_domain_parts(netloc: str) -> tuple[str, str]:
    host = netloc.split("@")[-1].split(":")[0].lower().strip(".")
    if not host or re.fullmatch(r"\d+\.\d+\.\d+\.\d+", host):
        return "", host
    parts = host.split(".")
    if len(parts) <= 2:
        return "", host
    suffix = ".".join(parts[-2:])
    if len(parts) >= 3 and suffix in MULTIPART_SUFFIXES:
        registrable = ".".join(parts[-3:])
        subdomain = ".".join(parts[:-3])
    else:
        registrable = ".".join(parts[-2:])
        subdomain = ".".join(parts[:-2])
    return subdomain, registrable


class SimpleResponse:
    def __init__(self, status: int, url: str, html: str, headers: dict[str, str] | None = None):
        self.status = status
        self.url = url
        self.html = html
        self.headers = headers or {}


def normalize_metadata(metadata: dict | None) -> dict:
    metadata = dict(metadata or {})
    status = metadata.get("fetch_status") or ("success" if metadata else "error")
    error = metadata.get("error", "")
    status_code = metadata.get("status_code")
    error_text = str(error or "")
    lowered = error_text.lower()

    if status == "success":
        reason_code = "ok"
    elif status == "timeout":
        reason_code = "timeout"
    elif status == "broken":
        reason_code = classify_http_status_reason(status_code)
    elif status == "skipped":
        reason_code = "invalid_url"
    elif any(token in lowered for token in ("certificate verify failed", "sslcertverificationerror", "hostname mismatch", "certificate has expired")):
        reason_code = "certificate"
    elif any(token in lowered for token in ("tlsv1_alert", "tlsv1 alert internal error")):
        reason_code = "certificate"
    elif any(token in lowered for token in ("nodename nor servname provided", "name or service not known", "temporary failure in name resolution", "getaddrinfo failed", "cannot connect to host", "connect call failed", "connection reset by peer", "network is unreachable")):
        reason_code = "dns_connection"
    else:
        reason_code = "other_error"

    metadata["fetch_status"] = status
    metadata["link_health"] = {
        "status": status,
        "reason_code": reason_code,
        "reason_label": REVIEW_LABELS[reason_code],
        "access_pattern": access_pattern_for_reason(status, reason_code),
        "review_required": status != "success",
        "user_action_required": reason_code in USER_ACTION_REASON_LABELS,
        "user_action_label": USER_ACTION_REASON_LABELS.get(reason_code, ""),
        "status_code": status_code,
        "error": error_text,
    }
    return metadata


def domain_matches_suffix(domain: str, suffix: str) -> bool:
    normalized_domain = (domain or "").strip(".").lower()
    normalized_suffix = (suffix or "").strip(".").lower()
    return bool(normalized_domain and normalized_suffix and (normalized_domain == normalized_suffix or normalized_domain.endswith(f".{normalized_suffix}")))


def matching_trusted_rule(domain: str, review_policy: dict | None) -> tuple[dict | None, str | None]:
    trusted_access = (review_policy or {}).get("trusted_access", {})
    if not trusted_access.get("enabled"):
        return None, None

    base_rule = {
        "http_statuses": list(trusted_access.get("http_statuses", [])),
        "allow_reason_codes": list(trusted_access.get("allow_reason_codes", [])),
    }
    for rule in trusted_access.get("domain_rules", []):
        matched_domain = next(
            (suffix for suffix in rule.get("domain_suffixes", []) if domain_matches_suffix(domain, suffix)),
            None,
        )
        if matched_domain:
            merged_rule = dict(base_rule)
            merged_rule.update(rule)
            return merged_rule, matched_domain

    matched_domain = next(
        (suffix for suffix in trusted_access.get("domain_suffixes", []) if domain_matches_suffix(domain, suffix)),
        None,
    )
    if not matched_domain:
        return None, None
    return base_rule, matched_domain


def trusted_access_match(domain: str, metadata: dict, review_policy: dict | None) -> str | None:
    normalized = normalize_metadata(metadata)
    link_health = normalized.get("link_health", {})
    status_code = link_health.get("status_code")
    rule, matched_domain = matching_trusted_rule(domain, review_policy)
    if not matched_domain:
        return None

    if status_code in set(rule.get("http_statuses", [])):
        return matched_domain
    reason_code = link_health.get("reason_code")
    if reason_code in set(rule.get("allow_reason_codes", [])):
        return matched_domain
    return None


def apply_review_policy(metadata: dict | None, domain: str, review_policy: dict | None = None) -> dict:
    normalized = normalize_metadata(metadata)
    link_health = dict(normalized.get("link_health", {}))
    raw_reason_code = link_health.get("reason_code")
    raw_reason_label = link_health.get("reason_label")
    raw_review_required = bool(link_health.get("review_required"))

    matched_domain = trusted_access_match(domain, normalized, review_policy)

    link_health["raw_reason_code"] = raw_reason_code
    link_health["raw_reason_label"] = raw_reason_label
    link_health["raw_review_required"] = raw_review_required
    link_health["trusted_override"] = False
    link_health["trusted_domain"] = None

    if matched_domain:
        link_health["reason_code"] = "trusted_access"
        link_health["reason_label"] = REVIEW_LABELS["trusted_access"]
        link_health["access_pattern"] = "access_limited"
        link_health["review_required"] = False
        link_health["user_action_required"] = False
        link_health["user_action_label"] = ""
        link_health["trusted_override"] = True
        link_health["trusted_domain"] = matched_domain

    normalized["link_health"] = link_health
    return normalized


def should_retry_bookmark(existing: dict | None, force_refetch: bool, review_policy: dict | None = None) -> bool:
    if force_refetch or not existing:
        return True
    metadata = apply_review_policy(existing.get("metadata", {}), existing.get("domain", ""), review_policy)
    if not metadata.get("link_health", {}).get("review_required", True):
        return False
    return metadata.get("fetch_status") in RETRYABLE_FETCH_STATUSES


def _metadata_priority(bookmark: dict, review_policy: dict | None = None) -> int:
    metadata = apply_review_policy(bookmark.get("metadata", {}), bookmark.get("domain", ""), review_policy)
    status = metadata.get("fetch_status")
    if status == "success":
        return 4
    if not metadata.get("link_health", {}).get("review_required", True):
        return 3
    if status == "broken":
        return 2
    if status in {"timeout", "error", "skipped"}:
        return 1
    return 0


def bookmark_cache_key(bookmark: dict) -> str:
    return str(bookmark.get("fetch_normalized_url") or normalize_fetch_url(bookmark.get("url", "")))


def build_existing_index(bookmarks: list[dict], review_policy: dict | None = None) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for bookmark in bookmarks:
        key = bookmark_cache_key(bookmark)
        existing = index.get(key)
        if existing is None or _metadata_priority(bookmark, review_policy) >= _metadata_priority(existing, review_policy):
            index[key] = bookmark
    return index


def merge_with_metadata(bookmark: dict, metadata: dict, review_policy: dict | None = None) -> dict:
    enriched = bookmark.copy()
    enriched["metadata"] = apply_review_policy(metadata, bookmark.get("domain", ""), review_policy)
    return enriched


def write_fetch_checkpoint(
    output_file: Path,
    bookmarks: list[dict],
    ordered_results: list[dict | None],
    *,
    processed_count: int,
    review_policy: dict | None = None,
) -> None:
    """Persist completed batches so a long fetch can resume after interruption."""
    checkpoint_bookmarks = []
    for bookmark, result in zip(bookmarks, ordered_results):
        if result is not None:
            checkpoint_bookmarks.append(result)
            continue
        checkpoint_bookmarks.append(
            merge_with_metadata(
                bookmark,
                {
                    "fetch_status": "error",
                    "error": "Fetch pending at last checkpoint",
                    "checkpoint_pending": True,
                    "metadata_schema_version": "site_profile/v1",
                },
                review_policy,
            )
        )

    payload = {
        "schema_version": FETCH_OUTPUT_SCHEMA_VERSION,
        "bookmarks": checkpoint_bookmarks,
        "checkpoint": {
            "complete": False,
            "processed_count": processed_count,
            "total_bookmarks": len(bookmarks),
        },
    }
    ensure_parent(output_file)
    temporary_file = output_file.with_name(f".{output_file.name}.tmp")
    temporary_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_file.replace(output_file)


def fetch_route_configured(proxy_options: dict | None) -> bool:
    options = proxy_options or {}
    explicit_proxy = any(options.get(key) for key in ("http_proxy", "https_proxy", "all_proxy"))
    return bool(options.get("enabled") and (options.get("trust_env") or explicit_proxy))


def build_fetch_context(proxy_options: dict | None, *, attempts: int, resolved_proxy: str | None = None) -> dict:
    options = proxy_options or {}
    explicit_proxy = any(options.get(key) for key in ("http_proxy", "https_proxy", "all_proxy"))
    route = "proxy" if fetch_route_configured(options) else "direct"
    return {
        "route": route,
        "attempts": attempts,
        "proxy_configured": route == "proxy",
        "proxy_explicit": explicit_proxy,
        "proxy_trust_env": bool(options.get("trust_env")),
        "resolved_proxy": resolved_proxy or "",
    }


def match_domain_override(url: str, domain_overrides: dict | None) -> tuple[str | None, dict]:
    if not isinstance(domain_overrides, dict):
        return None, {}
    host = urlparse(url).netloc.split("@")[-1].split(":")[0].lower().strip(".")
    best_match: str | None = None
    best_rule: dict = {}
    best_length = -1
    for suffix, raw_rule in domain_overrides.items():
        normalized_suffix = str(suffix or "").strip(".").lower()
        if not normalized_suffix or not isinstance(raw_rule, dict):
            continue
        if domain_matches_suffix(host, normalized_suffix) and len(normalized_suffix) > best_length:
            best_match = normalized_suffix
            best_rule = dict(raw_rule)
            best_length = len(normalized_suffix)
    return best_match, best_rule


def effective_proxy_options(proxy_options: dict | None, override_rule: dict | None) -> dict:
    effective = dict(proxy_options or {})
    if not override_rule:
        return effective
    if override_rule.get("prefer_direct") or override_rule.get("prefer_direct_retry"):
        effective["enabled"] = False
        effective["trust_env"] = False
        effective["http_proxy"] = None
        effective["https_proxy"] = None
        effective["all_proxy"] = None
    elif override_rule.get("prefer_proxy") and (
        any(effective.get(key) for key in ("http_proxy", "https_proxy", "all_proxy"))
        or effective.get("trust_env")
    ):
        effective["enabled"] = True
    return effective


def resolve_proxy_for_url(url: str, proxy_options: dict) -> str | None:
    if not proxy_options.get("enabled"):
        return None
    parsed = urlparse(url)
    if parsed.scheme == "https":
        return proxy_options.get("https_proxy") or proxy_options.get("all_proxy") or proxy_options.get("http_proxy")
    if parsed.scheme == "http":
        return proxy_options.get("http_proxy") or proxy_options.get("all_proxy") or proxy_options.get("https_proxy")
    return proxy_options.get("all_proxy")


def extract_url_signals(url: str) -> dict:
    normalized_url = normalize_fetch_url(url)
    parsed = urlparse(normalized_url)
    subdomain, registrable = split_domain_parts(parsed.netloc)
    return {
        "normalized_url": normalized_url,
        "scheme": parsed.scheme.lower(),
        "netloc": parsed.netloc.lower(),
        "subdomain": subdomain,
        "registrable_domain": registrable,
        "path_segments": [segment for segment in parsed.path.split("/") if segment],
        "query_keys": [key for key, _ in parse_qsl(parsed.query, keep_blank_values=True)],
    }


def get_meta_content(soup: BeautifulSoup, attr_name: str, attr_value: str) -> str:
    node = soup.find("meta", attrs={attr_name: attr_value})
    return clean_text(node.get("content", "")) if node else ""


def _as_text_list(value: Any) -> list[str]:
    if isinstance(value, str):
        parts = [segment.strip() for segment in re.split(r"[,;/|]", value) if segment.strip()]
        return dedupe_preserve_order(parts or [value])
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_as_text_list(item))
        return dedupe_preserve_order(result)
    if isinstance(value, dict):
        return _as_text_list(value.get("name") or value.get("headline") or value.get("title") or value.get("text"))
    return []


def text_preview(node, limit: int = TEXT_PREVIEW_LIMIT) -> str:
    return clean_text(node.get_text(separator=" ", strip=True))[:limit] if node else ""


def infer_page_type_hints(texts: list[str], path_segments: list[str]) -> list[str]:
    haystack = " ".join(clean_text(text).lower() for text in texts if text)
    hints = []
    for hint, patterns in PAGE_TYPE_HINT_PATTERNS.items():
        if any(pattern in haystack for pattern in patterns) or any(pattern in "/".join(path_segments).lower() for pattern in patterns):
            hints.append(hint)
    return hints


def infer_site_types(page_hints: list[str], homepage_hints: list[str], homepage_url: str, site_name: str) -> list[str]:
    combined = dedupe_preserve_order(page_hints + homepage_hints)
    homepage_text = f"{homepage_url} {site_name}".lower()
    if any(token in homepage_text for token in ("github", "gitlab", "bitbucket")) and "repository" not in combined:
        combined.append("repository")
    return combined or ["general"]


def parse_response_soup(html: str, headers: dict[str, str] | None = None) -> BeautifulSoup:
    content_type = clean_text((headers or {}).get("content-type", "")).lower()
    parser = "xml" if "xml" in content_type else "lxml"
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
        return BeautifulSoup(html, parser)


def build_brand_terms(site_name: str, registrable_domain: str, title: str) -> list[str]:
    brand_terms: list[str] = []
    if site_name:
        brand_terms.extend(re.split(r"[^\w\u4e00-\u9fff]+", site_name))
    if registrable_domain:
        brand_terms.extend(registrable_domain.split("."))
    if title:
        brand_terms.extend(re.split(r"[^\w\u4e00-\u9fff]+", title.split("|")[0]))
    return [term for term in dedupe_preserve_order(brand_terms) if len(term) > 1]


def extract_site_name(page_signals: dict, homepage_signals: dict, registrable_domain: str) -> str:
    for candidate in (
        page_signals.get("og:site_name"),
        homepage_signals.get("og:site_name"),
        homepage_signals.get("title"),
        page_signals.get("og:title"),
    ):
        if candidate:
            return candidate
    return registrable_domain


def should_fetch_homepage(url_signals: dict, page_signals: dict, fetch_homepage_override: bool | None = None) -> bool:
    if fetch_homepage_override is True:
        return True
    if fetch_homepage_override is False:
        return False
    path_depth = len(url_signals.get("path_segments", []))
    text_length = len(page_best_content_preview(page_signals))
    title = page_best_title(page_signals).lower()
    generic_title = not title or title in GENERIC_TITLE_TOKENS or len(title) <= 12
    return text_length < LOW_SIGNAL_TEXT_LIMIT or generic_title or path_depth >= 2


def iter_json_ld_nodes(payload: Any) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        nodes.append(payload)
        for value in payload.values():
            nodes.extend(iter_json_ld_nodes(value))
    elif isinstance(payload, list):
        for item in payload:
            nodes.extend(iter_json_ld_nodes(item))
    return nodes


def extract_json_ld_signals(soup: BeautifulSoup) -> dict[str, Any]:
    signals = {
        "schema_types": [],
        "titles": [],
        "descriptions": [],
        "keywords": [],
        "authors": [],
        "published_at": [],
        "languages": [],
        "article_bodies": [],
    }
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = clean_text(script.string or script.get_text(" ", strip=True))
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for node in iter_json_ld_nodes(payload):
            node_type = node.get("@type")
            if isinstance(node_type, list):
                signals["schema_types"].extend(str(value) for value in node_type if value)
            elif node_type:
                signals["schema_types"].append(str(node_type))
            signals["titles"].extend(_as_text_list(node.get("headline") or node.get("name") or node.get("title")))
            signals["descriptions"].extend(_as_text_list(node.get("description") or node.get("abstract")))
            signals["keywords"].extend(_as_text_list(node.get("keywords")))
            signals["authors"].extend(_as_text_list(node.get("author") or node.get("creator")))
            signals["published_at"].extend(_as_text_list(node.get("datePublished") or node.get("dateCreated")))
            signals["languages"].extend(_as_text_list(node.get("inLanguage")))
            article_body = clean_text(str(node.get("articleBody") or node.get("text") or ""))
            if article_body:
                signals["article_bodies"].append(article_body[:TEXT_PREVIEW_LIMIT])
    for key in signals:
        signals[key] = dedupe_preserve_order(signals[key])
    return signals


def page_best_title(page_signals: dict[str, Any]) -> str:
    return first_non_empty(
        page_signals.get("og:title"),
        page_signals.get("twitter:title"),
        page_signals.get("jsonld_title"),
        page_signals.get("title"),
        page_signals.get("h1"),
    )


def page_best_description(page_signals: dict[str, Any]) -> str:
    return first_non_empty(
        page_signals.get("og:description"),
        page_signals.get("twitter:description"),
        page_signals.get("jsonld_description"),
        page_signals.get("description"),
    )


def page_best_keywords(page_signals: dict[str, Any]) -> str:
    return first_non_empty(page_signals.get("keywords"), page_signals.get("jsonld_keywords"))


def page_best_content_preview(page_signals: dict[str, Any]) -> str:
    return first_non_empty(
        page_signals.get("main_text_preview"),
        page_signals.get("jsonld_article_body"),
        page_signals.get("content_preview"),
    )


def page_signal_is_low_value(page_signals: dict[str, Any]) -> bool:
    title = page_best_title(page_signals).lower()
    description = page_best_description(page_signals)
    preview = page_best_content_preview(page_signals)
    generic_title = not title or title in GENERIC_TITLE_TOKENS or len(title) <= 12
    return generic_title and not description and len(preview) < LOW_SIGNAL_TEXT_LIMIT


def extract_page_signals(soup: BeautifulSoup, resolved_url: str) -> dict:
    title_node = soup.find("title")
    h1 = soup.find("h1")
    main_node = soup.find("main") or soup.find("article") or soup.find("body")
    canonical = soup.find("link", rel=lambda value: value and "canonical" in value.lower())
    headings = {
        "h1": dedupe_preserve_order([node.get_text(" ", strip=True) for node in soup.find_all("h1")]),
        "h2": dedupe_preserve_order([node.get_text(" ", strip=True) for node in soup.find_all("h2")]),
    }
    nav_text = dedupe_preserve_order([text_preview(node, 200) for node in soup.find_all("nav")])
    generator = get_meta_content(soup, "name", "generator")
    code_languages: list[str] = []
    for code_node in soup.find_all(["code", "pre"]):
        classes = code_node.get("class") or []
        for class_name in classes:
            match = re.search(r"(?:language|lang)-([A-Za-z0-9_+#.-]+)", str(class_name))
            if match:
                code_languages.append(match.group(1))
    json_ld_signals = extract_json_ld_signals(soup)

    lang = ""
    html_node = soup.find("html")
    if html_node:
        lang = clean_text(html_node.get("lang", ""))

    page_signals = {
        "title": clean_text(title_node.get_text(strip=True)) if title_node else "",
        "description": get_meta_content(soup, "name", "description"),
        "keywords": get_meta_content(soup, "name", "keywords"),
        "h1": clean_text(h1.get_text(strip=True)) if h1 else "",
        "content_preview": text_preview(soup.find("body")),
        "canonical_url": urljoin(resolved_url, canonical.get("href", "")) if canonical and canonical.get("href") else "",
        "lang": lang,
        "og:title": get_meta_content(soup, "property", "og:title"),
        "og:description": get_meta_content(soup, "property", "og:description"),
        "og:site_name": get_meta_content(soup, "property", "og:site_name"),
        "twitter:title": get_meta_content(soup, "name", "twitter:title"),
        "twitter:description": get_meta_content(soup, "name", "twitter:description"),
        "jsonld_title": first_non_empty(*(json_ld_signals.get("titles") or [])),
        "jsonld_description": first_non_empty(*(json_ld_signals.get("descriptions") or [])),
        "jsonld_keywords": ", ".join(json_ld_signals.get("keywords") or []),
        "jsonld_authors": json_ld_signals.get("authors") or [],
        "published_at": first_non_empty(*(json_ld_signals.get("published_at") or [])),
        "headings": headings,
        "nav_text": nav_text,
        "main_text_preview": text_preview(main_node),
        "jsonld_article_body": first_non_empty(*(json_ld_signals.get("article_bodies") or [])),
        "schema_types": json_ld_signals.get("schema_types") or [],
        "generator": generator,
        "code_languages": dedupe_preserve_order(code_languages),
    }
    texts = [
        page_signals["title"],
        page_signals["description"],
        page_signals["og:title"],
        page_signals["og:description"],
        page_signals["twitter:title"],
        page_signals["twitter:description"],
        page_signals["jsonld_title"],
        page_signals["jsonld_description"],
        page_signals["jsonld_article_body"],
        page_signals["main_text_preview"],
        *page_signals["headings"]["h1"],
        *page_signals["headings"]["h2"],
        *page_signals["schema_types"],
        page_signals["generator"],
        *page_signals["code_languages"],
    ]
    page_signals["page_type_hints"] = infer_page_type_hints(texts, extract_url_signals(resolved_url)["path_segments"])
    return page_signals


async def fetch_url(
    session: aiohttp.ClientSession,
    url: str,
    timeout: int,
    max_retries: int,
    proxy_options: dict,
    *,
    domain_override_name: str | None = None,
    request_headers: dict[str, str] | None = None,
    strategy_name: str = "primary_request",
) -> Dict:
    proxy = resolve_proxy_for_url(url, proxy_options)
    for attempt in range(max_retries + 1):
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=timeout),
                allow_redirects=True,
                proxy=proxy,
                headers=request_headers,
            ) as response:
                status = response.status
                html = await response.text(errors="ignore")
                raw_headers = getattr(response, "headers", {}) or {}
                response_headers = {
                    key: value
                    for key, value in raw_headers.items()
                    if key.lower() in {"content-type", "server", "x-powered-by", "via"}
                }
                return {
                    "response": SimpleResponse(status=status, url=str(response.url), html=html, headers=response_headers),
                    "fetch_context": {
                        **build_fetch_context(proxy_options, attempts=attempt + 1, resolved_proxy=proxy),
                        "domain_override": domain_override_name or "",
                        "strategy": strategy_name,
                    },
                }
        except asyncio.TimeoutError:
            error = {
                "fetch_status": "timeout",
                "error": "Request timeout",
                "fetch_context": {
                    **build_fetch_context(proxy_options, attempts=attempt + 1, resolved_proxy=proxy),
                    "domain_override": domain_override_name or "",
                    "strategy": strategy_name,
                },
            }
        except aiohttp.ClientError as exc:
            error = {
                "fetch_status": "error",
                "error": str(exc),
                "fetch_context": {
                    **build_fetch_context(proxy_options, attempts=attempt + 1, resolved_proxy=proxy),
                    "domain_override": domain_override_name or "",
                    "strategy": strategy_name,
                },
            }
        except Exception as exc:  # noqa: BLE001
            error = {
                "fetch_status": "error",
                "error": str(exc),
                "fetch_context": {
                    **build_fetch_context(proxy_options, attempts=attempt + 1, resolved_proxy=proxy),
                    "domain_override": domain_override_name or "",
                    "strategy": strategy_name,
                },
            }

        if attempt == max_retries:
            return error
    return {
        "fetch_status": "error",
        "error": "Unknown error",
        "fetch_context": {
            **build_fetch_context(proxy_options, attempts=max_retries + 1, resolved_proxy=proxy),
            "domain_override": domain_override_name or "",
            "strategy": strategy_name,
        },
    }


def build_homepage_url(url_or_response: str) -> str:
    parsed = urlparse(url_or_response)
    return f"{parsed.scheme}://{parsed.netloc}/"


def base_site_signals(homepage_url: str, page_signals: dict[str, Any] | None = None) -> dict[str, Any]:
    page_signals = page_signals or {}
    return {
        "homepage_url": homepage_url,
        "site_name": page_signals.get("og:site_name") or "",
        "site_type_candidates": list(page_signals.get("page_type_hints", [])),
        "content_language": page_signals.get("lang") or "",
        "brand_terms": [],
        "homepage_fetch_status": "skipped",
        "homepage_source": "not_needed",
    }


async def enrich_site_from_homepage(
    session: aiohttp.ClientSession,
    homepage_url: str,
    *,
    effective_timeout: int,
    effective_retries: int,
    effective_proxy: dict,
    domain_override_name: str | None,
    page_signals: dict[str, Any] | None = None,
    target_url_signals: dict[str, Any] | None = None,
    fetch_homepage_override: bool | None = None,
    reason: str = "homepage_enrichment",
) -> tuple[dict[str, Any], dict[str, Any]]:
    page_signals = page_signals or {}
    site_signals = base_site_signals(homepage_url, page_signals)
    homepage_page_signals: dict[str, Any] = {}
    if not homepage_url:
        return site_signals, homepage_page_signals
    if page_signals and not should_fetch_homepage(target_url_signals or extract_url_signals(homepage_url), page_signals, fetch_homepage_override):
        return site_signals, homepage_page_signals

    homepage_fetch = await fetch_url(
        session,
        homepage_url,
        effective_timeout,
        effective_retries,
        effective_proxy,
        domain_override_name=domain_override_name,
        strategy_name=reason,
    )
    if "response" in homepage_fetch:
        homepage_response: SimpleResponse = homepage_fetch["response"]
        if homepage_response.status < 400:
            homepage_soup = parse_response_soup(homepage_response.html, homepage_response.headers)
            homepage_page_signals = extract_page_signals(homepage_soup, homepage_response.url)
            site_signals["homepage_fetch_status"] = "success"
            site_signals["homepage_source"] = "fetched"
            site_signals["site_type_candidates"] = infer_site_types(
                page_signals.get("page_type_hints", []),
                homepage_page_signals.get("page_type_hints", []),
                homepage_url,
                homepage_page_signals.get("og:site_name", ""),
            )
            if not site_signals["content_language"]:
                site_signals["content_language"] = homepage_page_signals.get("lang", "")
        else:
            site_signals["homepage_fetch_status"] = "broken"
            site_signals["homepage_source"] = "failed"
    else:
        site_signals["homepage_fetch_status"] = homepage_fetch.get("fetch_status", "error")
        site_signals["homepage_source"] = "failed"

    site_signals["site_name"] = extract_site_name(page_signals, homepage_page_signals, extract_url_signals(homepage_url).get("registrable_domain", ""))
    site_signals["brand_terms"] = build_brand_terms(
        site_signals["site_name"],
        extract_url_signals(homepage_url).get("registrable_domain", ""),
        page_best_title(page_signals) or page_best_title(homepage_page_signals),
    )
    return site_signals, homepage_page_signals


def should_attempt_origin_warmup_retry(status_code: int, url_signals: dict[str, Any], fetch_features: dict[str, Any], override_rule: dict[str, Any]) -> bool:
    if status_code not in ACCESS_LIMITED_HTTP_STATUSES:
        return False
    if not url_signals.get("path_segments"):
        return False
    if override_rule.get("origin_warmup_retry") is False:
        return False
    return bool(fetch_features.get("origin_warmup_retry", True))


def extract_doi_candidates(*values: Any) -> list[str]:
    seen: set[str] = set()
    candidates: list[str] = []
    for value in values:
        text = unquote(str(value or ""))
        for match in DOI_PATTERN.findall(text):
            cleaned = match.rstrip(").,;]")
            normalized = cleaned.lower()
            if normalized not in seen:
                seen.add(normalized)
                candidates.append(cleaned)
    return candidates


async def fetch_openalex_metadata(
    session: aiohttp.ClientSession,
    doi: str,
    *,
    timeout: int,
    proxy_options: dict,
) -> dict[str, Any] | None:
    query_url = "https://api.openalex.org/works"
    proxy = resolve_proxy_for_url(query_url, proxy_options)
    try:
        async with session.get(
            query_url,
            params={
                "filter": f"doi:https://doi.org/{doi}",
                "per-page": 1,
                "select": "display_name,doi,publication_year,type,primary_location",
            },
            timeout=aiohttp.ClientTimeout(total=timeout),
            proxy=proxy,
            headers={"Accept": "application/json"},
        ) as response:
            if response.status >= 400:
                return None
            payload = await response.json(content_type=None)
    except Exception:  # noqa: BLE001
        return None
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list) or not results:
        return None
    row = results[0] if isinstance(results[0], dict) else {}
    primary_location = row.get("primary_location") if isinstance(row.get("primary_location"), dict) else {}
    source = primary_location.get("source") if isinstance(primary_location.get("source"), dict) else {}
    return {
        "provider": "openalex",
        "matched_identifier": doi,
        "title": clean_text(row.get("display_name", "")),
        "description": clean_text(f"{source.get('display_name', '')} {row.get('publication_year', '')}"),
        "canonical_url": clean_text(row.get("doi", "")),
        "resource_type": clean_text(row.get("type", "")),
    }


async def resolve_external_metadata(
    session: aiohttp.ClientSession,
    url: str,
    url_signals: dict[str, Any],
    metadata_seed: dict[str, Any],
    fetch_features: dict[str, Any],
    proxy_options: dict,
) -> dict[str, Any] | None:
    external_options = fetch_features.get("external_sources") or {}
    if not external_options.get("enabled"):
        return None
    openalex_options = external_options.get("openalex") or {}
    if not openalex_options.get("enabled", True):
        return None
    doi_candidates = extract_doi_candidates(
        url,
        metadata_seed.get("canonical_url"),
        metadata_seed.get("title"),
        metadata_seed.get("description"),
        "/".join(url_signals.get("path_segments", [])),
    )
    if not doi_candidates:
        return None
    timeout = int(openalex_options.get("timeout", 6) or 6)
    return await fetch_openalex_metadata(session, doi_candidates[0], timeout=timeout, proxy_options=proxy_options)


def merge_external_metadata(metadata: dict[str, Any], external_metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not external_metadata:
        return metadata
    merged = dict(metadata)
    merged["title"] = first_non_empty(merged.get("title"), external_metadata.get("title"))
    merged["description"] = first_non_empty(merged.get("description"), external_metadata.get("description"))
    merged["canonical_url"] = first_non_empty(merged.get("canonical_url"), external_metadata.get("canonical_url"))
    merged["external_metadata"] = external_metadata
    providers = list(merged.get("metadata_sources") or [])
    provider_name = clean_text(external_metadata.get("provider"))
    if provider_name and provider_name not in providers:
        providers.append(provider_name)
    merged["metadata_sources"] = providers
    return merged


async def fetch_with_aiohttp(
    session: aiohttp.ClientSession,
    url: str,
    timeout: int,
    max_retries: int,
    proxy_options: dict,
    domain_overrides: dict | None = None,
    fetch_features: dict | None = None,
) -> Dict:
    fetch_features = fetch_features or {}
    url_signals = extract_url_signals(url)
    domain_override_name, override_rule = match_domain_override(url, domain_overrides)
    timeout_override = override_rule.get("timeout")
    retries_override = override_rule.get("max_retries")
    effective_timeout = int(timeout if timeout_override is None else timeout_override)
    effective_retries = int(max_retries if retries_override is None else retries_override)
    effective_proxy = effective_proxy_options(proxy_options, override_rule)
    fetch_homepage_override = override_rule.get("fetch_homepage")
    homepage_on_failure = bool(fetch_features.get("homepage_on_failure", True))

    page_fetch = await fetch_url(
        session,
        url,
        effective_timeout,
        effective_retries,
        effective_proxy,
        domain_override_name=domain_override_name,
    )
    fetch_context = page_fetch.get("fetch_context") or {
        **build_fetch_context(effective_proxy, attempts=effective_retries + 1),
        "domain_override": domain_override_name or "",
    }
    fallback_chain: list[str] = []
    prefetched_site_signals: dict[str, Any] = {}
    prefetched_homepage_page_signals: dict[str, Any] = {}
    homepage_url = build_homepage_url(url)

    if "response" not in page_fetch:
        if homepage_on_failure and homepage_url != url:
            prefetched_site_signals, prefetched_homepage_page_signals = await enrich_site_from_homepage(
                session,
                homepage_url,
                effective_timeout=effective_timeout,
                effective_retries=effective_retries,
                effective_proxy=effective_proxy,
                domain_override_name=domain_override_name,
                target_url_signals=url_signals,
                fetch_homepage_override=True,
                reason="homepage_fallback_on_transport_error",
            )
            if prefetched_site_signals.get("homepage_fetch_status") == "success":
                fallback_chain.append("homepage_site_profile")
        metadata_seed = {
            **url_signals,
            **page_fetch,
            "fetch_context": fetch_context,
            "site_signals": prefetched_site_signals,
            "site_profile": {
                "schema_version": "site_profile/v1",
                "url": url_signals,
                "site": prefetched_site_signals,
            } if prefetched_site_signals else {},
            "metadata_sources": ["bookmark"],
            "fallback_chain": fallback_chain,
            "metadata_schema_version": "site_profile/v1",
        }
        external_metadata = await resolve_external_metadata(session, url, url_signals, metadata_seed, fetch_features, effective_proxy)
        return normalize_metadata(merge_external_metadata(metadata_seed, external_metadata))

    page_response: SimpleResponse = page_fetch["response"]
    homepage_url = build_homepage_url(page_response.url)
    if page_response.status >= 400:
        if should_attempt_origin_warmup_retry(page_response.status, url_signals, fetch_features, override_rule) and homepage_url != page_response.url:
            prefetched_site_signals, prefetched_homepage_page_signals = await enrich_site_from_homepage(
                session,
                homepage_url,
                effective_timeout=effective_timeout,
                effective_retries=effective_retries,
                effective_proxy=effective_proxy,
                domain_override_name=domain_override_name,
                target_url_signals=url_signals,
                fetch_homepage_override=True,
                reason="origin_warmup_homepage",
            )
            if prefetched_site_signals.get("homepage_fetch_status") == "success":
                fallback_chain.append("origin_warmup_homepage")
                retry_fetch = await fetch_url(
                    session,
                    url,
                    effective_timeout,
                    effective_retries,
                    effective_proxy,
                    domain_override_name=domain_override_name,
                    request_headers={"Referer": homepage_url},
                    strategy_name="origin_warmup_retry",
                )
                if "response" in retry_fetch:
                    page_response = retry_fetch["response"]
                    fetch_context = retry_fetch.get("fetch_context") or fetch_context
                    if page_response.status < 400:
                        fallback_chain.append("origin_warmup_retry")
                else:
                    fetch_context = retry_fetch.get("fetch_context") or fetch_context
        if page_response.status >= 400:
            if homepage_on_failure and not prefetched_site_signals and homepage_url != page_response.url:
                prefetched_site_signals, prefetched_homepage_page_signals = await enrich_site_from_homepage(
                    session,
                    homepage_url,
                    effective_timeout=effective_timeout,
                    effective_retries=effective_retries,
                    effective_proxy=effective_proxy,
                    domain_override_name=domain_override_name,
                    target_url_signals=url_signals,
                    fetch_homepage_override=True,
                    reason="homepage_fallback_on_http_error",
                )
                if prefetched_site_signals.get("homepage_fetch_status") == "success":
                    fallback_chain.append("homepage_site_profile")
            metadata_seed = {
                **url_signals,
                "fetch_status": "broken",
                "status_code": page_response.status,
                "response_headers": page_response.headers,
                "error": f"HTTP {page_response.status}",
                "fetch_context": fetch_context,
                "site_signals": prefetched_site_signals,
                "site_profile": {
                    "schema_version": "site_profile/v1",
                    "url": url_signals,
                    "site": prefetched_site_signals,
                } if prefetched_site_signals else {},
                "metadata_sources": ["bookmark"],
                "fallback_chain": fallback_chain,
                "metadata_schema_version": "site_profile/v1",
            }
            external_metadata = await resolve_external_metadata(session, url, url_signals, metadata_seed, fetch_features, effective_proxy)
            return normalize_metadata(merge_external_metadata(metadata_seed, external_metadata))

    soup = parse_response_soup(page_response.html, page_response.headers)
    page_signals = extract_page_signals(soup, page_response.url)
    site_signals = base_site_signals(homepage_url, page_signals)
    homepage_page_signals: dict[str, Any] = {}
    if prefetched_site_signals:
        site_signals.update({key: value for key, value in prefetched_site_signals.items() if value not in ("", [], None)})
        homepage_page_signals = prefetched_homepage_page_signals
    if homepage_url != page_response.url and should_fetch_homepage(url_signals, page_signals, fetch_homepage_override):
        if not homepage_page_signals:
            site_signals, homepage_page_signals = await enrich_site_from_homepage(
                session,
                homepage_url,
                effective_timeout=effective_timeout,
                effective_retries=effective_retries,
                effective_proxy=effective_proxy,
                domain_override_name=domain_override_name,
                page_signals=page_signals,
                target_url_signals=url_signals,
                fetch_homepage_override=fetch_homepage_override,
                reason="homepage_enrichment",
            )
        else:
            site_signals["site_type_candidates"] = infer_site_types(
                page_signals.get("page_type_hints", []),
                homepage_page_signals.get("page_type_hints", []),
                homepage_url,
                homepage_page_signals.get("og:site_name", ""),
            )
            if not site_signals.get("content_language"):
                site_signals["content_language"] = homepage_page_signals.get("lang", "")
    site_signals["site_name"] = extract_site_name(page_signals, homepage_page_signals, url_signals.get("registrable_domain", ""))
    site_signals["brand_terms"] = build_brand_terms(
        site_signals["site_name"],
        url_signals.get("registrable_domain", ""),
        page_best_title(page_signals) or page_best_title(homepage_page_signals),
    )
    site_profile = {
        "schema_version": "site_profile/v1",
        "url": url_signals,
        "page": page_signals,
        "site": site_signals,
    }
    metadata_payload = {
        "title": page_best_title(page_signals),
        "description": page_best_description(page_signals),
        "keywords": page_best_keywords(page_signals),
        "h1": page_signals["h1"],
        "content_preview": page_best_content_preview(page_signals),
        "canonical_url": first_non_empty(page_signals.get("canonical_url")),
        **url_signals,
        "fetch_status": "success",
        "status_code": page_response.status,
        "response_headers": page_response.headers,
        "fetch_context": fetch_context,
        "page_signals": page_signals,
        "site_signals": site_signals,
        "site_profile": site_profile,
        "metadata_sources": ["page"],
        "fallback_chain": fallback_chain,
        "metadata_schema_version": "site_profile/v1",
    }
    if page_signal_is_low_value(page_signals):
        external_metadata = await resolve_external_metadata(session, url, url_signals, metadata_payload, fetch_features, effective_proxy)
        metadata_payload = merge_external_metadata(metadata_payload, external_metadata)
    return normalize_metadata(metadata_payload)


async def process_batch(
    bookmarks: list,
    session: aiohttp.ClientSession,
    timeout: int,
    max_retries: int,
    proxy_options: dict,
    domain_overrides: dict | None = None,
    fetch_features: dict | None = None,
    review_policy: dict | None = None,
) -> list:
    tasks = []
    for bookmark in bookmarks:
        if bookmark["url"].startswith(("http://", "https://")):
            tasks.append(fetch_with_aiohttp(session, bookmark["url"], timeout, max_retries, proxy_options, domain_overrides, fetch_features))
        else:
            tasks.append(asyncio.sleep(0, result=normalize_metadata({"fetch_status": "skipped", "error": "Invalid URL", "metadata_schema_version": "site_profile/v1"})))

    responses = await asyncio.gather(*tasks, return_exceptions=True)
    result = []
    for bookmark, metadata in zip(bookmarks, responses):
        if isinstance(metadata, Exception):
            metadata = {"fetch_status": "error", "error": str(metadata), "metadata_schema_version": "site_profile/v1"}
        result.append(merge_with_metadata(bookmark, metadata, review_policy))
    return result



def export_broken_links_report(bookmarks: list, report_file: Path, review_policy: dict | None = None) -> int:
    broken_links = []
    for bookmark in bookmarks:
        metadata = apply_review_policy(bookmark.get("metadata", {}), bookmark.get("domain", ""), review_policy)
        if metadata.get("fetch_status") == "broken" and not metadata.get("link_health", {}).get("trusted_override"):
            broken_links.append(
                {
                    "id": bookmark.get("id"),
                    "name": bookmark.get("name"),
                    "url": bookmark.get("url"),
                    "status_code": metadata.get("status_code"),
                    "error": metadata.get("error"),
                    "reason_code": metadata.get("link_health", {}).get("reason_code"),
                    "review_category": metadata.get("link_health", {}).get("reason_label"),
                }
            )
    ensure_parent(report_file)
    report_file.write_text(json.dumps({"count": len(broken_links), "broken_links": broken_links}, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(broken_links)


def export_review_report(bookmarks: list, report_file: Path, review_policy: dict | None = None) -> int:
    items = []
    for bookmark in bookmarks:
        metadata = apply_review_policy(bookmark.get("metadata", {}), bookmark.get("domain", ""), review_policy)
        link_health = metadata.get("link_health", {})
        if not link_health.get("review_required"):
            continue
        items.append(
            {
                "id": bookmark.get("id"),
                "name": bookmark.get("name"),
                "url": bookmark.get("url"),
                "domain": bookmark.get("domain"),
                "fetch_status": metadata.get("fetch_status"),
                "status_code": metadata.get("status_code"),
                "error": metadata.get("error"),
                "review_category": link_health.get("reason_label"),
                "reason_code": link_health.get("reason_code"),
                "access_pattern": link_health.get("access_pattern"),
                "metadata_sources": metadata.get("metadata_sources", []),
            }
        )
    ensure_parent(report_file)
    report_file.write_text(json.dumps({"count": len(items), "items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(items)


def _domain_fetch_hotspot_rows(bookmarks: list[dict], review_policy: dict | None = None) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for bookmark in bookmarks:
        domain = str(bookmark.get("domain") or "未知域名").strip() or "未知域名"
        row = rows.setdefault(
            domain,
            {
                "domain": domain,
                "total_count": 0,
                "review_count": 0,
                "success_count": 0,
                "reason_codes": Counter(),
                "routes": Counter(),
                "representative_urls": [],
            },
        )
        row["total_count"] += 1
        metadata = apply_review_policy(bookmark.get("metadata", {}), bookmark.get("domain", ""), review_policy)
        link_health = metadata.get("link_health", {})
        route = (metadata.get("fetch_context", {}) or {}).get("route") or "unknown"
        row["routes"][route] += 1
        if metadata.get("fetch_status") == "success":
            row["success_count"] += 1
        if link_health.get("review_required"):
            row["review_count"] += 1
            reason_code = str(link_health.get("reason_code") or "unknown")
            row["reason_codes"][reason_code] += 1
            if len(row["representative_urls"]) < 5:
                row["representative_urls"].append(
                    {
                        "name": bookmark.get("name"),
                        "url": bookmark.get("url"),
                        "reason_code": reason_code,
                    }
                )
    return rows


def export_fetch_hotspots_report(
    primary_bookmarks: list[dict],
    final_bookmarks: list[dict],
    report_file: Path,
    review_policy: dict | None = None,
) -> int:
    primary_rows = _domain_fetch_hotspot_rows(primary_bookmarks, review_policy)
    final_rows = _domain_fetch_hotspot_rows(final_bookmarks, review_policy)
    domains = sorted(set(primary_rows) | set(final_rows))
    items = []
    for domain in domains:
        final_row = final_rows.get(domain, {})
        primary_row = primary_rows.get(domain, {})
        review_count = int(final_row.get("review_count", 0) or 0)
        success_count = int(final_row.get("success_count", 0) or 0)
        pass_deltas = {
            "review_delta": review_count - int(primary_row.get("review_count", 0) or 0),
            "success_delta": success_count - int(primary_row.get("success_count", 0) or 0),
        }
        if review_count <= 0 and pass_deltas["review_delta"] == 0 and pass_deltas["success_delta"] == 0:
            continue
        items.append(
            {
                "domain": domain,
                "review_count": review_count,
                "total_count": int(final_row.get("total_count", 0) or 0),
                "success_count": success_count,
                "reason_codes": counter_rows(final_row.get("reason_codes", Counter()), key_name="reason_code"),
                "routes": counter_rows(final_row.get("routes", Counter()), key_name="route", limit=4),
                "pass_deltas": pass_deltas,
                "representative_urls": final_row.get("representative_urls", []),
            }
        )

    items.sort(key=lambda item: (-item["review_count"], -item["total_count"], item["domain"]))
    payload = {
        "schema_version": FETCH_HOTSPOTS_SCHEMA_VERSION,
        "domain_count": len(items),
        "domains": items,
    }
    ensure_parent(report_file)
    report_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(items)


async def fetch_webpage_info_async(input_file: Path, output_file: Path, options: dict, logger) -> dict:
    data = json.loads(input_file.read_text(encoding="utf-8"))
    bookmarks = data["bookmarks"]
    total = len(bookmarks)
    review_policy = options.get("review_policy", {})

    headers = {
        "User-Agent": options["user_agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    connector = aiohttp.TCPConnector(limit=options["concurrent_limit"], limit_per_host=max(int(options.get("per_host_limit", 0) or 0), 0))
    existing_bookmarks = []
    if output_file.exists():
        try:
            existing_bookmarks = json.loads(output_file.read_text(encoding="utf-8")).get("bookmarks", [])
        except json.JSONDecodeError:
            existing_bookmarks = []
    existing_index = build_existing_index(existing_bookmarks, review_policy)
    ordered_results: list[dict | None] = [None] * total
    pending: list[tuple[int, dict]] = []
    reused_count = 0
    explicit_proxy = any(options.get("proxy", {}).get(key) for key in ("http_proxy", "https_proxy", "all_proxy"))

    for index, bookmark in enumerate(bookmarks):
        existing = existing_index.get(bookmark_cache_key(bookmark))
        if should_retry_bookmark(existing, options["force_refetch"], review_policy):
            pending.append((index, bookmark))
        elif existing:
            ordered_results[index] = merge_with_metadata(bookmark, existing.get("metadata", {}), review_policy)
            reused_count += 1

    async with aiohttp.ClientSession(
        connector=connector,
        headers=headers,
        trust_env=options["proxy"]["enabled"] and options["proxy"]["trust_env"] and not explicit_proxy,
    ) as session:
        for index in range(0, len(pending), options["batch_size"]):
            batch_entries = pending[index:index + options["batch_size"]]
            batch = [bookmark for _, bookmark in batch_entries]
            logger.info("抓取进度: %s/%s", reused_count + index, total)
            process_batch_signature = inspect.signature(process_batch)
            batch_kwargs: dict[str, Any] = {}
            if "domain_overrides" in process_batch_signature.parameters:
                batch_kwargs["domain_overrides"] = options.get("domain_overrides")
            if "fetch_features" in process_batch_signature.parameters:
                batch_kwargs["fetch_features"] = options
            if "review_policy" in process_batch_signature.parameters:
                batch_kwargs["review_policy"] = review_policy
            batch_results = await process_batch(
                batch,
                session,
                options["timeout"],
                options["max_retries"],
                options["proxy"],
                **batch_kwargs,
            )
            for (bookmark_index, _), enriched in zip(batch_entries, batch_results):
                ordered_results[bookmark_index] = enriched
            write_fetch_checkpoint(
                output_file,
                bookmarks,
                ordered_results,
                processed_count=reused_count + index + len(batch_entries),
                review_policy=review_policy,
            )
            await asyncio.sleep(options["delay"])

    final_results: list[dict] = []
    for index, bookmark in enumerate(bookmarks):
        candidate = ordered_results[index]
        if candidate is None:
            candidate = merge_with_metadata(
                bookmark,
                {"fetch_status": "error", "error": "Missing fetch result", "metadata_schema_version": "site_profile/v1"},
                review_policy,
            )
        final_results.append(candidate)

    success_count = 0
    broken_count = 0
    failed_count = 0
    review_free_count = 0
    trusted_override_count = 0
    trusted_override_domains: Counter[str] = Counter()
    route_counts: Counter[str] = Counter()
    review_by_route: Counter[str] = Counter()
    for item in final_results:
        metadata = apply_review_policy(item.get("metadata", {}), item.get("domain", ""), review_policy)
        status = metadata.get("fetch_status")
        link_health = metadata.get("link_health", {})
        route = (metadata.get("fetch_context", {}) or {}).get("route") or "unknown"
        route_counts[route] += 1
        if status == "success":
            success_count += 1
        elif status == "broken":
            broken_count += 1
        else:
            failed_count += 1
        if not link_health.get("review_required", True):
            review_free_count += 1
        else:
            review_by_route[route] += 1
        if link_health.get("trusted_override"):
            trusted_override_count += 1
            trusted_override_domains[link_health.get("trusted_domain") or item.get("domain") or "未知域名"] += 1

    final_data = {
        "schema_version": FETCH_OUTPUT_SCHEMA_VERSION,
        "bookmarks": final_results,
        "stats": {
            "total_bookmarks": total,
            "success_count": success_count,
            "broken_count": broken_count,
            "fail_count": failed_count,
            "review_free_count": review_free_count,
            "success_rate": f"{(success_count * 100 / total) if total else 0:.1f}%",
            "reused_count": reused_count,
            "retried_count": len(pending),
            "proxy_enabled": options["proxy"]["enabled"],
            "proxy_trust_env": options["proxy"]["trust_env"],
            "trusted_override_count": trusted_override_count,
            "trusted_override_domains": dict(sorted(trusted_override_domains.items())),
            "route_counts": dict(sorted(route_counts.items())),
            "review_by_route": dict(sorted(review_by_route.items())),
            "metadata_schema_version": "site_profile/v1",
        },
    }
    ensure_parent(output_file)
    output_file.write_text(json.dumps(final_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return final_data


def build_pass_summary(label: str, result: dict) -> dict:
    stats = result.get("stats", {})
    return {
        "name": label,
        "success_count": stats.get("success_count", 0),
        "broken_count": stats.get("broken_count", 0),
        "fail_count": stats.get("fail_count", 0),
        "review_free_count": stats.get("review_free_count", 0),
        "reused_count": stats.get("reused_count", 0),
        "retried_count": stats.get("retried_count", 0),
        "trusted_override_count": stats.get("trusted_override_count", 0),
        "route_counts": stats.get("route_counts", {}),
        "review_by_route": stats.get("review_by_route", {}),
    }


def run_fetch_passes(input_file: Path, output_file: Path, options: dict, logger) -> dict:
    primary_result = asyncio.run(fetch_webpage_info_async(input_file, output_file, options, logger))
    if not options.get("direct_retry_after_proxy") or not fetch_route_configured(options.get("proxy", {})):
        primary_result["_primary_bookmarks"] = list(primary_result.get("bookmarks", []))
        return primary_result

    direct_retry_options = dict(options)
    direct_retry_options["force_refetch"] = False
    direct_retry_options["proxy"] = {
        "enabled": False,
        "trust_env": False,
        "http_proxy": None,
        "https_proxy": None,
        "all_proxy": None,
    }
    logger.info("开始第二轮直连重试，复用第一轮抓取缓存")
    final_result = asyncio.run(fetch_webpage_info_async(input_file, output_file, direct_retry_options, logger))
    primary_stats = primary_result.get("stats", {})
    final_stats = final_result.get("stats", {})
    final_stats["multi_pass_mode"] = "proxy_then_direct_retry"
    final_stats["proxy_enabled"] = primary_stats.get("proxy_enabled", final_stats.get("proxy_enabled", False))
    final_stats["proxy_trust_env"] = primary_stats.get("proxy_trust_env", final_stats.get("proxy_trust_env", False))
    final_stats["pass_summaries"] = [
        build_pass_summary("proxy", primary_result),
        build_pass_summary("direct_retry", final_result),
    ]
    final_stats["pass_deltas"] = {
        "success_delta": final_stats.get("success_count", 0) - primary_stats.get("success_count", 0),
        "broken_delta": final_stats.get("broken_count", 0) - primary_stats.get("broken_count", 0),
        "fail_delta": final_stats.get("fail_count", 0) - primary_stats.get("fail_count", 0),
        "review_free_delta": final_stats.get("review_free_count", 0) - primary_stats.get("review_free_count", 0),
    }
    ensure_parent(output_file)
    output_file.write_text(json.dumps(final_result, ensure_ascii=False, indent=2), encoding="utf-8")
    final_result["_primary_bookmarks"] = list(primary_result.get("bookmarks", []))
    return final_result


def main() -> int:
    parser = build_parser("抓取网页元信息")
    parser.add_argument("--input", type=Path, default=None, help="输入解析结果 JSON")
    parser.add_argument("--output", type=Path, default=None, help="输出增强结果 JSON")
    parser.add_argument("--broken-links-report", type=Path, default=None, help="失效链接报告输出路径")
    parser.add_argument("--review-report", type=Path, default=None, help="待审阅异常报告输出路径")
    parser.add_argument("--fetch-hotspots-report", type=Path, default=None, help="抓取热点域名报告输出路径")
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--timeout", type=int, default=None)
    parser.add_argument("--delay", type=float, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--max-retries", type=int, default=None, help="单个 URL 的最大重试次数")
    parser.add_argument("--use-proxy", action="store_true", help="显式启用代理")
    parser.add_argument("--trust-env", action="store_true", help="从环境变量读取代理")
    parser.add_argument("--http-proxy", default=None, help="HTTP 代理地址")
    parser.add_argument("--https-proxy", default=None, help="HTTPS 代理地址")
    parser.add_argument("--all-proxy", default=None, help="通用代理地址")
    parser.add_argument("--clear-cache", action="store_true", help="删除抓取缓存后重新开始")
    parser.add_argument("--force-refetch", action="store_true", help="忽略已有成功缓存并全量重抓")
    parser.add_argument("--direct-retry-after-proxy", action="store_true", help="代理抓取后，对剩余异常书签自动去代理重试一次")
    args = parser.parse_args()

    config = load_config_from_args(args)
    logger = configure_logging(config, args.log_level)
    input_file = args.input or config.paths.parsed_file
    output_file = args.output or config.paths.enriched_file
    broken_links_report_file = args.broken_links_report or config.paths.broken_links_report_file
    review_report_file = args.review_report or config.paths.review_report_file
    fetch_hotspots_report_file = args.fetch_hotspots_report or config.paths.fetch_hotspots_report_file

    if not input_file.exists():
        logger.error("输入文件不存在: %s", input_file)
        print(f"错误: 输入文件不存在: {input_file}")
        return 1

    if args.clear_cache and output_file.exists():
        output_file.unlink()
        logger.info("已清理抓取缓存: %s", output_file)

    options = dict(config.fetch_options)
    if args.concurrency is not None:
        options["concurrent_limit"] = args.concurrency
    if args.timeout is not None:
        options["timeout"] = args.timeout
    if args.delay is not None:
        options["delay"] = args.delay
    if args.batch_size is not None:
        options["batch_size"] = args.batch_size
    if args.max_retries is not None:
        options["max_retries"] = max(args.max_retries, 0)
    if args.force_refetch:
        options["force_refetch"] = True

    proxy_options = dict(options.get("proxy", {}))
    if args.use_proxy:
        proxy_options["enabled"] = True
    if args.trust_env:
        proxy_options["trust_env"] = True
    if args.http_proxy is not None:
        proxy_options["http_proxy"] = args.http_proxy
        proxy_options["enabled"] = True
    if args.https_proxy is not None:
        proxy_options["https_proxy"] = args.https_proxy
        proxy_options["enabled"] = True
    if args.all_proxy is not None:
        proxy_options["all_proxy"] = args.all_proxy
        proxy_options["enabled"] = True
    options["proxy"] = proxy_options
    options["direct_retry_after_proxy"] = bool(args.direct_retry_after_proxy)

    result = run_fetch_passes(input_file, output_file, options, logger)
    broken_links_count = export_broken_links_report(result["bookmarks"], broken_links_report_file, options.get("review_policy"))
    review_count = export_review_report(result["bookmarks"], review_report_file, options.get("review_policy"))
    fetch_hotspots_count = export_fetch_hotspots_report(
        result.get("_primary_bookmarks", result.get("bookmarks", [])),
        result["bookmarks"],
        fetch_hotspots_report_file,
        options.get("review_policy"),
    )
    print(f"✓ 网页信息获取完成: {output_file}")
    print(f"  成功: {result['stats']['success_count']}/{result['stats']['total_bookmarks']}")
    print(f"  复用缓存: {result['stats']['reused_count']}")
    print(f"  重试抓取: {result['stats']['retried_count']}")
    print(f"  免审阅: {result['stats']['review_free_count']}")
    print(f"  受信任站点放行: {result['stats']['trusted_override_count']}")
    print(f"  失效链接: {broken_links_count}")
    print(f"  失效链接报告: {broken_links_report_file}")
    print(f"  待审阅异常: {review_count}")
    print(f"  待审阅报告: {review_report_file}")
    print(f"  抓取热点域名: {fetch_hotspots_count}")
    print(f"  抓取热点报告: {fetch_hotspots_report_file}")
    print(f"  失败: {result['stats']['fail_count']}")
    if result["stats"].get("multi_pass_mode") == "proxy_then_direct_retry":
        deltas = result["stats"].get("pass_deltas", {})
        print(f"  双通路重试: 成功变化 {deltas.get('success_delta', 0)}, 免审阅变化 {deltas.get('review_free_delta', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

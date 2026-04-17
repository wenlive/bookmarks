#!/usr/bin/env python3
"""步骤3: 异步获取网页信息并检查失效链接。"""
from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from pathlib import Path
from typing import Dict
from urllib.parse import parse_qsl, urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from common import build_parser, configure_logging, ensure_parent, load_config_from_args, normalize_fetch_url

TEXT_PREVIEW_LIMIT = 500
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
    "http_error": "HTTP 4xx/5xx",
    "invalid_url": "无效链接/非HTTP",
    "other_error": "其他抓取异常",
    "trusted_access": "受信任站点/疑似反爬",
}


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


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
    def __init__(self, status: int, url: str, html: str):
        self.status = status
        self.url = url
        self.html = html


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
        reason_code = "http_error"
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
        "review_required": status != "success",
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
    reason_code = link_health.get("reason_code")
    status_code = link_health.get("status_code")
    rule, matched_domain = matching_trusted_rule(domain, review_policy)
    if not matched_domain:
        return None

    if reason_code == "http_error" and status_code in set(rule.get("http_statuses", [])):
        return matched_domain
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
        link_health["review_required"] = False
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


def should_fetch_homepage(url_signals: dict, page_signals: dict) -> bool:
    path_depth = len(url_signals.get("path_segments", []))
    text_length = len(page_signals.get("main_text_preview") or page_signals.get("content_preview") or "")
    title = (page_signals.get("title") or "").strip().lower()
    generic_title = not title or title in GENERIC_TITLE_TOKENS or len(title) <= 12
    return text_length < 120 or generic_title or path_depth >= 2


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
    json_ld_types: list[str] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = clean_text(script.string or script.get_text(" ", strip=True))
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if isinstance(item, dict):
                node_type = item.get("@type")
                if isinstance(node_type, list):
                    json_ld_types.extend(str(value) for value in node_type)
                elif node_type:
                    json_ld_types.append(str(node_type))

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
        "headings": headings,
        "nav_text": nav_text,
        "main_text_preview": text_preview(main_node),
        "schema_types": dedupe_preserve_order(json_ld_types),
    }
    texts = [
        page_signals["title"],
        page_signals["description"],
        page_signals["og:title"],
        page_signals["og:description"],
        page_signals["twitter:title"],
        page_signals["twitter:description"],
        page_signals["main_text_preview"],
        *page_signals["headings"]["h1"],
        *page_signals["headings"]["h2"],
        *page_signals["schema_types"],
    ]
    page_signals["page_type_hints"] = infer_page_type_hints(texts, extract_url_signals(resolved_url)["path_segments"])
    return page_signals


async def fetch_url(session: aiohttp.ClientSession, url: str, timeout: int, max_retries: int, proxy_options: dict) -> Dict:
    proxy = resolve_proxy_for_url(url, proxy_options)
    for attempt in range(max_retries + 1):
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=timeout),
                allow_redirects=True,
                proxy=proxy,
            ) as response:
                status = response.status
                html = await response.text(errors="ignore")
                return {"response": SimpleResponse(status=status, url=str(response.url), html=html)}
        except asyncio.TimeoutError:
            error = {"fetch_status": "timeout", "error": "Request timeout"}
        except aiohttp.ClientError as exc:
            error = {"fetch_status": "error", "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            error = {"fetch_status": "error", "error": str(exc)}

        if attempt == max_retries:
            return error
    return {"fetch_status": "error", "error": "Unknown error"}


async def fetch_with_aiohttp(session: aiohttp.ClientSession, url: str, timeout: int, max_retries: int, proxy_options: dict) -> Dict:
    url_signals = extract_url_signals(url)
    page_fetch = await fetch_url(session, url, timeout, max_retries, proxy_options)
    if "response" not in page_fetch:
        return normalize_metadata({**url_signals, **page_fetch, "metadata_schema_version": "site_profile/v1"})

    page_response: SimpleResponse = page_fetch["response"]
    if page_response.status >= 400:
        return {
            **url_signals,
            "fetch_status": "broken",
            "status_code": page_response.status,
            "error": f"HTTP {page_response.status}",
            "metadata_schema_version": "site_profile/v1",
        }

    soup = BeautifulSoup(page_response.html, "lxml")
    page_signals = extract_page_signals(soup, page_response.url)
    homepage_url = f"{urlparse(page_response.url).scheme}://{urlparse(page_response.url).netloc}/"
    site_signals = {
        "homepage_url": homepage_url,
        "site_name": page_signals.get("og:site_name") or "",
        "site_type_candidates": page_signals.get("page_type_hints", []),
        "content_language": page_signals.get("lang") or "",
        "brand_terms": [],
        "homepage_fetch_status": "skipped",
        "homepage_source": "not_needed",
    }

    homepage_page_signals: dict = {}
    if homepage_url != page_response.url and should_fetch_homepage(url_signals, page_signals):
        homepage_fetch = await fetch_url(session, homepage_url, timeout, max_retries, proxy_options)
        if "response" in homepage_fetch:
            homepage_response: SimpleResponse = homepage_fetch["response"]
            if homepage_response.status < 400:
                homepage_soup = BeautifulSoup(homepage_response.html, "lxml")
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

    site_signals["site_name"] = extract_site_name(page_signals, homepage_page_signals, url_signals.get("registrable_domain", ""))
    site_signals["brand_terms"] = build_brand_terms(
        site_signals["site_name"],
        url_signals.get("registrable_domain", ""),
        page_signals.get("title", "") or homepage_page_signals.get("title", ""),
    )
    site_profile = {
        "schema_version": "site_profile/v1",
        "url": url_signals,
        "page": page_signals,
        "site": site_signals,
    }
    return normalize_metadata({
        "title": page_signals["title"],
        "description": page_signals["description"],
        "keywords": page_signals["keywords"],
        "h1": page_signals["h1"],
        "content_preview": page_signals["content_preview"],
        **url_signals,
        "fetch_status": "success",
        "status_code": page_response.status,
        "page_signals": page_signals,
        "site_signals": site_signals,
        "site_profile": site_profile,
        "metadata_schema_version": "site_profile/v1",
    })


async def process_batch(
    bookmarks: list,
    session: aiohttp.ClientSession,
    timeout: int,
    max_retries: int,
    proxy_options: dict,
    review_policy: dict | None = None,
) -> list:
    tasks = []
    for bookmark in bookmarks:
        if bookmark["url"].startswith(("http://", "https://")):
            tasks.append(fetch_with_aiohttp(session, bookmark["url"], timeout, max_retries, proxy_options))
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
            }
        )
    ensure_parent(report_file)
    report_file.write_text(json.dumps({"count": len(items), "items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(items)


async def fetch_webpage_info_async(input_file: Path, output_file: Path, options: dict, logger) -> dict:
    data = json.loads(input_file.read_text(encoding="utf-8"))
    bookmarks = data["bookmarks"]
    total = len(bookmarks)
    review_policy = options.get("review_policy", {})

    headers = {
        "User-Agent": options["user_agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    connector = aiohttp.TCPConnector(limit=options["concurrent_limit"])
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
            batch_results = await process_batch(
                batch,
                session,
                options["timeout"],
                options["max_retries"],
                options["proxy"],
                review_policy,
            )
            for (bookmark_index, _), enriched in zip(batch_entries, batch_results):
                ordered_results[bookmark_index] = enriched
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
    for item in final_results:
        metadata = apply_review_policy(item.get("metadata", {}), item.get("domain", ""), review_policy)
        status = metadata.get("fetch_status")
        link_health = metadata.get("link_health", {})
        if status == "success":
            success_count += 1
        elif status == "broken":
            broken_count += 1
        else:
            failed_count += 1
        if not link_health.get("review_required", True):
            review_free_count += 1
        if link_health.get("trusted_override"):
            trusted_override_count += 1
            trusted_override_domains[link_health.get("trusted_domain") or item.get("domain") or "未知域名"] += 1

    final_data = {
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
            "metadata_schema_version": "site_profile/v1",
        },
    }
    ensure_parent(output_file)
    output_file.write_text(json.dumps(final_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return final_data


def main() -> int:
    parser = build_parser("抓取网页元信息")
    parser.add_argument("--input", type=Path, default=None, help="输入解析结果 JSON")
    parser.add_argument("--output", type=Path, default=None, help="输出增强结果 JSON")
    parser.add_argument("--broken-links-report", type=Path, default=None, help="失效链接报告输出路径")
    parser.add_argument("--review-report", type=Path, default=None, help="待审阅异常报告输出路径")
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--timeout", type=int, default=None)
    parser.add_argument("--delay", type=float, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--use-proxy", action="store_true", help="显式启用代理")
    parser.add_argument("--trust-env", action="store_true", help="从环境变量读取代理")
    parser.add_argument("--http-proxy", default=None, help="HTTP 代理地址")
    parser.add_argument("--https-proxy", default=None, help="HTTPS 代理地址")
    parser.add_argument("--all-proxy", default=None, help="通用代理地址")
    parser.add_argument("--clear-cache", action="store_true", help="删除抓取缓存后重新开始")
    parser.add_argument("--force-refetch", action="store_true", help="忽略已有成功缓存并全量重抓")
    args = parser.parse_args()

    config = load_config_from_args(args)
    logger = configure_logging(config, args.log_level)
    input_file = args.input or config.paths.parsed_file
    output_file = args.output or config.paths.enriched_file
    broken_links_report_file = args.broken_links_report or config.paths.broken_links_report_file
    review_report_file = args.review_report or config.paths.review_report_file

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

    result = asyncio.run(fetch_webpage_info_async(input_file, output_file, options, logger))
    broken_links_count = export_broken_links_report(result["bookmarks"], broken_links_report_file, options.get("review_policy"))
    review_count = export_review_report(result["bookmarks"], review_report_file, options.get("review_policy"))
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
    print(f"  失败: {result['stats']['fail_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

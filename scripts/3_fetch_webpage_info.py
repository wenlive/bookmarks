#!/usr/bin/env python3
"""步骤3: 异步获取网页信息并检查失效链接。"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Dict
from urllib.parse import parse_qsl, urljoin, urlparse, urlunparse

import aiohttp
from bs4 import BeautifulSoup

from common import build_parser, configure_logging, ensure_parent, load_config_from_args

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


def extract_url_signals(url: str) -> dict:
    parsed = urlparse(url)
    normalized_url = urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", "", parsed.query, ""))
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


async def fetch_url(session: aiohttp.ClientSession, url: str, timeout: int, max_retries: int) -> Dict:
    for attempt in range(max_retries + 1):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout), allow_redirects=True) as response:
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


async def fetch_with_aiohttp(session: aiohttp.ClientSession, url: str, timeout: int, max_retries: int) -> Dict:
    url_signals = extract_url_signals(url)
    page_fetch = await fetch_url(session, url, timeout, max_retries)
    if "response" not in page_fetch:
        return {**url_signals, **page_fetch, "metadata_schema_version": "site_profile/v1"}

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
        homepage_fetch = await fetch_url(session, homepage_url, timeout, max_retries)
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
    return {
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
    }


async def process_batch(bookmarks: list, session: aiohttp.ClientSession, timeout: int, max_retries: int) -> list:
    tasks = []
    for bookmark in bookmarks:
        if bookmark["url"].startswith(("http://", "https://")):
            tasks.append(fetch_with_aiohttp(session, bookmark["url"], timeout, max_retries))
        else:
            tasks.append(asyncio.sleep(0, result={"fetch_status": "skipped", "error": "Invalid URL", "metadata_schema_version": "site_profile/v1"}))

    responses = await asyncio.gather(*tasks, return_exceptions=True)
    result = []
    for bookmark, metadata in zip(bookmarks, responses):
        if isinstance(metadata, Exception):
            metadata = {"fetch_status": "error", "error": str(metadata), "metadata_schema_version": "site_profile/v1"}
        enriched = bookmark.copy()
        enriched["metadata"] = metadata
        result.append(enriched)
    return result



def export_broken_links_report(bookmarks: list, report_file: Path) -> int:
    broken_links = []
    for bookmark in bookmarks:
        metadata = bookmark.get("metadata", {})
        if metadata.get("fetch_status") == "broken":
            broken_links.append(
                {
                    "id": bookmark.get("id"),
                    "name": bookmark.get("name"),
                    "url": bookmark.get("url"),
                    "status_code": metadata.get("status_code"),
                    "error": metadata.get("error"),
                }
            )
    ensure_parent(report_file)
    report_file.write_text(json.dumps({"count": len(broken_links), "broken_links": broken_links}, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(broken_links)


async def fetch_webpage_info_async(input_file: Path, output_file: Path, options: dict, logger) -> dict:
    data = json.loads(input_file.read_text(encoding="utf-8"))
    bookmarks = data["bookmarks"]
    total = len(bookmarks)

    headers = {"User-Agent": options["user_agent"]}
    connector = aiohttp.TCPConnector(limit=options["concurrent_limit"])
    results = []
    success_count = 0
    broken_count = 0
    failed_count = 0

    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        for index in range(0, total, options["batch_size"]):
            batch = bookmarks[index:index + options["batch_size"]]
            logger.info("抓取进度: %s/%s", index, total)
            batch_results = await process_batch(batch, session, options["timeout"], options["max_retries"])
            results.extend(batch_results)
            for item in batch_results:
                status = item["metadata"].get("fetch_status")
                if status == "success":
                    success_count += 1
                elif status == "broken":
                    broken_count += 1
                else:
                    failed_count += 1
            await asyncio.sleep(options["delay"])

    final_data = {
        "bookmarks": results,
        "stats": {
            "total_bookmarks": total,
            "success_count": success_count,
            "broken_count": broken_count,
            "fail_count": failed_count,
            "success_rate": f"{(success_count * 100 / total) if total else 0:.1f}%",
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
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--timeout", type=int, default=None)
    parser.add_argument("--delay", type=float, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    args = parser.parse_args()

    config = load_config_from_args(args)
    logger = configure_logging(config, args.log_level)
    input_file = args.input or config.paths.parsed_file
    output_file = args.output or config.paths.enriched_file
    broken_links_report_file = args.broken_links_report or config.paths.broken_links_report_file

    if not input_file.exists():
        logger.error("输入文件不存在: %s", input_file)
        print(f"错误: 输入文件不存在: {input_file}")
        return 1

    options = dict(config.fetch_options)
    if args.concurrency is not None:
        options["concurrent_limit"] = args.concurrency
    if args.timeout is not None:
        options["timeout"] = args.timeout
    if args.delay is not None:
        options["delay"] = args.delay
    if args.batch_size is not None:
        options["batch_size"] = args.batch_size

    result = asyncio.run(fetch_webpage_info_async(input_file, output_file, options, logger))
    broken_links_count = export_broken_links_report(result["bookmarks"], broken_links_report_file)
    print(f"✓ 网页信息获取完成: {output_file}")
    print(f"  成功: {result['stats']['success_count']}/{result['stats']['total_bookmarks']}")
    print(f"  失效链接: {broken_links_count}")
    print(f"  失效链接报告: {broken_links_report_file}")
    print(f"  失败: {result['stats']['fail_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

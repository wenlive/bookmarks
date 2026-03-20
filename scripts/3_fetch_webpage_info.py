#!/usr/bin/env python3
"""步骤3: 异步获取网页信息并检查失效链接。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Dict

import aiohttp
from bs4 import BeautifulSoup

from common import build_parser, configure_logging, ensure_parent, load_config_from_args


async def fetch_with_aiohttp(session: aiohttp.ClientSession, url: str, timeout: int, max_retries: int) -> Dict:
    for attempt in range(max_retries + 1):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout), allow_redirects=True) as response:
                status = response.status
                html = await response.text(errors="ignore")
                if status >= 400:
                    return {"fetch_status": "broken", "status_code": status, "error": f"HTTP {status}"}

                soup = BeautifulSoup(html, "lxml")
                title = soup.find("title")
                meta_desc = soup.find("meta", attrs={"name": "description"})
                meta_keywords = soup.find("meta", attrs={"name": "keywords"})
                h1 = soup.find("h1")
                body = soup.find("body")
                text = body.get_text(separator=" ", strip=True)[:500] if body else ""

                return {
                    "title": title.get_text(strip=True) if title else "",
                    "description": meta_desc.get("content", "") if meta_desc else "",
                    "keywords": meta_keywords.get("content", "") if meta_keywords else "",
                    "h1": h1.get_text(strip=True) if h1 else "",
                    "content_preview": text,
                    "fetch_status": "success",
                    "status_code": status,
                }
        except asyncio.TimeoutError:
            error = {"fetch_status": "timeout", "error": "Request timeout"}
        except aiohttp.ClientError as exc:
            error = {"fetch_status": "error", "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            error = {"fetch_status": "error", "error": str(exc)}

        if attempt == max_retries:
            return error

    return {"fetch_status": "error", "error": "Unknown error"}


async def process_batch(bookmarks: list, session: aiohttp.ClientSession, timeout: int, max_retries: int) -> list:
    tasks = []
    for bookmark in bookmarks:
        if bookmark["url"].startswith(("http://", "https://")):
            tasks.append(fetch_with_aiohttp(session, bookmark["url"], timeout, max_retries))
        else:
            tasks.append(asyncio.sleep(0, result={"fetch_status": "skipped", "error": "Invalid URL"}))

    responses = await asyncio.gather(*tasks, return_exceptions=True)
    result = []
    for bookmark, metadata in zip(bookmarks, responses):
        if isinstance(metadata, Exception):
            metadata = {"fetch_status": "error", "error": str(metadata)}
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

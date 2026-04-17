#!/usr/bin/env python3
"""步骤2: 解析 Chrome 书签 HTML 文件。"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from common import (
    build_parser,
    configure_logging,
    ensure_parent,
    load_config_from_args,
    normalize_bookmark_url,
    normalize_fetch_url,
)


IGNORED_FOLDERS = {"书签栏", "Bookmarks Bar", "Bookmarks bar", "其他书签", "Other Bookmarks"}


def parse_bookmarks(html_file: Path) -> dict:
    content = html_file.read_text(encoding="utf-8")
    soup = BeautifulSoup(content, "html.parser")

    bookmarks = []
    seen_urls = set()
    bookmark_id = 0
    all_folders = set()
    duplicates = []

    def get_folder_path(a_tag) -> list[str]:
        path = []
        current = a_tag.parent
        while current:
            if current.name == "dt":
                h3 = current.find("h3", recursive=False)
                if h3:
                    folder_name = h3.get_text(strip=True)
                    if folder_name and folder_name not in IGNORED_FOLDERS:
                        path.insert(0, folder_name)
                        all_folders.add("/".join(path))
            current = current.parent
        return path

    for a in soup.find_all("a"):
        url = a.get("href", "")
        if not url or url.startswith("javascript:"):
            continue

        bookmark_url_key = normalize_bookmark_url(url)
        fetch_url_key = normalize_fetch_url(url)
        if bookmark_url_key in seen_urls:
            duplicates.append({"url": url, "name": a.get_text(strip=True)})
            continue
        seen_urls.add(bookmark_url_key)

        try:
            domain = urlparse(fetch_url_key or url).netloc
        except ValueError:
            domain = ""

        bookmarks.append(
            {
                "id": f"bookmark_{bookmark_id}",
                "name": a.get_text(strip=True),
                "url": url,
                "domain": domain,
                "fetch_normalized_url": fetch_url_key,
                "original_folder_path": get_folder_path(a),
                "add_date": a.get("add_date", ""),
                "icon": a.get("icon", ""),
                "metadata": {},
            }
        )
        bookmark_id += 1

    domain_stats = defaultdict(int)
    folder_stats = defaultdict(int)
    for bookmark in bookmarks:
        if bookmark["domain"]:
            domain_stats[bookmark["domain"]] += 1
        folder_path = "/".join(bookmark["original_folder_path"])
        if folder_path:
            folder_stats[folder_path] += 1

    return {
        "bookmarks": bookmarks,
        "stats": {
            "total_bookmarks": len(bookmarks),
            "duplicate_count": len(duplicates),
            "duplicates": duplicates[:100],
            "total_folders": len(all_folders),
            "unique_domains": len(domain_stats),
            "top_domains": dict(sorted(domain_stats.items(), key=lambda item: item[1], reverse=True)[:20]),
            "top_folders": dict(sorted(folder_stats.items(), key=lambda item: item[1], reverse=True)[:20]),
            "all_folders": sorted(all_folders),
        },
    }


def export_duplicate_report(result: dict, report_file: Path) -> None:
    ensure_parent(report_file)
    report = {
        "count": result["stats"]["duplicate_count"],
        "duplicates": result["stats"]["duplicates"],
    }
    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = build_parser("解析 Chrome 书签 HTML")
    parser.add_argument("--input", type=Path, default=None, help="输入 HTML 文件")
    parser.add_argument("--output", type=Path, default=None, help="输出解析 JSON 文件")
    parser.add_argument("--duplicate-report", type=Path, default=None, help="重复 URL 报告输出路径")
    args = parser.parse_args()

    config = load_config_from_args(args)
    logger = configure_logging(config, args.log_level)

    input_file = args.input or config.paths.copied_bookmark_file
    output_file = args.output or config.paths.parsed_file
    duplicate_report_file = args.duplicate_report or config.paths.duplicate_report_file

    if not input_file.exists():
        logger.error("输入文件不存在: %s", input_file)
        print(f"错误: 输入文件不存在: {input_file}")
        return 1

    result = parse_bookmarks(input_file)
    ensure_parent(output_file)
    output_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    export_duplicate_report(result, duplicate_report_file)

    logger.info("步骤2完成: %s -> %s", input_file, output_file)
    print(f"✓ 解析完成: {output_file}")
    print(f"  总书签数: {result['stats']['total_bookmarks']}")
    print(f"  重复 URL 数: {result['stats']['duplicate_count']}")
    print(f"  重复 URL 报告: {duplicate_report_file}")
    print(f"  唯一域名数: {result['stats']['unique_domains']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

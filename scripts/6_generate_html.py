#!/usr/bin/env python3
"""步骤6: 生成 Chrome 可导入的书签 HTML。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict

from common import build_parser, configure_logging, ensure_parent, load_config_from_args


class BookmarkHTMLGenerator:
    def __init__(self):
        self.bookmark_count = 0
        self.folder_count = 0

    @staticmethod
    def escape_html(text: str) -> str:
        if not text:
            return ""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    def generate_bookmark_item(self, bookmark: dict) -> str:
        self.bookmark_count += 1
        name = self.escape_html(bookmark.get("name", "未命名"))
        url = self.escape_html(bookmark.get("url", ""))
        add_date = bookmark.get("add_date") or str(int(datetime.now().timestamp()))
        icon = bookmark.get("icon", "")
        icon_attr = f' ICON="{self.escape_html(icon)}"' if icon else ""
        return f'        <DT><A HREF="{url}" ADD_DATE="{add_date}"{icon_attr}>{name}</A>\n'

    def generate_subcategory(self, category_name: str, subcategory_data: dict, level: int = 2) -> str:
        self.folder_count += 1
        display_name = category_name.split("/")[-1]
        indent = "    " * level
        add_date = str(int(datetime.now().timestamp()))
        html = f'{indent}<DT><H3 ADD_DATE="{add_date}">{self.escape_html(display_name)}</H3>\n'
        html += f"{indent}<DL><p>\n"
        for bookmark in subcategory_data.get("bookmarks", []):
            html += self.generate_bookmark_item(bookmark)
        html += f"{indent}</DL><p>\n"
        return html

    def generate_category(self, category_name: str, category_data: dict) -> str:
        self.folder_count += 1
        add_date = str(int(datetime.now().timestamp()))
        display_name = category_name
        html = f'    <DT><H3 ADD_DATE="{add_date}">{self.escape_html(display_name)}</H3>\n'
        html += "    <DL><p>\n"
        for sub_name, sub_data in category_data.get("subcategories", {}).items():
            html += self.generate_subcategory(sub_name, sub_data, level=2)
        for bookmark in category_data.get("bookmarks", []):
            html += self.generate_bookmark_item(bookmark)
        html += "    </DL><p>\n"
        return html

    def generate_html(self, hierarchy: Dict) -> str:
        html = "<!DOCTYPE NETSCAPE-Bookmark-file-1>\n"
        html += "<!-- This is an automatically generated file. DO NOT EDIT! -->\n"
        html += '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">\n'
        html += "<TITLE>Bookmarks</TITLE>\n<H1>Bookmarks</H1>\n<DL><p>\n"
        html += '    <DT><H3 PERSONAL_TOOLBAR_FOLDER="true">书签栏</H3>\n    <DL><p>\n'

        other_category = None
        categories = []
        for category, data in hierarchy.items():
            if category == "其他/未分类":
                other_category = (category, data)
            else:
                categories.append((category, data))
        categories.sort(key=lambda item: item[1]["count"], reverse=True)

        for category, data in categories:
            html += self.generate_category(category, data)
        if other_category:
            html += self.generate_category(*other_category)

        html += "    </DL><p>\n"
        html += "    <DT><H3>其他书签</H3>\n    <DL><p>\n    </DL><p>\n"
        html += "</DL><p>\n"
        return html


def main() -> int:
    parser = build_parser("生成书签 HTML")
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    config = load_config_from_args(args)
    logger = configure_logging(config, args.log_level)
    input_file = args.input or config.paths.clustering_file
    output_file = args.output or config.paths.html_output

    if not input_file.exists():
        print(f"错误: 输入文件不存在: {input_file}")
        return 1

    hierarchy = json.loads(input_file.read_text(encoding="utf-8"))["hierarchy"]
    generator = BookmarkHTMLGenerator()
    html_content = generator.generate_html(hierarchy)
    ensure_parent(output_file)
    output_file.write_text(html_content, encoding="utf-8")

    logger.info("步骤6完成: %s -> %s", input_file, output_file)
    print(f"✓ HTML生成完成: {output_file}")
    print(f"  总书签数: {generator.bookmark_count}")
    print(f"  总文件夹数: {generator.folder_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

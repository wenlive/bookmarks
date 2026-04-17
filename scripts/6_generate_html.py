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

    @classmethod
    def normalize_node(cls, node_name: str, node_data: dict) -> dict:
        children = []
        for child_name, child_data in (node_data.get("subcategories") or {}).items():
            children.append(cls.normalize_node(child_name, child_data))
        for child in node_data.get("children") or []:
            if isinstance(child, dict):
                children.append(cls.normalize_node(child.get("name", "未命名目录"), child))
        return {
            "name": node_data.get("name", node_name),
            "children": children,
            "bookmarks": node_data.get("bookmarks") or [],
            "count": node_data.get("count", len(node_data.get("bookmarks", []))),
            "node_type": node_data.get("node_type", "mixed"),
            "display_order": node_data.get("display_order"),
        }

    @staticmethod
    def sort_key(item: dict) -> tuple[int, int, str]:
        order = item.get("display_order")
        normalized_order = order if isinstance(order, int) else 10**6
        return (normalized_order, -item.get("count", 0), item.get("name", ""))

    def generate_bookmark_item(self, bookmark: dict, level: int = 3) -> str:
        self.bookmark_count += 1
        name = self.escape_html(bookmark.get("name", "未命名"))
        url = self.escape_html(bookmark.get("url", ""))
        add_date = bookmark.get("add_date") or str(int(datetime.now().timestamp()))
        icon = bookmark.get("icon", "")
        icon_attr = f' ICON="{self.escape_html(icon)}"' if icon else ""
        indent = "    " * level
        return f'{indent}<DT><A HREF="{url}" ADD_DATE="{add_date}"{icon_attr}>{name}</A>\n'

    def generate_folder(self, node: dict, level: int = 2) -> str:
        self.folder_count += 1
        indent = "    " * level
        add_date = str(int(datetime.now().timestamp()))
        html = f'{indent}<DT><H3 ADD_DATE="{add_date}">{self.escape_html(node.get("name", "未命名目录"))}</H3>\n'
        html += f"{indent}<DL><p>\n"
        children = sorted(node.get("children", []), key=self.sort_key)
        for child in children:
            html += self.generate_folder(child, level=level + 1)
        for bookmark in node.get("bookmarks", []):
            html += self.generate_bookmark_item(bookmark, level=level + 1)
        html += f"{indent}</DL><p>\n"
        return html

    def generate_html(self, hierarchy: Dict, review_hierarchy: Dict | None = None) -> str:
        html = "<!DOCTYPE NETSCAPE-Bookmark-file-1>\n"
        html += "<!-- This is an automatically generated file. DO NOT EDIT! -->\n"
        html += '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">\n'
        html += "<TITLE>Bookmarks</TITLE>\n<H1>Bookmarks</H1>\n<DL><p>\n"
        html += '    <DT><H3 PERSONAL_TOOLBAR_FOLDER="true">书签栏</H3>\n    <DL><p>\n'

        other_category = None
        categories = []
        for category, data in hierarchy.items():
            node = self.normalize_node(category, data)
            if category == "其他/未分类":
                other_category = node
            else:
                categories.append(node)
        categories.sort(key=self.sort_key)

        for node in categories:
            html += self.generate_folder(node)
        if other_category:
            html += self.generate_folder(other_category)
        if review_hierarchy:
            review_nodes = [self.normalize_node(category, data) for category, data in review_hierarchy.items()]
            review_nodes.sort(key=self.sort_key)
            for node in review_nodes:
                html += self.generate_folder(node)

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

    payload = json.loads(input_file.read_text(encoding="utf-8"))
    hierarchy = payload["hierarchy"]
    review_hierarchy = payload.get("review_hierarchy", {})
    generator = BookmarkHTMLGenerator()
    html_content = generator.generate_html(hierarchy, review_hierarchy)
    ensure_parent(output_file)
    output_file.write_text(html_content, encoding="utf-8")

    logger.info("步骤6完成: %s -> %s", input_file, output_file)
    print(f"✓ HTML生成完成: {output_file}")
    print(f"  总书签数: {generator.bookmark_count}")
    print(f"  总文件夹数: {generator.folder_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

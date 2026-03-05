#!/usr/bin/env python3
"""步骤6: 生成Chrome书签HTML"""
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List


class BookmarkHTMLGenerator:
    """生成符合Chrome标准的书签HTML"""

    def __init__(self):
        self.bookmark_count = 0
        self.folder_count = 0

    def escape_html(self, text: str) -> str:
        """转义HTML特殊字符"""
        if not text:
            return ""
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    def generate_bookmark_item(self, bookmark: dict) -> str:
        """生成单个书签项"""
        self.bookmark_count += 1

        name = self.escape_html(bookmark.get('name', '未命名'))
        url = bookmark.get('url', '')
        add_date = bookmark.get('add_date', str(int(datetime.now().timestamp())))
        icon = bookmark.get('icon', '')

        icon_attr = f' ICON="{self.escape_html(icon)}"' if icon else ''

        return f'        <DT><A HREF="{url}" ADD_DATE="{add_date}"{icon_attr}>{name}</A>\n'

    def generate_folder(self, name: str, bookmarks: List[dict], level: int = 2) -> str:
        """生成文件夹"""
        self.folder_count += 1

        indent = '    ' * level
        add_date = str(int(datetime.now().timestamp()))

        html = f'{indent}<DT><H3 ADD_DATE="{add_date}">{self.escape_html(name)}</H3>\n'
        html += f'{indent}<DL><p>\n'

        for bm in bookmarks:
            html += self.generate_bookmark_item(bm)

        html += f'{indent}</DL><p>\n'
        return html

    def generate_subcategory(self, category_name: str, subcategory_data: dict, level: int = 2) -> str:
        """生成子分类文件夹"""
        self.folder_count += 1

        # 提取子分类名称
        parts = category_name.split('/')
        display_name = parts[-1] if parts else category_name

        indent = '    ' * level
        add_date = str(int(datetime.now().timestamp()))

        html = f'{indent}<DT><H3 ADD_DATE="{add_date}">{self.escape_html(display_name)}</H3>\n'
        html += f'{indent}<DL><p>\n'

        # 添加书签
        for bm in subcategory_data.get('bookmarks', []):
            html += self.generate_bookmark_item(bm)

        html += f'{indent}</DL><p>\n'
        return html

    def generate_category(self, category_name: str, category_data: dict) -> str:
        """生成分类文件夹"""
        self.folder_count += 1

        # 提取主分类名称
        parts = category_name.split('/')
        display_name = parts[0] if parts else category_name

        add_date = str(int(datetime.now().timestamp()))

        html = f'    <DT><H3 ADD_DATE="{add_date}">{self.escape_html(display_name)}</H3>\n'
        html += f'    <DL><p>\n'

        # 如果有子分类
        if category_data.get('subcategories'):
            for sub_name, sub_data in category_data['subcategories'].items():
                html += self.generate_subcategory(sub_name, sub_data, level=2)

        # 如果有直接的书签
        if category_data.get('bookmarks'):
            for bm in category_data['bookmarks']:
                html += self.generate_bookmark_item(bm)

        html += f'    </DL><p>\n'
        return html

    def generate_html(self, hierarchy: Dict) -> str:
        """生成完整的书签HTML"""
        # HTML头部
        html = '<!DOCTYPE NETSCAPE-Bookmark-file-1>\n'
        html += '<!-- This is an automatically generated file.\n'
        html += '     It will be read and overwritten.\n'
        html += '     DO NOT EDIT! -->\n'
        html += '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">\n'
        html += '<TITLE>Bookmarks</TITLE>\n'
        html += '<H1>Bookmarks</H1>\n'
        html += '<DL><p>\n'

        # 书签栏
        html += '    <DT><H3 PERSONAL_TOOLBAR_FOLDER="true">书签栏</H3>\n'
        html += '    <DL><p>\n'

        # 添加"其他书签栏"分类（如果有）
        other_category = None
        sorted_categories = []

        for category, data in hierarchy.items():
            if category == "其他/未分类":
                other_category = (category, data)
            else:
                sorted_categories.append((category, data))

        # 按数量排序（除了"其他"）
        sorted_categories.sort(key=lambda x: x[1]['count'], reverse=True)

        # 添加排序后的分类
        for category, data in sorted_categories:
            html += self.generate_category(category, data)

        # 最后添加"其他"分类
        if other_category:
            html += self.generate_category(other_category[0], other_category[1])

        html += '    </DL><p>\n'

        # 其他书签（Chrome默认结构）
        html += '    <DT><H3>其他书签</H3>\n'
        html += '    <DL><p>\n'
        html += '    </DL><p>\n'

        html += '</DL><p>\n'

        return html


def main():
    input_file = Path("/Users/lipoqi/data/playground/bookmarks/data/clustering_result.json")
    output_file = Path("/Users/lipoqi/data/playground/bookmarks/output/organized_bookmarks.html")

    if not input_file.exists():
        print(f"错误: 输入文件不存在: {input_file}")
        return

    print("正在加载聚类结果...")
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    hierarchy = data['hierarchy']

    print(f"正在生成HTML，共 {len(hierarchy)} 个分类...")
    generator = BookmarkHTMLGenerator()
    html_content = generator.generate_html(hierarchy)

    # 保存文件
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html_content)

    print(f"\n✓ HTML生成完成: {output_file}")
    print(f"  总书签数: {generator.bookmark_count}")
    print(f"  总文件夹数: {generator.folder_count}")
    print(f"  文件大小: {output_file.stat().st_size / 1024:.2f} KB")
    print(f"\n可以导入Chrome: chrome://bookmarks/ → 导入书签")


if __name__ == "__main__":
    main()

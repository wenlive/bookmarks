#!/usr/bin/env python3
"""步骤2: 解析Chrome书签HTML文件（支持深度嵌套 - 使用html.parser）"""
import json
import hashlib
from pathlib import Path
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from collections import defaultdict


def parse_bookmarks(html_file: Path) -> dict:
    """解析Chrome书签HTML文件，支持深度嵌套"""
    with open(html_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # 使用html.parser而不是lxml，因为html.parser对Chrome书签格式更友好
    soup = BeautifulSoup(content, 'html.parser')

    bookmarks = []
    seen_urls = set()
    bookmark_id = 0
    all_folders = set()

    def get_url_hash(url: str) -> str:
        """生成URL的hash用于去重"""
        return hashlib.md5(url.encode()).hexdigest()

    def get_folder_path(a_tag):
        """通过向上遍历获取书签的完整文件夹路径"""
        path = []
        current = a_tag.parent

        while current:
            # 查找最近的H3祖先（在同一DT或祖先DT中）
            if current.name == 'dt':
                h3 = current.find('h3', recursive=False)
                if h3:
                    folder_name = h3.get_text(strip=True)
                    if folder_name and folder_name not in ['书签栏', 'Bookmarks Bar', 'Bookmarks bar', '其他书签', 'Other Bookmarks']:
                        path.insert(0, folder_name)
                        all_folders.add('/'.join(path))

            current = current.parent

        return path

    # 找到所有书签链接
    all_links = soup.find_all('a')
    print(f"找到 {len(all_links)} 个链接标签")

    for a in all_links:
        url = a.get('href', '')
        if not url or url.startswith('javascript:'):
            continue

        # URL去重
        url_hash = get_url_hash(url)
        if url_hash in seen_urls:
            continue
        seen_urls.add(url_hash)

        # 提取域名
        try:
            domain = urlparse(url).netloc
        except:
            domain = ''

        # 获取文件夹路径
        folder_path = get_folder_path(a)

        bookmark = {
            "id": f"bookmark_{bookmark_id}",
            "name": a.get_text(strip=True),
            "url": url,
            "domain": domain,
            "original_folder_path": folder_path,
            "add_date": a.get('add_date', ''),
            "icon": a.get('icon', ''),
            "metadata": {}
        }
        bookmarks.append(bookmark)
        bookmark_id += 1

    # 统计信息
    domain_stats = defaultdict(int)
    folder_stats = defaultdict(int)

    for bm in bookmarks:
        if bm['domain']:
            domain_stats[bm['domain']] += 1
        folder_path = '/'.join(bm['original_folder_path'])
        if folder_path:
            folder_stats[folder_path] += 1

    result = {
        "bookmarks": bookmarks,
        "stats": {
            "total_bookmarks": len(bookmarks),
            "total_folders": len(all_folders),
            "unique_domains": len(domain_stats),
            "top_domains": dict(sorted(domain_stats.items(), key=lambda x: x[1], reverse=True)[:20]),
            "top_folders": dict(sorted(folder_stats.items(), key=lambda x: x[1], reverse=True)[:20]),
            "all_folders": sorted(list(all_folders))
        }
    }

    return result


def main():
    input_file = Path("/Users/lipoqi/data/playground/bookmarks/data/bookmarks_2026_3_4.html")
    output_file = Path("/Users/lipoqi/data/playground/bookmarks/data/parsed_bookmarks.json")

    if not input_file.exists():
        print(f"错误: 输入文件不存在: {input_file}")
        return

    print("正在解析书签文件...")
    result = parse_bookmarks(input_file)

    # 保存结果
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n✓ 解析完成: {output_file}")
    print(f"  总书签数: {result['stats']['total_bookmarks']}")
    print(f"  总文件夹数: {result['stats']['total_folders']}")
    print(f"  唯一域名数: {result['stats']['unique_domains']}")
    print(f"\n前10个域名:")
    for domain, count in list(result['stats']['top_domains'].items())[:10]:
        print(f"  {domain}: {count}")
    print(f"\n前15个文件夹:")
    for folder, count in list(result['stats']['top_folders'].items())[:15]:
        print(f"  {folder}: {count}")


if __name__ == "__main__":
    main()

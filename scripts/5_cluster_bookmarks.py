#!/usr/bin/env python3
"""步骤5: 聚类分析与层级构建"""
import json
from pathlib import Path
from collections import defaultdict
from typing import List, Dict
import re


class BookmarkClusterer:
    """简化版聚类器 - 不依赖scikit-learn"""

    def __init__(self, min_cluster_size: int = 10):
        self.min_cluster_size = min_cluster_size

    def extract_keywords(self, text: str) -> List[str]:
        """提取关键词（简化版）"""
        # 移除特殊字符
        text = re.sub(r'[^\w\s\u4e00-\u9fff]', ' ', text.lower())
        words = text.split()

        # 过滤停用词（简化版）
        stopwords = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
                     'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were', 'been',
                     '的', '了', '和', '是', '在', '有', '个', '我', '他', '她', '它'}

        keywords = [w for w in words if w not in stopwords and len(w) > 1]
        return keywords

    def cluster_by_keywords(self, bookmarks: List[dict]) -> Dict[str, List[dict]]:
        """基于关键词的简单聚类"""
        keyword_groups = defaultdict(list)

        for bm in bookmarks:
            # 合并文本特征
            text = f"{bm.get('name', '')} {bm.get('metadata', {}).get('description', '')} {bm.get('metadata', {}).get('keywords', '')}"
            keywords = self.extract_keywords(text)

            # 使用最重要的关键词作为分组
            if keywords:
                primary_keyword = keywords[0]
                keyword_groups[primary_keyword].append(bm)
            else:
                keyword_groups['其他'].append(bm)

        # 合并小组
        final_groups = {}
        others = []

        for keyword, group in keyword_groups.items():
            if len(group) >= self.min_cluster_size:
                final_groups[keyword] = group
            else:
                others.extend(group)

        if others:
            final_groups['其他'] = others

        return final_groups

    def cluster_by_domain(self, bookmarks: List[dict]) -> Dict[str, List[dict]]:
        """基于域名的聚类"""
        domain_groups = defaultdict(list)

        for bm in bookmarks:
            domain = bm.get('domain', '其他')
            if domain:
                domain_groups[domain].append(bm)
            else:
                domain_groups['其他'].append(bm)

        return dict(domain_groups)

    def build_hierarchy(self, bookmarks: List[dict], category: str) -> Dict:
        """构建层级结构"""
        if len(bookmarks) <= 20:
            # 数量较少，直接返回
            return {
                "category": category,
                "subcategories": {},
                "bookmarks": bookmarks,
                "count": len(bookmarks)
            }

        # 数量较多，进行聚类
        # 先尝试按域名聚类
        domain_clusters = self.cluster_by_domain(bookmarks)

        # 如果域名聚类效果好，使用域名聚类
        if len([g for g in domain_clusters.values() if len(g) >= 5]) >= 2:
            subcategories = {}
            ungrouped = []

            for domain, group in domain_clusters.items():
                if len(group) >= 5:
                    subcategories[f"{category}/{domain}"] = {
                        "bookmarks": group,
                        "count": len(group)
                    }
                else:
                    ungrouped.extend(group)

            return {
                "category": category,
                "subcategories": subcategories,
                "bookmarks": ungrouped,  # 未分组的书签放在父级
                "count": len(bookmarks)
            }

        # 否则使用关键词聚类
        keyword_clusters = self.cluster_by_keywords(bookmarks)
        subcategories = {}
        ungrouped = []

        for keyword, group in keyword_clusters.items():
            if keyword != '其他' and len(group) >= self.min_cluster_size:
                subcategories[f"{category}/{keyword}"] = {
                    "bookmarks": group,
                    "count": len(group)
                }
            else:
                ungrouped.extend(group)

        return {
            "category": category,
            "subcategories": subcategories,
            "bookmarks": ungrouped,  # 未分组的书签放在父级
            "count": len(bookmarks)
        }


def main():
    input_file = Path("/Users/lipoqi/data/playground/bookmarks/data/classified_bookmarks.json")
    output_file = Path("/Users/lipoqi/data/playground/bookmarks/data/clustering_result.json")

    if not input_file.exists():
        print(f"错误: 输入文件不存在: {input_file}")
        return

    print("正在加载分类数据...")
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    bookmarks = data['bookmarks']

    # 按分类分组
    category_groups = defaultdict(list)
    for bm in bookmarks:
        category = bm['classification']['category']
        category_groups[category].append(bm)

    print(f"正在对 {len(category_groups)} 个分类进行聚类分析...")

    clusterer = BookmarkClusterer(min_cluster_size=10)
    hierarchy = {}

    for category, group in category_groups.items():
        hierarchy[category] = clusterer.build_hierarchy(group, category)

    # 统计信息
    stats = {
        "total_categories": len(hierarchy),
        "category_sizes": {cat: data['count'] for cat, data in hierarchy.items()},
        "subcategories_count": sum(1 for data in hierarchy.values() if data['subcategories'])
    }

    output_data = {
        "hierarchy": hierarchy,
        "stats": stats
    }

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print(f"\n✓ 聚类完成: {output_file}")
    print(f"\n分类统计:")
    for category, size in sorted(stats['category_sizes'].items(), key=lambda x: x[1], reverse=True)[:15]:
        print(f"  {category}: {size}")
    print(f"\n有子分类的分类数: {stats['subcategories_count']}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""步骤4: 智能分类书签"""
import json
import re
from pathlib import Path
from typing import Dict, List
from collections import defaultdict


class BookmarkClassifier:
    def __init__(self, rules_file: Path):
        with open(rules_file, 'r', encoding='utf-8') as f:
            self.rules = json.load(f)

        self.categories = self.rules['categories']
        self.default_category = self.rules['default_category']
        self.scoring = self.rules['scoring']

    def calculate_domain_score(self, bookmark: dict, category_rules: dict) -> int:
        """计算域名匹配分数"""
        domain = bookmark.get('domain', '').lower()
        if not domain:
            return 0

        # 精确匹配
        for pattern in category_rules.get('domains', []):
            if pattern.lower() in domain:
                return 100

        return 0

    def calculate_keyword_score(self, bookmark: dict, category_rules: dict) -> int:
        """计算关键词匹配分数"""
        text = f"{bookmark.get('name', '')} {bookmark.get('metadata', {}).get('keywords', '')} {bookmark.get('metadata', {}).get('description', '')}"
        text_lower = text.lower()

        score = 0
        keywords = category_rules.get('keywords', [])

        for keyword in keywords:
            if keyword.lower() in text_lower:
                score += 20  # 每个关键词20分

        return min(score, 100)  # 最高100分

    def calculate_title_score(self, bookmark: dict, category_rules: dict) -> int:
        """计算标题模式匹配分数"""
        title = bookmark.get('name', '')
        patterns = category_rules.get('title_patterns', [])

        for pattern in patterns:
            if re.search(pattern, title, re.IGNORECASE):
                return 80

        return 0

    def calculate_folder_score(self, bookmark: dict, category_rules: dict) -> int:
        """根据原始文件夹路径计算分数"""
        folder_path = bookmark.get('original_folder_path', [])
        if not folder_path:
            return 0

        folder_str = '/'.join(folder_path)

        # 优先使用folder_keywords
        folder_keywords = category_rules.get('folder_keywords', [])
        if folder_keywords:
            for keyword in folder_keywords:
                if keyword.lower() in folder_str.lower():
                    return 80  # 文件夹关键词匹配给高分

        return 0

    def calculate_content_score(self, bookmark: dict, category_rules: dict) -> int:
        """计算网页内容匹配分数"""
        metadata = bookmark.get('metadata', {})
        content = f"{metadata.get('description', '')} {metadata.get('h1', '')} {metadata.get('content_preview', '')}"
        content_lower = content.lower()

        score = 0
        keywords = category_rules.get('keywords', [])

        for keyword in keywords:
            if keyword.lower() in content_lower:
                score += 10

        return min(score, 50)  # 最高50分

    def classify_bookmark(self, bookmark: dict) -> tuple:
        """对单个书签进行分类"""
        scores = {}

        for category_name, category_rules in self.categories.items():
            # 计算各维度分数
            domain_score = self.calculate_domain_score(bookmark, category_rules)
            keyword_score = self.calculate_keyword_score(bookmark, category_rules)
            title_score = self.calculate_title_score(bookmark, category_rules)
            folder_score = self.calculate_folder_score(bookmark, category_rules)
            content_score = self.calculate_content_score(bookmark, category_rules)

            # 加权总分
            total_score = (
                domain_score * self.scoring['domain_weight'] / 100 +
                (keyword_score + title_score) * self.scoring['keyword_weight'] / 100 +
                folder_score * self.scoring['folder_weight'] / 100 +
                content_score * self.scoring['content_weight'] / 100
            )

            if total_score > 0:
                scores[category_name] = {
                    'total': total_score,
                    'domain': domain_score,
                    'keyword': keyword_score,
                    'title': title_score,
                    'folder': folder_score,
                    'content': content_score
                }

        if not scores:
            return self.default_category, 0, {}

        # 选择最高分的分类
        best_category = max(scores.items(), key=lambda x: x[1]['total'])

        # 检查是否达到最低阈值
        if best_category[1]['total'] < self.scoring['min_score']:
            return self.default_category, best_category[1]['total'], scores

        return best_category[0], best_category[1]['total'], scores

    def classify_all(self, bookmarks: list) -> list:
        """分类所有书签"""
        results = []
        category_stats = defaultdict(int)
        confirm_needed = []

        total = len(bookmarks)
        for idx, bm in enumerate(bookmarks):
            # 显示进度
            if (idx + 1) % 50 == 0 or idx == 0:
                print(f"  处理进度: {idx + 1}/{total} ({(idx + 1) * 100 // total}%)")

            category, score, all_scores = self.classify_bookmark(bm)

            bm_classified = bm.copy()
            bm_classified['classification'] = {
                'category': category,
                'score': score,
                'needs_confirmation': score < self.scoring['confirm_threshold'],
                'all_scores': all_scores
            }

            results.append(bm_classified)
            category_stats[category] += 1

            if score < self.scoring['confirm_threshold'] and category != self.default_category:
                confirm_needed.append(bm_classified['id'])

        print(f"  处理进度: {total}/{total} (100%)")
        return results, dict(category_stats), confirm_needed


def main():
    input_file = Path("/Users/lipoqi/data/playground/bookmarks/data/bookmarks_with_info.json")
    rules_file = Path("/Users/lipoqi/data/playground/bookmarks/data/category_rules.json")
    output_file = Path("/Users/lipoqi/data/playground/bookmarks/data/classified_bookmarks.json")

    if not input_file.exists():
        print(f"错误: 输入文件不存在: {input_file}")
        return

    if not rules_file.exists():
        print(f"错误: 分类规则文件不存在: {rules_file}")
        return

    print("正在加载书签数据...")
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    bookmarks = data['bookmarks']

    print(f"正在分类 {len(bookmarks)} 个书签...")
    classifier = BookmarkClassifier(rules_file)
    classified_bookmarks, category_stats, confirm_needed = classifier.classify_all(bookmarks)

    # 保存结果
    output_data = {
        "bookmarks": classified_bookmarks,
        "stats": {
            "total_bookmarks": len(classified_bookmarks),
            "category_distribution": category_stats,
            "confirm_needed_count": len(confirm_needed),
            "confirm_needed_ids": confirm_needed[:50]  # 只保存前50个
        }
    }

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print(f"\n✓ 分类完成: {output_file}")
    print(f"\n分类统计:")
    for category, count in sorted(category_stats.items(), key=lambda x: x[1], reverse=True):
        percentage = count * 100 / len(bookmarks)
        print(f"  {category}: {count} ({percentage:.1f}%)")

    print(f"\n待确认书签数: {len(confirm_needed)}")


if __name__ == "__main__":
    main()

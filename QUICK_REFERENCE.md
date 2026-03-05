# 快速参考指南

## 一键运行

```bash
# 最快方式
./organize.sh data/bookmarks.html
```

## 分步运行

```bash
# 步骤1: 复制书签
python3 scripts/1_copy_bookmark.py

# 步骤2: 解析
python3 scripts/2_parse_bookmarks.py

# 步骤3: 抓取网页信息（最慢，可跳过如果已有）
python3 scripts/3_fetch_webpage_info.py

# 步骤4: 分类
python3 scripts/4_classify_bookmarks.py

# 步骤5: 聚类
python3 scripts/5_cluster_bookmarks.py

# 步骤6: 生成HTML
python3 scripts/6_generate_html.py
```

## 常用命令

```bash
# 查看统计信息
cat data/parsed_bookmarks.json | grep "total_bookmarks"
cat data/classified_bookmarks.json | grep "category_distribution"

# 检查抓取成功率
cat data/bookmarks_with_info.json | grep "success_rate"

# 查看生成的分类
cat data/clustering_result.json | grep "category_sizes"
```

## 快速调整

### 提高抓取速度
编辑 `scripts/3_fetch_webpage_info.py`:
```python
CONCURRENT_LIMIT = 25  # 提高并发
DELAY = 0.5            # 减少延迟
```

### 添加自定义分类
编辑 `data/category_rules.json`:
```json
{
  "分类名称": {
    "domains": ["example.com"],
    "keywords": ["关键词"],
    "title_patterns": ["模式.*"],
    "folder_keywords": ["文件夹"]
  }
}
```

### 调整分类严格度
编辑 `data/category_rules.json`:
```json
{
  "scoring": {
    "min_score": 10  # 降低=更宽松
  }
}
```

## 文件位置

- **输入**: `data/bookmarks_*.html`
- **输出**: `output/organized_bookmarks.html`
- **规则**: `data/category_rules.json`
- **配置**: `skill_config.json`
- **日志**: `logs/bookmarks_organizer.log`

## 性能参考

| 书签数量 | 步骤3耗时 | 总耗时 | 成功率 |
|---------|----------|--------|--------|
| 500     | ~8分钟   | ~10分钟 | 95%+ |
| 1000    | ~15分钟  | ~18分钟 | 92%+ |
| 2000    | ~30分钟  | ~35分钟 | 90%+ |
| 5000    | ~75分钟  | ~80分钟 | 88%+ |

## 故障排查

```bash
# 检查Python版本（需要3.8+）
python3 --version

# 检查依赖
python3 -c "import bs4, aiohttp; print('OK')"

# 重新安装依赖
pip install --upgrade -r requirements.txt

# 检查文件是否存在
ls -lh data/*.json data/*.html

# 查看错误日志
tail -100 logs/bookmarks_organizer.log
```

## 导入Chrome

1. `chrome://bookmarks/`
2. 右上角 `⋮` → `导入书签`
3. 选择 `output/organized_bookmarks.html`
4. 完成！

## 获取帮助

- 📖 完整文档: `README.md`
- 🐛 问题反馈: GitHub Issues
- 💬 讨论: GitHub Discussions

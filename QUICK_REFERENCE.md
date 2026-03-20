# 快速参考指南

## 一键运行

```bash
./organize.sh data/bookmarks.html skill_config.json
```

## 分步运行

```bash
python3 scripts/1_copy_bookmark.py --config skill_config.json --source data/bookmarks.html
python3 scripts/2_parse_bookmarks.py --config skill_config.json
python3 scripts/3_fetch_webpage_info.py --config skill_config.json
python3 scripts/4_classify_bookmarks.py --config skill_config.json
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json
```

## 关键输出

- 输出 HTML：`output/organized_bookmarks.html`
- 重复 URL 报告：`output/reports/duplicates.json`
- 失效链接报告：`output/reports/broken_links.json`
- 待确认报告：`output/reports/needs_confirmation.json`
- 日志：`logs/bookmarks_organizer.log`

## 快速检查

```bash
python3 -m compileall scripts tests
pytest
```

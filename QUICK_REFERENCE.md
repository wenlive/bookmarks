---
name: bookmark-organizer-quick-reference
description: 极简命令卡。解释看 RUNBOOK，服务契约看 SERVICE_CONTRACT。
---

# Quick Reference

解释看 `RUNBOOK.md`，服务契约看 `SERVICE_CONTRACT.md`。

## 普通运行

```bash
./organize.sh data/bookmarks.html skill_config.json
```

## 代理优先运行

```bash
export https_proxy=http://127.0.0.1:7897
export http_proxy=http://127.0.0.1:7897
export all_proxy=socks5://127.0.0.1:7897

./organize.sh data/bookmarks.html skill_config.json --use-proxy --trust-env --direct-retry-after-proxy
```

## 清理模式

```bash
./organize.sh data/bookmarks.html skill_config.json --clear-fetch-cache
./organize.sh data/bookmarks.html skill_config.json --reset-all
./organize.sh data/bookmarks.html skill_config.json --force-refetch
```

## 分步执行

```bash
python3 scripts/1_copy_bookmark.py --config skill_config.json --source data/bookmarks.html
python3 scripts/2_parse_bookmarks.py --config skill_config.json
python3 scripts/3_fetch_webpage_info.py --config skill_config.json
python3 scripts/4_classify_bookmarks.py --config skill_config.json
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json
```

## 局部重跑

```bash
python3 scripts/4_classify_bookmarks.py --config skill_config.json
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json
```

```bash
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json
```

```bash
python3 scripts/6_generate_html.py --config skill_config.json
```

## taxonomy bootstrap

```bash
./organize.sh data/bookmarks.html skill_config.json --bootstrap-taxonomy
python3 scripts/apply_taxonomy_response.py --config skill_config.json --response data/generated/taxonomy_response.json
python3 scripts/4_classify_bookmarks.py --config skill_config.json
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json
```

Bootstrap 需要一起读：

```text
output/reports/taxonomy_bootstrap_prompt.md
output/reports/taxonomy_bootstrap_clusters.json
```

## taxonomy follow-up

```bash
python3 scripts/generate_taxonomy_followup.py --config skill_config.json
python3 scripts/apply_taxonomy_response.py --config skill_config.json --response data/generated/taxonomy_followup_response.json --clusters output/reports/taxonomy_followup_candidates.json --merge-existing
python3 scripts/4_classify_bookmarks.py --config skill_config.json
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json
```

Follow-up 需要一起读：

```text
output/reports/taxonomy_followup_prompt.md
output/reports/taxonomy_followup_candidates.json
```

## 关键文件

```text
skill_config.json
data/generated/user_taxonomy.json
data/generated/bookmark_taxonomy_assignments.json
data/bookmarks_with_info.json
output/organized_bookmarks.html
output/reports/
```

## 校验

```bash
python3 -m py_compile scripts/common.py scripts/1_copy_bookmark.py scripts/2_parse_bookmarks.py scripts/3_fetch_webpage_info.py scripts/4_classify_bookmarks.py scripts/5_cluster_bookmarks.py scripts/6_generate_html.py scripts/generate_taxonomy_bootstrap.py scripts/generate_taxonomy_followup.py scripts/apply_taxonomy_response.py scripts/reset_pipeline_state.py
python3 -c "import json; json.load(open('skill_config.json'))"
pytest -q
git diff --check
```

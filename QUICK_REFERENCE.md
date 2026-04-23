---
name: bookmark-organizer-quick-reference
description: Fast command reference for operating and validating the Chrome bookmark organizer.
---

# Quick Reference

## Canonical Run

```bash
./organize.sh data/bookmarks.html skill_config.json
```

## Run With Proxy

```bash
export https_proxy=http://127.0.0.1:7897
export http_proxy=http://127.0.0.1:7897
export all_proxy=socks5://127.0.0.1:7897

./organize.sh data/bookmarks.html skill_config.json --use-proxy --trust-env
```

## Reset Modes

```bash
# Retry from a clean fetch cache.
./organize.sh data/bookmarks.html skill_config.json --clear-fetch-cache

# Remove all generated pipeline state and rebuild.
./organize.sh data/bookmarks.html skill_config.json --reset-all

# Ignore successful fetch cache and refetch everything.
./organize.sh data/bookmarks.html skill_config.json --force-refetch
```

## Step-by-step

```bash
python3 scripts/1_copy_bookmark.py --config skill_config.json --source data/bookmarks.html
python3 scripts/2_parse_bookmarks.py --config skill_config.json
python3 scripts/3_fetch_webpage_info.py --config skill_config.json
python3 scripts/4_classify_bookmarks.py --config skill_config.json
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json
```

## Partial Reruns

```bash
# Rules changed.
python3 scripts/4_classify_bookmarks.py --config skill_config.json
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json

# Clustering/display changed.
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json

# HTML rendering changed.
python3 scripts/6_generate_html.py --config skill_config.json
```

## Key Files

| Path | Meaning |
| --- | --- |
| `skill_config.json` | Operational config |
| `data/category_rules.json` | Shared default rules |
| `data/category_rules_overrides.json` | Personal/local rule extensions |
| `data/bookmarks.html` | Copied Chrome export |
| `data/bookmarks_with_info.json` | Fetch cache and enriched metadata |
| `data/classified_bookmarks.json` | Classification result |
| `data/clustering_result.json` | Clustered hierarchy payload |
| `output/organized_bookmarks.html` | Chrome import output |

## Reports

```text
output/reports/duplicates.json
output/reports/broken_links.json
output/reports/needs_confirmation.json
output/reports/review_queue.json
output/reports/rule_suggestions.json
output/reports/quality_report.json
```

## Quality Check

```bash
python3 -m py_compile scripts/common.py scripts/1_copy_bookmark.py scripts/2_parse_bookmarks.py scripts/3_fetch_webpage_info.py scripts/4_classify_bookmarks.py scripts/5_cluster_bookmarks.py scripts/6_generate_html.py scripts/reset_pipeline_state.py
python3 -c "import json; [json.load(open(path)) for path in ['data/category_rules.json','data/category_rules_overrides.json','skill_config.json']]"
pytest -q
git diff --check
```

## Quality Metrics To Watch

In `output/reports/quality_report.json`:

```text
folder_only_classification_count == 0
low_confidence_normal_category_count == 0
generic_platform_domain_suggestion_count == 0
largest_generic_platform_cluster_size is not unexpectedly large
```

## Agent Rules

- Treat `README.md` as the design contract.
- Treat `RUNBOOK.md` as the operational procedure.
- Treat `AGENTS.md` as the agent implementation guide.
- Do not trust old Chrome folders as topic evidence.
- Do not classify by broad platform domain.
- Preserve broken links; mirror them to `待审阅`.
- Prefer precise overrides in `data/category_rules_overrides.json`.
- Rerun only the downstream steps required by the change.

## Output Shape

```text
书签栏
├── 技术主题
├── 工具与平台
├── 学习与资料
├── 个人与生活
├── 待整理
├── 发现主题
└── 待审阅
```

---
name: bookmark-organizer-quick-reference
description: Fast command reference for operating and validating the Chrome bookmark organizer.
---

# Quick Reference

## Canonical Run

```bash
./organize.sh data/bookmarks.html skill_config.json
```

## Preferred Proxy Run

```bash
export https_proxy=http://127.0.0.1:7897
export http_proxy=http://127.0.0.1:7897
export all_proxy=socks5://127.0.0.1:7897

./organize.sh data/bookmarks.html skill_config.json --use-proxy --trust-env --direct-retry-after-proxy
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

Proxy-first fetch with automatic direct retry:

```bash
python3 scripts/3_fetch_webpage_info.py --config skill_config.json --use-proxy --trust-env --direct-retry-after-proxy
```

## Partial Reruns

```bash
# Rules or classification logic changed.
python3 scripts/4_classify_bookmarks.py --config skill_config.json
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json

# Clustering or display logic changed.
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
| `data/category_rules_overrides.json` | Local rule extensions |
| `data/bookmarks.html` | Copied Chrome export |
| `data/bookmarks_with_info.json` | Fetch cache and enriched metadata |
| `data/classified_bookmarks.json` | Classification result |
| `data/clustering_result.json` | Cluster hierarchy payload |
| `output/organized_bookmarks.html` | Chrome import output |

## Reports

```text
output/reports/duplicates.json
output/reports/broken_links.json
output/reports/needs_confirmation.json
output/reports/review_queue.json
output/reports/rule_suggestions.json
output/reports/quality_report.json
output/reports/signal_audit.json
```

## Report Meanings

- `broken_links.json`: HTTP-broken rows only
- `review_queue.json`: all review-required fetch outcomes
- `needs_confirmation.json`: rule-gap, fetch-blocked, or low-confidence classification output
- `rule_suggestions.json`: `add_alias`, `add_specific_domain`, `create_topic`, `split_mixed_cluster`, `investigate_fetch_failures`
- `quality_report.json`: discovery, mixed, generic-platform, flat-root, and review-hotspot summaries
- `signal_audit.json`: collected vs consumed signals plus unused high-value fields

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
fetch_blocked_discovery_cluster_count == 0
mixed_cluster_count does not unexpectedly spike
normal_root_direct_bookmark_share does not grow unexpectedly
```

## Agent Rules

- Treat `README.md` as the design contract.
- Treat `RUNBOOK.md` as the operating procedure.
- Treat `AGENTS.md` as the agent implementation guide.
- Treat `TODO_RUNTIME_FOLLOWUP.md` as the latest real-run continuation baseline.
- Do not trust old Chrome folders as topic evidence.
- Do not classify by broad platform domain.
- Preserve broken links; mirror them to `待审阅`.
- Prefer precise overrides over broad default-rule edits.
- Rerun only the downstream steps required by the change.

## Default Output Shape

```text
书签栏
├── 技术主题
├── 工具与平台
├── 学习与资料
├── 个人与生活
├── 待整理
├── 发现主题
└── 待审阅  # only when review items exist
```

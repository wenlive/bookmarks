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
# Generated taxonomy or classification logic changed.
python3 scripts/4_classify_bookmarks.py --config skill_config.json
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json

# Clustering or display logic changed.
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json

# HTML rendering changed.
python3 scripts/6_generate_html.py --config skill_config.json
```

## Taxonomy Bootstrap

```bash
# Generate the prompt and supporting cluster evidence for an external LLM.
./organize.sh data/bookmarks.html skill_config.json --bootstrap-taxonomy

# Apply the strict JSON response from the external LLM.
python3 scripts/apply_taxonomy_response.py --config skill_config.json --response data/generated/taxonomy_response.json
```

## Taxonomy Follow-up

```bash
# Generate one larger rule-gap follow-up package for your own external LLM or code agent.
python3 scripts/generate_taxonomy_followup.py --config skill_config.json

# Merge the strict JSON response back into the generated taxonomy and assignments.
python3 scripts/apply_taxonomy_response.py --config skill_config.json --response data/generated/taxonomy_followup_response.json --clusters output/reports/taxonomy_followup_candidates.json --merge-existing
python3 scripts/4_classify_bookmarks.py --config skill_config.json
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json
```

## Key Files

| Path | Meaning |
| --- | --- |
| `skill_config.json` | Operational config |
| `data/generated/user_taxonomy.json` | Generated user taxonomy, ignored by git |
| `data/generated/bookmark_taxonomy_assignments.json` | Generated bookmark assignment constraints, ignored by git |
| `data/bookmarks.html` | Copied Chrome export |
| `data/bookmarks_with_info.json` | Fetch cache and enriched metadata |
| `data/classified_bookmarks.json` | Classification result |
| `data/clustering_result.json` | Cluster hierarchy payload |
| `output/organized_bookmarks.html` | Chrome import output |

## Reports

```text
output/reports/duplicates.json
output/reports/broken_links.json
output/reports/fetch_hotspots.json
output/reports/needs_confirmation.json
output/reports/review_queue.json
output/reports/rule_suggestions.json
output/reports/quality_report.json
output/reports/signal_audit.json
output/reports/taxonomy_bootstrap_prompt.md
output/reports/taxonomy_bootstrap_clusters.json
output/reports/taxonomy_followup_prompt.md
output/reports/taxonomy_followup_candidates.json
```

## Report Meanings

- `broken_links.json`: HTTP-broken rows only
- `fetch_hotspots.json`: domain-level fetch review hotspots plus proxy/direct pass deltas
- `review_queue.json`: all review-required fetch outcomes
- `needs_confirmation.json`: rule-gap, fetch-blocked, or low-confidence classification output
- `rule_suggestions.json`: `add_alias`, `add_specific_domain`, `create_topic`, `split_mixed_cluster`, `investigate_fetch_failures`
- `quality_report.json`: discovery, mixed, generic-platform, flat-root, and review-hotspot summaries
- `signal_audit.json`: collected vs consumed signals plus unused high-value fields

## Quality Check

```bash
python3 -m py_compile scripts/common.py scripts/1_copy_bookmark.py scripts/2_parse_bookmarks.py scripts/3_fetch_webpage_info.py scripts/4_classify_bookmarks.py scripts/5_cluster_bookmarks.py scripts/6_generate_html.py scripts/generate_taxonomy_bootstrap.py scripts/generate_taxonomy_followup.py scripts/apply_taxonomy_response.py scripts/reset_pipeline_state.py
python3 -c "import json; json.load(open('skill_config.json'))"
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
- Treat `DESIGN_CONSTRAINTS.md` as the persistent product constraint file.
- Treat `RUNBOOK.md` as the operating procedure.
- Treat `AGENTS.md` as the agent implementation guide.
- Treat `TODO_RUNTIME_FOLLOWUP.md` as the latest real-run continuation baseline.
- Do not trust old Chrome folders as topic evidence.
- Do not classify by broad platform domain.
- Preserve broken links; mirror them to `待审阅`.
- Prefer generated user taxonomy constraints over broad built-in defaults.
- Prefer exported prompt plus imported JSON workflows over built-in live LLM API calls.
- Preserve both proxy and direct fetch workflows, and surface proxy env setup when real runs need it.
- Optimize the final visible hierarchy for bookmark-bar browsing, not only clustering purity.
- Rerun only the downstream steps required by the change.

## Default Output Shape

```text
书签栏
├── 数据库 / 编程语言 / ...
├── 待整理
├── 发现主题  # or grouped under 待整理 when extremely small
└── 待审阅  # only when review items exist
```

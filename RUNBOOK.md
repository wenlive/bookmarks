---
name: bookmark-organizer-runbook
description: Follow this runbook to operate the bookmark pipeline, recover from fetch problems, inspect reports, and decide when to rerun individual stages.
---

# Runbook

## Operating Contract

Use this document for day-to-day execution. For design intent and extension rules, read `README.md` and `AGENTS.md`.

Default paths:

```text
input:  data/bookmarks.html
config: skill_config.json
output: output/organized_bookmarks.html
```

## Recommended Real Run

When network access matters, the current preferred command is:

```bash
export https_proxy=http://127.0.0.1:7897
export http_proxy=http://127.0.0.1:7897
export all_proxy=socks5://127.0.0.1:7897

./organize.sh data/bookmarks.html skill_config.json --use-proxy --trust-env --direct-retry-after-proxy
```

Why this is preferred:

- the proxy-first pass captures the majority of reachable pages
- the built-in direct retry only revisits unresolved rows
- final fetch stats include `multi_pass_mode`, `pass_summaries`, and `pass_deltas`

## Preflight

Run these checks before a real full run:

```bash
python3 --version
python3 -c "import bs4, aiohttp"
python3 -c "import urllib.request; print(urllib.request.urlopen('https://example.com', timeout=10).status)"
```

If dependency import fails:

```bash
python3 -m pip install -r requirements.txt
```

If your local Python environment has SSL or network issues but `conda` is known-good, use:

```bash
conda run -n base ./organize.sh data/bookmarks.html skill_config.json
```

## Decision Table

| Situation | Command |
| --- | --- |
| First normal run | `./organize.sh data/bookmarks.html skill_config.json` |
| Network needs proxy | `./organize.sh data/bookmarks.html skill_config.json --use-proxy --trust-env --direct-retry-after-proxy` |
| Want to refresh all pages | `./organize.sh data/bookmarks.html skill_config.json --force-refetch` |
| Want to discard only fetch cache | `./organize.sh data/bookmarks.html skill_config.json --clear-fetch-cache` |
| Want to discard all generated state | `./organize.sh data/bookmarks.html skill_config.json --reset-all` |
| Need a first-pass taxonomy prompt | `./organize.sh data/bookmarks.html skill_config.json --bootstrap-taxonomy` |
| Applied external taxonomy response | `python3 scripts/apply_taxonomy_response.py --config skill_config.json --response data/generated/taxonomy_response.json`, then rerun steps 4, 5, 6 |
| Changed generated taxonomy or classification logic | rerun steps 4, 5, 6 |
| Changed only clustering/display logic | rerun steps 5, 6 |
| Changed only HTML generation | rerun step 6 |

## Normal Run

```bash
./organize.sh data/bookmarks.html skill_config.json
```

Expected generated files:

```text
data/parsed_bookmarks.json
data/bookmarks_with_info.json
data/classified_bookmarks.json
data/clustering_result.json
output/organized_bookmarks.html
output/reports/duplicates.json
output/reports/broken_links.json
output/reports/needs_confirmation.json
output/reports/review_queue.json
output/reports/rule_suggestions.json
output/reports/quality_report.json
output/reports/signal_audit.json
logs/bookmarks_organizer.log
```

## Proxy Run

Set proxy variables:

```bash
export https_proxy=http://127.0.0.1:7897
export http_proxy=http://127.0.0.1:7897
export all_proxy=socks5://127.0.0.1:7897
```

Run:

```bash
./organize.sh data/bookmarks.html skill_config.json --use-proxy --trust-env --direct-retry-after-proxy
```

Important behavior:

- `--use-proxy` enables proxy support
- `--trust-env` lets `aiohttp` read proxy variables from the shell
- `--direct-retry-after-proxy` performs a second direct pass only for rows still needing review
- successful rows from the first pass are reused
- final fetch stats preserve proxy-mode summary and direct-retry summary

## Force Full Refetch

Use this only when prior metadata is broadly untrusted or when page content should be refreshed completely.

```bash
./organize.sh data/bookmarks.html skill_config.json --force-refetch
```

With proxy:

```bash
./organize.sh data/bookmarks.html skill_config.json --force-refetch --use-proxy --trust-env --direct-retry-after-proxy
```

## Clear Fetch Cache

Use this when only fetched metadata should be discarded.

```bash
./organize.sh data/bookmarks.html skill_config.json --clear-fetch-cache
```

This deletes the configured fetch cache, then runs the full pipeline.

## Reset All Generated State

Use this after large config or logic changes when stale intermediate files should not survive.

```bash
./organize.sh data/bookmarks.html skill_config.json --reset-all
```

This removes configured generated files and report outputs while protecting the source bookmark file passed through `--source`.

## Step-by-step Commands

```bash
python3 scripts/1_copy_bookmark.py --config skill_config.json --source data/bookmarks.html
python3 scripts/2_parse_bookmarks.py --config skill_config.json
python3 scripts/3_fetch_webpage_info.py --config skill_config.json
python3 scripts/4_classify_bookmarks.py --config skill_config.json
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json
```

## Taxonomy Bootstrap

The default repository has no checked-in category rule files. A first run can
cluster with open-topic evidence, then produce an external-LLM prompt:

```bash
./organize.sh data/bookmarks.html skill_config.json --bootstrap-taxonomy
```

Give `output/reports/taxonomy_bootstrap_prompt.md` to the external model. Keep
`output/reports/taxonomy_bootstrap_clusters.json` locally as the structured
evidence backing that prompt.

After receiving strict JSON, save it as `data/generated/taxonomy_response.json`
and apply it:

```bash
python3 scripts/apply_taxonomy_response.py --config skill_config.json --response data/generated/taxonomy_response.json
python3 scripts/4_classify_bookmarks.py --config skill_config.json
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json
```

This writes ignored generated state:

```text
data/generated/user_taxonomy.json
data/generated/bookmark_taxonomy_assignments.json
```

External `title_patterns` are treated as literal phrases by default. Use object
form such as `{"regex": "..."}` only when a real regular expression is intended.

Proxy-first fetch with built-in direct retry:

```bash
python3 scripts/3_fetch_webpage_info.py --config skill_config.json --use-proxy --trust-env --direct-retry-after-proxy
```

Common partial reruns:

```bash
# Rebuild downstream artifacts from existing fetch output.
python3 scripts/4_classify_bookmarks.py --config skill_config.json
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json

# Rebuild clusters and HTML after clustering logic changes.
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json

# Rebuild only HTML after display/template changes.
python3 scripts/6_generate_html.py --config skill_config.json
```

## Latest Validated Baseline

Real-input validation on `2026-04-28` is recorded in `TODO_RUNTIME_FOLLOWUP.md`.

Headline metrics from that run:

- input bookmarks: `1014`
- duplicate URL groups: `92`
- unique domains: `439`
- fetch success: `745`
- fetch review queue: `269`
- generated taxonomy categories: `41`
- generated cluster assignments: `371`
- classify `待确认`: `470`
- classify `待整理`: `407`
- cluster count: `623`
- discovery clusters: `1`
- mixed clusters: `5`
- generic-platform clusters: `235`
- largest generic-platform cluster size: `11`

Guardrail metrics from that run:

- `folder_only_classification_count = 0`
- `low_confidence_normal_category_count = 0`
- `generic_platform_domain_suggestion_count = 0`
- `fetch_blocked_discovery_cluster_count = 0`

Use that file as the continuation baseline when doing behavior-sensitive work.

## Report Semantics

### `duplicates.json`

Duplicate normalized bookmark URLs from parsing.

Use it to identify source bookmark duplication, not fetch or classification issues.

### `broken_links.json`

Only HTTP-broken rows that stayed in `fetch_status == "broken"` and were not suppressed by trusted-access policy.

Typical contents:

- `404`
- `403`
- `521`
- similar HTTP error statuses

This file is intentionally narrower than `review_queue.json`.

### `review_queue.json`

All rows whose final `link_health.review_required == true`.

This includes:

- HTTP errors
- DNS/connection failures
- timeouts
- certificate failures
- invalid URLs

Trusted-access policy is disabled by default. If a local config enables it, keep
site rules narrow and document why review suppression is acceptable.

### `needs_confirmation.json`

Classification results that did not meet the assignment bar.

Important fields to inspect:

- confirmation bucket
- confirmation reasons
- top decision drivers
- open topic candidates

This is the main report for deciding whether uncertainty came from:

- fetch blockage
- generated taxonomy coverage gaps
- low-confidence matches

### `rule_suggestions.json`

Structured taxonomy and clustering improvement suggestions.

Current suggestion types include:

- `add_alias`
- `add_specific_domain`
- `create_topic`
- `split_mixed_cluster`
- `investigate_fetch_failures`

Do not apply suggestions blindly. Check representative bookmarks first.

### `quality_report.json`

Main quality summary. Current sections include:

- `metrics`
- `largest_discovery_clusters`
- `largest_tidy_clusters`
- `largest_mixed_clusters`
- `largest_generic_platform_clusters`
- `largest_fetch_blocked_clusters`
- `largest_flat_normal_roots`
- `review_hotspots`

Key metrics to watch:

- `folder_only_classification_count`
- `low_confidence_normal_category_count`
- `generic_platform_domain_suggestion_count`
- `fetch_blocked_discovery_cluster_count`
- `mixed_cluster_count`
- `normal_root_direct_bookmark_share`
- `flat_normal_root_count`

### `signal_audit.json`

Signal collection and consumption audit.

Current sections include:

- `summary`
- `families`
- `fields`
- `unused_high_value_signals`
- `hotspots`

Use this before adding new fetch heuristics. Prefer consuming existing collected signals first.

## Review Workflow After A Real Run

Inspect in this order:

1. `output/reports/review_queue.json`
2. `output/reports/needs_confirmation.json`
3. `output/reports/quality_report.json`
4. `output/reports/rule_suggestions.json`
5. `output/reports/signal_audit.json`
6. `output/organized_bookmarks.html`

Interpretation:

- `待审阅`: link health problem or suspicious fetch state
- `待整理`: not enough reliable evidence for normal-category assignment
- `发现主题`: cluster has meaningful but unsupported topic concentration
- `rule_suggestions.json`: next likely rule or clustering work
- `signal_audit.json`: missing use of already-collected evidence

## Expected Count Difference

If `review_hierarchy` is present, reviewed bookmarks are mirrored into `待审阅`.

So:

```text
generated HTML bookmark count = main hierarchy count + review mirror count
```

This is expected. Validate uniqueness in the main hierarchy, not by raw HTML count alone.

## Troubleshooting

### Dependency Import Fails

Run:

```bash
python3 -m pip install -r requirements.txt
```

### Fetch Produces Too Many Failures

Use the preferred proxy command:

```bash
./organize.sh data/bookmarks.html skill_config.json --use-proxy --trust-env --direct-retry-after-proxy
```

If the fetch cache is stale:

```bash
./organize.sh data/bookmarks.html skill_config.json --clear-fetch-cache --use-proxy --trust-env --direct-retry-after-proxy
```

If the failures are concentrated on a few domains, inspect:

- `output/reports/review_queue.json`
- `output/reports/quality_report.json`
- `TODO_RUNTIME_FOLLOWUP.md`

### Too Many Items In `待整理`

Inspect:

```text
output/reports/needs_confirmation.json
output/reports/rule_suggestions.json
output/reports/quality_report.json
output/reports/signal_audit.json
```

Then decide whether the main issue is:

- fetch blockage
- generated taxonomy gaps
- generic-platform naming noise
- mixed clusters

### Generic Platform Cluster Is Too Large

Inspect `largest_generic_platform_clusters` in `quality_report.json`.

Prefer these fixes:

- remove source/platform tokens from cluster labels and hints
- add precise non-platform topic aliases through generated taxonomy
- add topic-specific project/product domains through generated taxonomy
- split mixed clusters earlier

Avoid adding `github.com`, `csdn.net`, `zhihu.com`, `docs.qq.com`, or similar broad domains to topic categories.

## Validation Before Commit

```bash
python3 -m py_compile scripts/common.py scripts/1_copy_bookmark.py scripts/2_parse_bookmarks.py scripts/3_fetch_webpage_info.py scripts/4_classify_bookmarks.py scripts/5_cluster_bookmarks.py scripts/6_generate_html.py scripts/generate_taxonomy_bootstrap.py scripts/apply_taxonomy_response.py scripts/reset_pipeline_state.py
python3 -c "import json; json.load(open('skill_config.json'))"
pytest -q
git diff --check
```

---
name: bookmark-organizer-runbook
description: Follow this runbook to operate the bookmark pipeline, recover from fetch problems, inspect reports, and decide when to rerun individual stages.
---

# Runbook

## Operating Contract

Use this document for day-to-day execution. For design intent and extension rules, read `README.md` and `AGENTS.md`.

The normal source file is:

```text
data/bookmarks.html
```

The normal config file is:

```text
skill_config.json
```

The normal final output is:

```text
output/organized_bookmarks.html
```

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

If HTTPS fails, fix the Python environment or use a known working environment before running the fetch step.

## Decision Table

| Situation | Command |
| --- | --- |
| First normal run | `./organize.sh data/bookmarks.html skill_config.json` |
| Network needs proxy | `./organize.sh data/bookmarks.html skill_config.json --use-proxy --trust-env` |
| Forgot proxy and want to retry failures | same proxy command; successful fetch cache is reused |
| Want to refresh all pages | `./organize.sh data/bookmarks.html skill_config.json --force-refetch` |
| Want to discard only fetch cache | `./organize.sh data/bookmarks.html skill_config.json --clear-fetch-cache` |
| Want to discard all generated state | `./organize.sh data/bookmarks.html skill_config.json --reset-all` |
| Changed only classification rules | rerun steps 4, 5, 6 |
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
logs/bookmarks_organizer.log
```

## Proxy Run

Set proxy variables in the shell:

```bash
export https_proxy=http://127.0.0.1:7897
export http_proxy=http://127.0.0.1:7897
export all_proxy=socks5://127.0.0.1:7897
```

Run:

```bash
./organize.sh data/bookmarks.html skill_config.json --use-proxy --trust-env
```

Important behavior:

- `--use-proxy` enables proxy support.
- `--trust-env` lets `aiohttp` read proxy variables from the shell.
- Already successful fetches in `data/bookmarks_with_info.json` are reused by default.
- Failed, skipped, timeout, and broken fetches are retried.

## Force Full Refetch

Use this only when prior metadata is broadly untrusted or when page content should be refreshed completely.

```bash
./organize.sh data/bookmarks.html skill_config.json --force-refetch
```

With proxy:

```bash
./organize.sh data/bookmarks.html skill_config.json --force-refetch --use-proxy --trust-env
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

Common partial reruns:

```bash
# Refetch failures, then rebuild downstream artifacts.
python3 scripts/3_fetch_webpage_info.py --config skill_config.json --use-proxy --trust-env
python3 scripts/4_classify_bookmarks.py --config skill_config.json
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json

# Reapply changed classification rules without fetching.
python3 scripts/4_classify_bookmarks.py --config skill_config.json
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json

# Rebuild clusters and HTML after clustering logic changes.
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json
```

## Reports

### `duplicates.json`

Shows duplicate normalized bookmark URLs from parsing. Use it to identify source bookmark duplication.

### `broken_links.json`

Shows invalid URLs, non-HTTP URLs, HTTP errors, timeouts, and fetch errors.

### `review_queue.json`

Shows items that need human review because link health is questionable.

Trusted-access policy can suppress review noise for high-friction sites such as `zhihu.com`, `csdn.net`, `github.com`, `gitbook.com`, and `gitbook.io`.

### `needs_confirmation.json`

Shows classification results that did not meet the confidence threshold.

These are candidates for `待整理`, `发现主题`, or future rule updates.

### `rule_suggestions.json`

Shows rule improvement candidates.

Use this report to decide whether to:

- create a new topic;
- add a specific domain;
- add a keyword or alias;
- split a mixed cluster;
- demote noisy evidence.

Do not blindly apply suggestions. Check representative bookmarks first.

### `quality_report.json`

Main quality metrics:

- `folder_only_classification_count`: should be `0`.
- `low_confidence_normal_category_count`: should be `0`.
- `generic_platform_domain_suggestion_count`: should be `0`.
- `discovery_cluster_count`: large values suggest missing rules or emerging themes.
- `tidy_cluster_count`: large values suggest insufficient evidence or intentionally conservative routing.
- `mixed_cluster_count`: inspect for cluster splitting needs.
- `largest_generic_platform_cluster_size`: watch for platform-source over-merge.

## Review Workflow After Importing HTML

After importing `output/organized_bookmarks.html` into Chrome, inspect in this order:

1. `待审阅`
2. `待整理`
3. `发现主题`
4. High-value roots under `技术主题`
5. `rule_suggestions.json`
6. `quality_report.json`

Interpretation:

- `待审阅`: link health problem or suspicious fetch state.
- `待整理`: not enough reliable evidence for normal category assignment.
- `发现主题`: cluster has meaningful discovered labels but no stable destination root.
- Normal roots: assigned only when evidence and support are sufficient.

## Expected Count Difference

If `review_hierarchy` is present, reviewed bookmarks are mirrored into `待审阅`.

So:

```text
generated HTML bookmark count = normal hierarchy count + review mirror count
```

This is expected. Validate uniqueness in the main hierarchy, not by raw HTML count alone.

## Troubleshooting

### Dependency Import Fails

Run:

```bash
python3 -m pip install -r requirements.txt
```

### Fetch Produces Too Many Failures

Use proxy:

```bash
./organize.sh data/bookmarks.html skill_config.json --use-proxy --trust-env
```

If prior failed metadata is stale:

```bash
./organize.sh data/bookmarks.html skill_config.json --clear-fetch-cache --use-proxy --trust-env
```

### Too Many Items In `待整理`

Inspect:

```text
output/reports/needs_confirmation.json
output/reports/rule_suggestions.json
output/reports/quality_report.json
```

Then add precise rules to `data/category_rules_overrides.json`.

### Generic Platform Cluster Is Too Large

Inspect `largest_generic_platform_clusters` in `quality_report.json`.

Prefer these fixes:

- remove platform tokens from cluster hints;
- add specific non-platform topic keywords;
- add topic-specific project/product domains;
- split mixed clusters.

Avoid adding `github.com`, `csdn.net`, `zhihu.com`, `medium.com`, or similar broad domains to topic categories.

## Validation Before Commit

```bash
python3 -m py_compile scripts/common.py scripts/1_copy_bookmark.py scripts/2_parse_bookmarks.py scripts/3_fetch_webpage_info.py scripts/4_classify_bookmarks.py scripts/5_cluster_bookmarks.py scripts/6_generate_html.py scripts/reset_pipeline_state.py
python3 -c "import json; [json.load(open(path)) for path in ['data/category_rules.json','data/category_rules_overrides.json','skill_config.json']]"
pytest -q
git diff --check
```

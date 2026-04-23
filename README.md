---
name: chrome-bookmark-organizer
description: Use this project to turn exported Chrome bookmarks into classified, clustered, reviewable, Chrome-importable HTML. Read this first when operating, debugging, or extending the pipeline.
---

# Chrome Bookmark Organizer

## Purpose

This repository is a local, configuration-driven pipeline for reorganizing exported Chrome bookmarks.

It is optimized for practical output quality rather than perfect taxonomy. The pipeline keeps uncertain items visible, avoids over-trusting stale Chrome folders, separates generic publishing platforms from real topics, and emits reports that help future rule improvements.

## Use This When

- You have a Chrome-exported `bookmarks.html` file and want a cleaner importable HTML file.
- You want technical bookmarks grouped by topic, resource type, and discovered themes.
- You need failed or suspicious links preserved but mirrored into a review queue.
- You want reports that identify weak rules, mixed clusters, and topics worth adding.
- You are an agent improving this project and need the current mental model, entrypoints, and quality gates.

## Do Not Assume

- Do not assume the original Chrome folder path is correct. It is context only, not strong topic evidence.
- Do not add broad platform domains such as `github.com`, `csdn.net`, `zhihu.com`, or `medium.com` to topic rules. These are generic platforms.
- Do not delete broken links automatically. The pipeline preserves them and mirrors them into `待审阅`.
- Do not treat a higher count in generated HTML as duplication by itself. Review links are intentionally mirrored.
- Do not edit generated files in `data/*.json`, `output/`, or `logs/` as source of truth.

## Primary Command

```bash
./organize.sh data/bookmarks.html skill_config.json
```

If the network needs a proxy:

```bash
export https_proxy=http://127.0.0.1:7897
export http_proxy=http://127.0.0.1:7897
export all_proxy=socks5://127.0.0.1:7897

./organize.sh data/bookmarks.html skill_config.json --use-proxy --trust-env
```

The final importable file is:

```text
output/organized_bookmarks.html
```

## Pipeline Contract

The pipeline is linear:

```text
Chrome export HTML
  -> scripts/1_copy_bookmark.py
  -> scripts/2_parse_bookmarks.py
  -> scripts/3_fetch_webpage_info.py
  -> scripts/4_classify_bookmarks.py
  -> scripts/5_cluster_bookmarks.py
  -> scripts/6_generate_html.py
  -> Chrome-importable HTML
```

Each step reads configuration from `skill_config.json` unless overridden with CLI flags.

## Step Map

| Step | Script | Main input | Main output | Role |
| --- | --- | --- | --- | --- |
| 1 | `scripts/1_copy_bookmark.py` | source HTML | `data/bookmarks.html` | Copy source into project path |
| 2 | `scripts/2_parse_bookmarks.py` | copied HTML | `data/parsed_bookmarks.json` | Parse Chrome bookmark HTML and duplicates |
| 3 | `scripts/3_fetch_webpage_info.py` | parsed JSON | `data/bookmarks_with_info.json` | Fetch metadata, page signals, review health |
| 4 | `scripts/4_classify_bookmarks.py` | enriched JSON | `data/classified_bookmarks.json` | Build `signal_pack`, score topic/rules/facets |
| 5 | `scripts/5_cluster_bookmarks.py` | classified JSON | `data/clustering_result.json` | Soft cluster, build hierarchy, quality reports |
| 6 | `scripts/6_generate_html.py` | clustering JSON | `output/organized_bookmarks.html` | Emit Chrome import HTML |

## Data Model

The current design centers on `signal_pack`, built in `scripts/common.py`.

Important signals:

- `preferred_title`: saved bookmark title first, then page metadata.
- `title_candidates`: saved title, OG/Twitter title, `h1`, HTML title.
- `preferred_description`: user description/notes, OG/Twitter description, meta description, main text.
- `semantic_text`: consolidated text for classification and clustering.
- `resource_facets`: structured hints such as `文档`, `博客`, `论文`, `仓库`, `工具`.
- `source_facets`: site name, registrable domain, brand terms.
- `quality_facets`: fetch success, review requirement, trusted-access state.
- `canonical_identity`: canonical URL or normalized fetch URL.
- `time_bucket`: year/month/week derived from Chrome `ADD_DATE`.

## Classification Intent

The classifier should prefer reliable evidence:

- Strong: exact/suffix domain rules, title patterns, meaningful keyword/content hits.
- Medium: metadata, OpenGraph/Twitter fields, schema/page type, main text.
- Weak: dynamic topic candidates and generic discovered tokens.
- Disabled for topic scoring: stale Chrome folder names.

Low-confidence items should go to `待整理`, not a normal topic. Useful emerging themes should surface under `发现主题`.

## Clustering Intent

The clusterer builds soft topic groups without letting source platforms dominate.

Key constraints:

- Generic platforms are source signals, not topic roots.
- `GitHub`, `CSDN`, `Zhihu`, `StackOverflow`, `Medium`, `docs.qq.com`, and similar domains must not merge unrelated content by domain alone.
- `rule_roots` can guide destination only when enough normal-category support and confidence exist.
- Mixed or weak clusters should land in `发现主题` or `待整理`, where reports can guide future rules.

## Outputs

Main output:

- `output/organized_bookmarks.html`

Reports:

- `output/reports/duplicates.json`
- `output/reports/broken_links.json`
- `output/reports/needs_confirmation.json`
- `output/reports/review_queue.json`
- `output/reports/rule_suggestions.json`
- `output/reports/quality_report.json`

Logs:

- `logs/bookmarks_organizer.log`

## Output Semantics

The final HTML is directly importable into Chrome.

Broken or suspicious links are not removed. They remain in the normal hierarchy and are also mirrored into top-level `待审阅`. Therefore the generated HTML bookmark count may be greater than the original input count.

Default top-level display groups:

```text
技术主题
工具与平台
学习与资料
个人与生活
待整理
发现主题
待审阅
```

## Configuration

Use `skill_config.json` as the operational config.

Core sections:

- `input`: source bookmark path and rule files.
- `pipeline`: intermediate file paths.
- `output`: final HTML and report paths.
- `fetch_options`: concurrency, timeout, retry, cache, proxy, trusted-access policy.
- `classification_options`: scoring weights and confidence thresholds.
- `clustering_options`: cluster thresholds, generic platforms, display groups.
- `logging`: log level and log file.

When using an alternate config file, relative paths are resolved relative to that config file's directory.

## Rule Files

- `data/category_rules.json`: default shared rules.
- `data/category_rules_overrides.json`: personal or local extensions.

Rule improvement guidance:

- Add specific product/project domains only when the domain is topic-specific.
- Add aliases/keywords when a stable theme appears in `rule_suggestions.json`.
- Add personal overrides in `data/category_rules_overrides.json` instead of rewriting broad defaults when possible.
- Keep generic platform domains in `generic_platform_domains`, not in topic rules.

## Run Modes

Normal run:

```bash
./organize.sh data/bookmarks.html skill_config.json
```

Retry failed fetches with proxy:

```bash
./organize.sh data/bookmarks.html skill_config.json --use-proxy --trust-env
```

Force full refetch:

```bash
./organize.sh data/bookmarks.html skill_config.json --force-refetch
```

Clear only fetch cache:

```bash
./organize.sh data/bookmarks.html skill_config.json --clear-fetch-cache
```

Remove all generated pipeline state and rebuild:

```bash
./organize.sh data/bookmarks.html skill_config.json --reset-all
```

## Quality Gates

Before committing code or rule changes:

```bash
python3 -m py_compile scripts/common.py scripts/1_copy_bookmark.py scripts/2_parse_bookmarks.py scripts/3_fetch_webpage_info.py scripts/4_classify_bookmarks.py scripts/5_cluster_bookmarks.py scripts/6_generate_html.py scripts/reset_pipeline_state.py
python3 -c "import json; [json.load(open(path)) for path in ['data/category_rules.json','data/category_rules_overrides.json','skill_config.json']]"
pytest -q
git diff --check
```

After a real run, inspect `output/reports/quality_report.json`.

Important metrics:

- `folder_only_classification_count` should stay `0`.
- `low_confidence_normal_category_count` should stay `0`.
- `generic_platform_domain_suggestion_count` should stay `0`.
- `largest_generic_platform_cluster_size` should not grow unexpectedly.

## Document Map

- `README.md`: canonical project skill and design contract.
- `AGENTS.md`: operational guide for coding agents.
- `RUNBOOK.md`: concrete daily operating procedures.
- `QUICK_REFERENCE.md`: command card for frequent actions.

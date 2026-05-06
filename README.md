---
name: chrome-bookmark-organizer
description: Use this project to turn exported Chrome bookmarks into classified, clustered, reviewable, Chrome-importable HTML. Read this first when operating, debugging, or extending the pipeline.
---

# Chrome Bookmark Organizer

## Purpose

This repository is a local, configuration-driven pipeline for reorganizing exported Chrome bookmarks.

It is optimized for practical output quality rather than perfect taxonomy. The pipeline keeps uncertainty visible, avoids over-trusting stale Chrome folders, treats generic publishing platforms as source signals rather than topics, and emits reports that drive the next round of rule or clustering improvements.

Persistent design constraints are tracked in `DESIGN_CONSTRAINTS.md`. Treat that
file as part of the product contract before changing classification,
clustering, fetch behavior, LLM-assisted workflows, or display hierarchy.

## Current State

The codebase is already on the information-flow version of the pipeline:

- `signal_pack/v2` is the shared contract between fetch, classify, and cluster.
- stage outputs carry explicit `schema_version` and downstream stages reject stale inputs.
- classification and clustering both emit decision evidence.
- `signal_audit.json` tracks which collected signals are actually consumed.

Latest real-input validation is documented in `TODO_RUNTIME_FOLLOWUP.md`. As of `2026-04-28`, the pipeline has been validated on a real `1014`-bookmark export with generated user taxonomy and is operational on real data.

## Use This When

- You have a Chrome-exported `bookmarks.html` file and want a cleaner importable HTML file.
- You want bookmarks grouped by generated topics, resource type, and discovered themes.
- You want broken or suspicious links preserved but mirrored into `待审阅`.
- You want reports that identify fetch hotspots, rule gaps, mixed clusters, and unused signals.
- You are an agent improving this project and need the current design contract before making changes.

## Do Not Assume

- Original Chrome folder path is context only, not strong topic evidence.
- Generic platform domains such as `github.com`, `csdn.net`, `zhihu.com`, `jianshu.com`, `docs.qq.com`, or `medium.com` are not topic domains.
- More bookmarks in generated HTML does not imply duplication. `待审阅` is an intentional mirror.
- Generated files in `data/*.json`, `output/`, and `logs/` are not source of truth.
- Tracked defaults should not become a container for one user's personal topic rules.
- LLM-assisted improvements should use prompt and file workflows, not built-in live model API calls.
- Better clustering metrics alone do not justify a bookmark-bar hierarchy that becomes harder to browse.

## Persistent Design Constraints

Read `DESIGN_CONSTRAINTS.md` when changing product behavior. The short version:

- keep tracked defaults generic and reusable across different users
- keep user-specific topic knowledge in generated taxonomy and assignment files
- allow LLM-assisted refinement through exported prompts and imported JSON, not direct provider SDK coupling
- preserve explicit proxy and direct fetch workflows, and surface proxy setup clearly when real runs need it
- extract more structure from the current user's corpus instead of solving gaps with one-off hard-coded topics
- optimize the final visible hierarchy for browsing and retrieval, not just purity metrics

## Primary Command

```bash
./organize.sh data/bookmarks.html skill_config.json
```

Preferred real-network run when a proxy is required:

```bash
export https_proxy=http://127.0.0.1:7897
export http_proxy=http://127.0.0.1:7897
export all_proxy=socks5://127.0.0.1:7897

./organize.sh data/bookmarks.html skill_config.json --use-proxy --trust-env --direct-retry-after-proxy
```

Final importable output:

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
| 2 | `scripts/2_parse_bookmarks.py` | copied HTML | `data/parsed_bookmarks.json` | Parse Chrome bookmark HTML and detect duplicate URLs |
| 3 | `scripts/3_fetch_webpage_info.py` | parsed JSON | `data/bookmarks_with_info.json` | Fetch page/site metadata, link health, fetch provenance |
| 4 | `scripts/4_classify_bookmarks.py` | enriched JSON | `data/classified_bookmarks.json` | Build `signal_pack/v2`, score categories, infer resource type, emit confirmation evidence |
| 5 | `scripts/5_cluster_bookmarks.py` | classified JSON | `data/clustering_result.json` | Build feature sets, cluster hierarchy, quality and rule reports |
| 6 | `scripts/6_generate_html.py` | clustering JSON | `output/organized_bookmarks.html` | Emit Chrome import HTML |

## Stage Schemas

Current stage contracts:

- fetch output: `fetch_output/v2`
- classified output: `classified_output/v2`
- clustering output: `clustering_output/v2`
- signal audit report: `signal_audit/v1`
- shared signal contract: `signal_pack/v2`

If step 4, 5, or 6 sees an older stage payload, it fails fast instead of silently consuming stale JSON.

## Shared Signal Model

The current design centers on `signal_pack/v2`, built in `scripts/common.py`.

Grouped signal families:

- `identity`
- `content`
- `structure`
- `health_access`
- `context_time`

Important shared fields:

- `identity.canonical_identity`
- `identity.domain`
- `identity.registrable_domain`
- `identity.path_segments`
- `content.preferred_title`
- `content.preferred_description`
- `content.semantic_text`
- `content.keywords_text`
- `content.language`
- `structure.resource_facets`
- `structure.site_name`
- `structure.brand_terms`
- `structure.page_type_hints`
- `structure.schema_types`
- `health_access.fetch_status`
- `health_access.link_health`
- `health_access.fetch_context`
- `health_access.quality_facets`
- `context_time.time_bucket`

This contract is the preferred place to add reusable signals. Do not duplicate fetch-derived extraction logic across classifier and clusterer if it can live in `signal_pack`.

## Classification Intent

The classifier should prefer reliable evidence:

- strong: exact/suffix domain rules, title patterns, meaningful keyword/content hits
- medium: page metadata, schema/page-type hints, main text, site name and brand terms
- weak: dynamic topic candidates and discovered tokens
- disabled for topic scoring: stale Chrome folder names

Important invariants:

- low-confidence normal assignments must be downgraded to `待整理`
- folder-only normal assignments should remain `0`
- fetch-caused uncertainty and rule-caused uncertainty should remain distinguishable

## Clustering Intent

The clusterer builds soft topic groups without letting source platforms dominate.

Important constraints:

- generic platforms are source signals, not topic roots
- source platforms should not over-merge unrelated technical topics
- `发现主题` should contain plausible emerging topics, not obvious fetch-failure or platform-noise clusters
- `待整理` should remain a conservative bucket for weak or unsupported clusters
- `待审阅` is a review mirror, not duplicate output

## Outputs

Main output:

- `output/organized_bookmarks.html`

Reports:

- `output/reports/duplicates.json`
- `output/reports/broken_links.json`
- `output/reports/fetch_hotspots.json`
- `output/reports/needs_confirmation.json`
- `output/reports/review_queue.json`
- `output/reports/rule_suggestions.json`
- `output/reports/quality_report.json`
- `output/reports/signal_audit.json`

Logs:

- `logs/bookmarks_organizer.log`

## Output Semantics

Generated HTML is directly importable into Chrome.

Broken or suspicious links are not deleted. They remain in the normal hierarchy and are also mirrored into top-level `待审阅` when `review_hierarchy` is non-empty. Therefore:

```text
generated HTML bookmark count >= original input bookmark count
```

Default top-level groups are generated from the current run. In `auto` display
mode, normal roots are shown directly at the top level when there are not too
many of them; only larger root sets are wrapped under `主要主题`. Unresolved and
review areas stay separate, and very small discovery roots can be grouped under
`待整理` for display without changing the raw clustering result:

```text
数据库
编程语言
待整理
发现主题  # or grouped under 待整理 when extremely small
待审阅  # only when review items exist
```

## Configuration

Use `skill_config.json` as the operational config.

Core sections:

- `input`: source bookmark path plus optional generated taxonomy/assignment files
- `pipeline`: intermediate file paths
- `output`: final HTML and report paths
- `fetch_options`: concurrency, timeout, retry, cache, proxy, trusted-access policy, optional `domain_overrides`
- `classification_options`: scoring weights and confidence thresholds
- `clustering_options`: cluster thresholds, generic platforms, display groups
- `logging`: log level and log file

When using an alternate config file, relative paths are resolved relative to that config file's directory.

## Taxonomy Bootstrap

The default pipeline no longer depends on checked-in category rule files. First runs
use an empty topic taxonomy and produce open-topic candidates from `signal_pack/v2`.

Generate a prompt for an external LLM:

```bash
./organize.sh data/bookmarks.html skill_config.json --bootstrap-taxonomy
```

Then place the LLM's strict JSON response in `data/generated/taxonomy_response.json`
and apply it:

```bash
python3 scripts/apply_taxonomy_response.py --config skill_config.json --response data/generated/taxonomy_response.json
```

This writes `data/generated/user_taxonomy.json` and
`data/generated/bookmark_taxonomy_assignments.json`; normal runs consume those files
when they exist.

Display grouping is data-tolerant: when `clustering_options.root_groups` is empty or
`clustering_options.display.grouping_mode` is `auto`, the clusterer decides whether
normal roots should stay directly top-level or be wrapped for display. `待整理`
is also restructured by confirmation bucket plus a second semantic aggregation pass.

Taxonomy guidance:

- add topic-specific domains only when the domain itself is topic-specific
- prefer aliases, title patterns, and stable project/product tokens before broad domain rules
- keep broad source platforms out of topic domains
- validate taxonomy additions with `taxonomy_bootstrap_clusters.json`, `quality_report.json`, and tests

For a later large one-shot `待整理` long-tail follow-up without adding any in-repo API dependency:

```bash
python3 scripts/generate_taxonomy_followup.py --config skill_config.json
python3 scripts/apply_taxonomy_response.py --config skill_config.json --response data/generated/taxonomy_followup_response.json --clusters output/reports/taxonomy_followup_candidates.json --merge-existing
python3 scripts/4_classify_bookmarks.py --config skill_config.json
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json
```

This produces `taxonomy_followup_prompt.md` and `taxonomy_followup_candidates.json`
for your own external LLM or code agent. The follow-up candidates now include
existing tidy clusters, deterministic tidy semantic bundles, and leftover long-tail
aggregates. Save the strict JSON response locally, then merge it back with
`--merge-existing`.

## Run Modes

Normal run:

```bash
./organize.sh data/bookmarks.html skill_config.json
```

Preferred fetch strategy when proxy is needed:

```bash
./organize.sh data/bookmarks.html skill_config.json --use-proxy --trust-env --direct-retry-after-proxy
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
python3 -m py_compile scripts/common.py scripts/1_copy_bookmark.py scripts/2_parse_bookmarks.py scripts/3_fetch_webpage_info.py scripts/4_classify_bookmarks.py scripts/5_cluster_bookmarks.py scripts/6_generate_html.py scripts/generate_taxonomy_bootstrap.py scripts/generate_taxonomy_followup.py scripts/apply_taxonomy_response.py scripts/reset_pipeline_state.py
python3 -c "import json; json.load(open('skill_config.json'))"
pytest -q
git diff --check
```

After a real run, inspect:

- `output/reports/quality_report.json`
- `output/reports/rule_suggestions.json`
- `output/reports/signal_audit.json`
- `output/reports/review_queue.json`
- `output/reports/fetch_hotspots.json`

Important metrics:

- `folder_only_classification_count == 0`
- `low_confidence_normal_category_count == 0`
- `generic_platform_domain_suggestion_count == 0`
- `fetch_blocked_discovery_cluster_count == 0`
- `largest_generic_platform_cluster_size` should not jump unexpectedly

## Document Map

- `README.md`: design contract and current architecture
- `DESIGN_CONSTRAINTS.md`: persistent product and implementation constraints
- `RUNBOOK.md`: operating procedures and report interpretation
- `QUICK_REFERENCE.md`: concise command card
- `AGENTS.md`: agent implementation guidance
- `EVIDENCE_DRIVEN_ITERATION.md`: real-run workflow for isolated evaluation
- `TODO_RUNTIME_FOLLOWUP.md`: latest real-run baseline, remaining issues, and next-step priorities

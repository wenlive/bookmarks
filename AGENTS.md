---
name: bookmark-organizer-agent-guide
description: Agent-facing operating and implementation contract for the Chrome bookmark organization pipeline.
---

# Agent Guide

## Mission

Improve the actual usefulness of generated Chrome bookmarks without breaking the repository's generic, reusable behavior.

Primary success criteria:

- fewer unrelated bookmarks in the same normal category
- fewer false assignments caused by stale Chrome folders
- fewer source/platform labels leaking into topic roots
- more useful `待整理`、`发现主题`、`待审阅`
- stable, reproducible commands and report semantics

## Read First

Read in this order:

1. `README.md`
2. `SERVICE_CONTRACT.md`
3. `DESIGN_CONSTRAINTS.md`
4. `RUNBOOK.md`
5. `QUICK_REFERENCE.md`
6. `skill_config.json`
7. `scripts/common.py`
8. `scripts/3_fetch_webpage_info.py`
9. `scripts/4_classify_bookmarks.py`
10. `scripts/5_cluster_bookmarks.py`
11. `tests/test_pipeline.py`

## Architecture

The project is a six-step local pipeline:

```text
copy -> parse -> fetch -> classify -> cluster -> generate html
```

Stable shared contracts:

- `fetch_output/v2`
- `classified_output/v2`
- `clustering_output/v2`
- `signal_pack/v2`

`signal_pack/v2` in `scripts/common.py` is the preferred place to add reusable signals. Avoid duplicating fetch-derived extraction logic across classifier and clusterer when it can be normalized once there.

## Invariants

Keep these unless the user explicitly asks for a different product behavior:

- original Chrome folder path is context, not strong topic evidence
- low-confidence normal assignments should downgrade to `待整理`
- coherent unsupported clusters with enough independent support should route to `发现主题`; small or source-host-only groups should stay in `待整理`
- broken or suspicious links should be preserved
- fetch/enrichment failure is internal diagnostic state, not a user-facing folder concept
- only links with actionable validity problems should be mirrored to `待审阅`
- generic platforms should not become topic evidence by domain alone
- reports should explain why the output still needs review or rule work
- generated taxonomy and runtime state stay ignored; tracked source remains generic

## External-Service View

When acting on behalf of a user, treat `SERVICE_CONTRACT.md` as the runtime truth for:

- inputs and outputs
- file side effects
- reset semantics
- network/proxy behavior
- the two LLM-assisted workflow handoff points

Do not re-infer those semantics from memory when the contract already states them.

## LLM-Assisted Workflow Contract

Current design has exactly two LLM-assisted steps:

1. taxonomy bootstrap
2. taxonomy follow-up

Agent rules:

- do not stop after generating a prompt
- do read the generated prompt file and its paired structured evidence JSON
- do analyze the current run's actual clusters/candidates before producing a response
- do produce strict schema-valid response JSON
- do apply that response through `scripts/apply_taxonomy_response.py`
- do not embed direct vendor model API calls into the default local pipeline
- do not add a third model step merely because fetching failed; bootstrap/follow-up already receive identity and unresolved-cluster evidence

Required input pairs:

- bootstrap:
  - `output/reports/taxonomy_bootstrap_prompt.md`
  - `output/reports/taxonomy_bootstrap_clusters.json`
- follow-up:
  - `output/reports/taxonomy_followup_prompt.md`
  - `output/reports/taxonomy_followup_candidates.json`

## Network And State Rules

When real fetching matters:

- preserve direct fetch support
- preserve proxy fetch support
- preserve proxy-first plus direct-retry behavior unless the user asks otherwise
- surface proxy env guidance when network restrictions are likely
- keep fetch provenance and failure semantics visible
- treat fetched content as optional enrichment; title, URL identity, generated taxonomy, and cross-bookmark evidence must remain usable without it
- do not expose timeout, DNS, access-denied, or server-error buckets as Chrome folder names

Canonical proxy example:

```bash
export https_proxy=http://127.0.0.1:7897
export http_proxy=http://127.0.0.1:7897
export all_proxy=socks5://127.0.0.1:7897
```

State rules:

- normal runs consume existing `data/generated/user_taxonomy.json` and `data/generated/bookmark_taxonomy_assignments.json` when present
- `--reset-all` does not currently remove those generated taxonomy files
- if a user wants a pure baseline rerun, generated taxonomy state may need separate cleanup

## Generic Platform Policy

Treat these as source hosts, not topic category domains:

```text
github.com
github.io
gitlab.com
gitee.com
bitbucket.org
stackoverflow.com
stackexchange.com
medium.com
zhihu.com
csdn.net
51cto.com
jianshu.com
docs.qq.com
qq.com
tencent.com
google.com
notion.so
youtube.com
bilibili.com
feishu.cn
feishu.com
larksuite.com
```

Agent rules:

- do not add these domains as topic domains in tracked defaults
- do not use them as justification for broad category creation
- do use title, URL path, product name, schema type, repository name, and page content as topic evidence

## Layer-Specific Guidance

### Fetch

Primary file:

```text
scripts/3_fetch_webpage_info.py
```

Responsibilities:

- fetch page metadata and site/profile signals
- normalize link health and failure reason
- record fetch provenance in `fetch_context`
- support proxy-first fetch plus direct retry
- emit `broken_links.json` and `review_queue.json`
- distinguish internal `review_required` diagnostics from user-facing `user_action_required`

When changing fetch:

- validate with a real run when transport, retry, parser, or trusted-access behavior changes
- separate transport problems from site policy problems from taxonomy problems
- do not hide uncertainty by widening trusted-access defaults

### Classification

Primary file:

```text
scripts/4_classify_bookmarks.py
```

Responsibilities:

- load generic base logic plus generated taxonomy when present
- build or consume `signal_pack/v2`
- score categories
- infer resource type, intent labels, and quality signals
- extract dynamic open-topic candidates
- downgrade weak normal assignments to `待整理`
- emit `needs_confirmation.json`

When changing classification:

- prefer aliases, title patterns, and narrow project/product tokens
- prefer content and metadata over folder names
- keep fetch operational fields out of topic scoring
- verify `low_confidence_normal_category_count == 0`
- downgrade cross-root score ties and homepage aggregate-content-only matches unless an explicit generated assignment resolves them

### Clustering

Primary file:

```text
scripts/5_cluster_bookmarks.py
```

Responsibilities:

- build features from classification plus `signal_pack/v2`
- cluster with explainable similarity
- prevent generic-platform over-merge
- decide destination root
- build display hierarchy
- emit `rule_suggestions.json`, `quality_report.json`, and `signal_audit.json`

When changing clustering:

- keep similarity explainable
- penalize weak cross-topic merges
- stop source/platform labels from dominating cluster names
- add or update quality metrics when changing routing behavior

## Validation-First Iteration Loop

For behavior-sensitive work, establish a baseline before changing rules, then repeat:

1. run classify, cluster, and HTML generation against the same input and generated state
2. inspect `quality_report.json` before optimizing coverage
3. inspect representative bookmarks from low-purity, discovery, and tidy clusters
4. change one reusable mechanism or generated taxonomy response
5. rerun and compare report semantics and HTML browse structure

Required `quality_report/v2` checks:

- `guardrails.status == "pass"`
- `display_missing_bookmark_count == 0`
- `display_duplicate_bookmark_count == 0`
- `low_confidence_normal_category_count == 0`
- `generic_platform_domain_suggestion_count == 0`
- `fetch_blocked_discovery_cluster_count == 0`
- no source/platform labels in discovered topic roots
- no oversized display leaf created by the change

Track `content_unavailable_normal_category_share` to evaluate graceful degradation. Do not improve it by promoting ambiguous bookmarks; normal cluster purity and semantic samples take precedence.

## Safe And Risky Changes

Safe improvements:

- add a reusable structured signal to `signal_pack`
- improve report clarity
- add generated taxonomy aliases, title patterns, or narrow domains through ignored files
- add regression tests for concrete false positives or false negatives
- improve cluster routing from weak evidence to `待整理` or `发现主题`

Risky changes:

- raising confidence globally to make metrics look better
- adding broad domains as topic defaults
- reusing stale folder names as strong evidence
- optimizing for fewer `待整理` items at the cost of false normal classifications
- using fetch operational fields such as `homepage_source` as topic evidence
- hard-coding one user's current topic distribution into tracked source

## Validation

Before handing work back:

```bash
python3 -m py_compile scripts/common.py scripts/1_copy_bookmark.py scripts/2_parse_bookmarks.py scripts/3_fetch_webpage_info.py scripts/4_classify_bookmarks.py scripts/5_cluster_bookmarks.py scripts/6_generate_html.py scripts/generate_taxonomy_bootstrap.py scripts/generate_taxonomy_followup.py scripts/apply_taxonomy_response.py scripts/reset_pipeline_state.py
python3 -c "import json; json.load(open('skill_config.json'))"
pytest -q
git diff --check
```

For behavior-sensitive work:

- prefer an isolated runtime under `/tmp`
- inspect `review_queue.json`, `fetch_hotspots.json`, `needs_confirmation.json`, `rule_suggestions.json`, `quality_report.json`, `signal_audit.json`
- compare report semantics, not just visible HTML
- rerun deterministic downstream steps twice when clustering or display grouping changes

## Commit Readiness

A change is ready when:

- docs match actual code behavior
- tests and checks pass
- generated state is not mistakenly committed
- no tracked default logic has been bent toward one user's current dataset
- output quality does not regress on folder-only classification, low-confidence normal classification, generic-platform suggestions, or fetch-blocked discovery routing
- display conservation is exact: every parsed bookmark appears once in the main hierarchy; `待审阅` is an intentional actionable-link mirror only

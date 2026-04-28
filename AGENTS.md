---
name: bookmark-organizer-agent-guide
description: Agent-facing guide for understanding, operating, debugging, and safely improving this Chrome bookmark organization pipeline.
---

# Agent Guide

## Mission

Improve the actual usefulness of generated Chrome bookmarks.

Primary success criteria:

- fewer unrelated bookmarks in the same normal category
- fewer false assignments caused by stale Chrome folders
- fewer rules that hard-code broad platforms as topics
- more useful `待整理`, `发现主题`, `待审阅`, and rule suggestion outputs
- stable, reproducible commands and reports

## First Files To Read

Read in this order:

1. `README.md`
2. `RUNBOOK.md`
3. `QUICK_REFERENCE.md`
4. `TODO_RUNTIME_FOLLOWUP.md`
5. `EVIDENCE_DRIVEN_ITERATION.md`
6. `skill_config.json`
7. `scripts/common.py`
8. `scripts/3_fetch_webpage_info.py`
9. `scripts/4_classify_bookmarks.py`
10. `scripts/5_cluster_bookmarks.py`
11. `tests/test_pipeline.py`

## Current Architecture

The project is a six-step local pipeline:

```text
copy -> parse -> fetch -> classify -> cluster -> generate html
```

The important design shift is from folder-driven classification to information-flow-driven classification.

The shared `signal_pack/v2` in `scripts/common.py` is the stable handoff between fetch, classification, and clustering. Prefer adding reusable signals there instead of duplicating extraction logic across classifier and clusterer.

Stage outputs are versioned and downstream steps reject stale payloads:

- `fetch_output/v2`
- `classified_output/v2`
- `clustering_output/v2`

## Invariants

Keep these invariants unless the user explicitly asks for a different product behavior:

- original Chrome folder path is context, not strong topic evidence
- low-confidence normal-category assignments should be downgraded to `待整理`
- discovered but unsupported clusters should go to `发现主题`
- broken or suspicious links should be preserved
- review-required links should be mirrored to `待审阅`
- generic platforms should not become topic evidence by domain alone
- reports should explain why output needs review or rule improvement
- generated taxonomy and runtime state should stay ignored; source code and docs should be tracked

## Generic Platform Policy

Generic platform domains include source hosts such as:

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
google.com
notion.so
youtube.com
bilibili.com
```

Agent rule:

- do not add these domains as topic category domains
- do use them to suppress source-like labels
- do let title, description, repository name, product name, schema type, and page content provide topic evidence

## Fetch Layer

Primary file:

```text
scripts/3_fetch_webpage_info.py
```

Responsibilities:

- fetch page metadata and site/profile signals
- normalize link health
- record fetch provenance in `fetch_context`
- support proxy-first fetch plus automatic direct retry
- emit `broken_links.json` and `review_queue.json`

Current known facts:

- real-input validation on `2026-04-28` is already done
- Brotli decode failures and XML parser warnings were addressed
- current remaining fetch work is hotspot-domain cleanup, not global fetch failure

When improving fetch:

- keep proxy-first plus direct-retry behavior intact
- do not widen trusted-access policy casually
- separate network failures from taxonomy failures before proposing rule changes
- validate with a real run when changing transport, retry, parsing, or trusted-access logic

## Classification Layer

Primary file:

```text
scripts/4_classify_bookmarks.py
```

Responsibilities:

- load the generic base contract plus generated user taxonomy when present
- build or consume `signal_pack/v2`
- score rule categories
- infer resource type, intent labels, and quality signals
- extract dynamic open-topic candidates
- downgrade low-confidence items to `待整理`
- emit `needs_confirmation.json`

When improving classification:

- prefer precise generated taxonomy domains for topic-specific domains
- prefer title patterns and narrow aliases for stable product/project names
- prefer content and metadata signals over folder names
- keep operational fetch terms out of topic extraction
- add tests for false positive and false negative cases
- check `low_confidence_normal_category_count` remains `0`

## Clustering Layer

Primary file:

```text
scripts/5_cluster_bookmarks.py
```

Responsibilities:

- build feature sets from classification plus `signal_pack/v2`
- compute similarity
- prevent generic-platform over-merge
- decide destination root
- build display hierarchy
- emit `rule_suggestions.json`, `quality_report.json`, and `signal_audit.json`

When improving clustering:

- keep similarity explainable
- penalize cross-topic merges when evidence is weak
- keep source/platform labels from dominating cluster names
- add quality metrics when introducing new routing behavior
- add tests for mixed clusters and generic-platform clusters

Current known pain points:

- discovery naming noise
- generic-platform cluster naming quality
- mixed clusters that should split earlier
- flat normal roots and high direct-bookmark share in some roots

## Generated Taxonomy

The repository no longer tracks default category rule files. The checked-in base
classifier only provides generic resource, intent, quality, dynamic-topic, and
generic-platform guardrails.

Generated user-specific constraints live under ignored paths:

```text
data/generated/user_taxonomy.json
data/generated/bookmark_taxonomy_assignments.json
```

Preferred workflow:

1. run the pipeline or `./organize.sh data/bookmarks.html skill_config.json --bootstrap-taxonomy`
2. give `output/reports/taxonomy_bootstrap_prompt.md` to an external LLM
3. save the strict JSON response as `data/generated/taxonomy_response.json`
4. run `python3 scripts/apply_taxonomy_response.py --config skill_config.json --response data/generated/taxonomy_response.json`
5. rerun steps 4, 5, 6
6. inspect `rule_suggestions.json`, `quality_report.json`, and `signal_audit.json`
7. validate tests and quality metrics

## Command Policy

Normal run:

```bash
./organize.sh data/bookmarks.html skill_config.json
```

Preferred real-network run:

```bash
export https_proxy=http://127.0.0.1:7897
export http_proxy=http://127.0.0.1:7897
export all_proxy=socks5://127.0.0.1:7897

./organize.sh data/bookmarks.html skill_config.json --use-proxy --trust-env --direct-retry-after-proxy
```

Classification-only downstream rerun:

```bash
python3 scripts/4_classify_bookmarks.py --config skill_config.json
python3 scripts/5_cluster_bookmarks.py --config skill_config.json
python3 scripts/6_generate_html.py --config skill_config.json
```

## Validation Policy

Before handing work back:

```bash
python3 -m py_compile scripts/common.py scripts/1_copy_bookmark.py scripts/2_parse_bookmarks.py scripts/3_fetch_webpage_info.py scripts/4_classify_bookmarks.py scripts/5_cluster_bookmarks.py scripts/6_generate_html.py scripts/generate_taxonomy_bootstrap.py scripts/apply_taxonomy_response.py scripts/reset_pipeline_state.py
python3 -c "import json; json.load(open('skill_config.json'))"
pytest -q
git diff --check
```

For behavior-sensitive changes, prefer an isolated runtime workspace under `/tmp` so you do not overwrite tracked or user-facing generated files.

## Evidence-Driven Iteration

When an agent needs to validate this project on a real bookmark export and turn the result into concrete improvements, follow:

```text
EVIDENCE_DRIVEN_ITERATION.md
```

Use:

```text
TODO_RUNTIME_FOLLOWUP.md
```

as the latest real-run baseline and continuation point.

## Review Reports Before Finalizing

Inspect:

```text
output/reports/review_queue.json
output/reports/needs_confirmation.json
output/reports/rule_suggestions.json
output/reports/quality_report.json
output/reports/signal_audit.json
```

For temporary verification outputs, use the same files under `/tmp` and report the metrics.

## Common Safe Improvements

- add a new structured signal to `signal_pack`
- add a narrow topic alias, title pattern, assignment, or domain through generated taxonomy
- add a regression test for misclassification
- adjust generic-platform token filtering
- improve cluster destination routing from weak evidence to `待整理` or `发现主题`
- improve report fields that explain why a cluster needs attention

## Common Risky Changes

- raising confidence globally without real-output validation
- adding broad domains to a topic category
- reusing source folder names as strong evidence
- optimizing for fewer `待整理` items at the cost of false normal classifications
- treating `review_hierarchy` mirror count as duplicate output
- changing generated output shape without updating `scripts/6_generate_html.py` tests
- using fetch operational fields such as `homepage_source` as topic evidence

## Commit Readiness

A change is ready when:

- worktree only contains intended source/docs/test changes
- tests pass
- JSON config loads
- docs describe actual commands, output paths, and report semantics
- quality metrics do not regress in the direction of folder-only classification, low-confidence normal classification, generic-platform suggestions, or fetch-blocked discovery clusters

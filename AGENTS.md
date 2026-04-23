---
name: bookmark-organizer-agent-guide
description: Agent-facing guide for understanding, operating, debugging, and safely improving this Chrome bookmark organization pipeline.
---

# Agent Guide

## Mission

Improve the actual usefulness of generated Chrome bookmarks.

Primary success criteria:

- Fewer unrelated bookmarks in the same normal category.
- Fewer false assignments caused by stale Chrome folders.
- Fewer rules that hard-code broad platforms as topics.
- More useful `待整理`, `发现主题`, `待审阅`, and rule suggestion outputs.
- Stable, reproducible commands and reports.

## First Files To Read

Read in this order:

1. `README.md`
2. `RUNBOOK.md`
3. `QUICK_REFERENCE.md`
4. `skill_config.json`
5. `scripts/common.py`
6. `scripts/4_classify_bookmarks.py`
7. `scripts/5_cluster_bookmarks.py`
8. `tests/test_pipeline.py`

## Current Architecture

The project is a six-step local pipeline:

```text
copy -> parse -> fetch -> classify -> cluster -> generate html
```

The important design shift is from folder-driven classification to information-flow-driven classification.

The shared `signal_pack` in `scripts/common.py` is the stable handoff between fetch, classification, and clustering. Prefer adding reusable signals there instead of duplicating extraction logic across classifier and clusterer.

## Invariants

Keep these invariants unless the user explicitly asks for a different product behavior:

- Original Chrome folder path is context, not strong topic evidence.
- Low-confidence normal-category assignments should be downgraded to `待整理`.
- Discovered but unsupported clusters should go to `发现主题`.
- Broken or suspicious links should be preserved.
- Review-required links should be mirrored to `待审阅`.
- Generic platforms should not become topic evidence by domain alone.
- Reports should explain why output needs review or rule improvement.
- Generated state should stay ignored; source rules and docs should be tracked.

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
docs.qq.com
google.com
notion.so
youtube.com
bilibili.com
```

Agent rule:

- Do not add these domains as topic category domains.
- Do use them to suppress source-like labels.
- Do let title, description, repository name, project name, schema type, and page content provide topic evidence.

## Classification Layer

Primary file:

```text
scripts/4_classify_bookmarks.py
```

Responsibilities:

- Load and merge default rules plus overrides.
- Build or consume `signal_pack`.
- Score rule categories.
- Infer resource type, intent labels, quality signals.
- Extract dynamic open-topic candidates.
- Downgrade low-confidence items to `待整理`.
- Emit `needs_confirmation.json`.

When improving classification:

- Prefer precise domain rules for topic-specific domains.
- Prefer title patterns for stable product/project names.
- Prefer content and metadata signals over folder names.
- Add tests for false positive and false negative cases.
- Check `low_confidence_normal_category_count` remains `0`.

## Clustering Layer

Primary file:

```text
scripts/5_cluster_bookmarks.py
```

Responsibilities:

- Build feature sets from classification plus `signal_pack`.
- Compute similarity.
- Prevent generic platform over-merge.
- Decide destination root.
- Build display hierarchy.
- Emit `rule_suggestions.json` and `quality_report.json`.

When improving clustering:

- Keep similarity explainable.
- Penalize cross-topic merges when evidence is weak.
- Keep source/platform labels from dominating cluster names.
- Add quality metrics when introducing new routing behavior.
- Add tests for mixed clusters and generic platform clusters.

## Rule Files

Default rules:

```text
data/category_rules.json
```

Local extensions:

```text
data/category_rules_overrides.json
```

Preferred workflow:

1. Run pipeline.
2. Inspect `rule_suggestions.json` and `quality_report.json`.
3. Add narrow overrides.
4. Rerun steps 4, 5, 6.
5. Validate tests and quality metrics.

## Command Policy

Normal run:

```bash
./organize.sh data/bookmarks.html skill_config.json
```

Proxy run:

```bash
./organize.sh data/bookmarks.html skill_config.json --use-proxy --trust-env
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
python3 -m py_compile scripts/common.py scripts/1_copy_bookmark.py scripts/2_parse_bookmarks.py scripts/3_fetch_webpage_info.py scripts/4_classify_bookmarks.py scripts/5_cluster_bookmarks.py scripts/6_generate_html.py scripts/reset_pipeline_state.py
python3 -c "import json; [json.load(open(path)) for path in ['data/category_rules.json','data/category_rules_overrides.json','skill_config.json']]"
pytest -q
git diff --check
```

For behavior-sensitive changes, run a real pipeline pass using `/tmp` outputs if you do not want to overwrite tracked or user-facing generated files.

## Review Reports Before Finalizing

Inspect:

```text
output/reports/quality_report.json
output/reports/rule_suggestions.json
output/reports/needs_confirmation.json
```

For temporary verification outputs, use the same files under `/tmp` and report the metrics.

## Common Safe Improvements

- Add a new structured signal to `signal_pack`.
- Add a narrow topic alias or domain override.
- Add a regression test for misclassification.
- Adjust generic platform token filtering.
- Improve cluster destination routing from weak evidence to `待整理` or `发现主题`.
- Improve report fields that explain why a cluster needs attention.

## Common Risky Changes

- Raising confidence globally without real-output validation.
- Adding broad domains to a topic category.
- Reusing source folder names as strong evidence.
- Optimizing for fewer `待整理` items at the cost of false normal classifications.
- Treating `review_hierarchy` mirror count as duplicate output.
- Changing generated output shape without updating `scripts/6_generate_html.py` tests.

## Commit Readiness

A change is ready when:

- Worktree only contains intended source/docs/test changes.
- Tests pass.
- JSON config loads.
- Docs describe actual commands and output paths.
- Quality metrics do not regress in the direction of folder-only classification, low-confidence normal classification, or generic-platform suggestions.


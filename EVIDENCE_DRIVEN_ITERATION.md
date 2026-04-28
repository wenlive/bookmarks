---
name: bookmark-organizer-evidence-driven-iteration
description: Agent-facing playbook for running this project on a real bookmark export, preserving evidence, and turning runtime evidence into concrete improvements.
---

# Evidence-Driven Iteration

## Purpose

Use this playbook when an agent needs to:

- validate the project on a real Chrome bookmark export
- preserve intermediate artifacts and runtime evidence
- assess whether the pipeline behavior matches the design contract
- convert observed output quality into concrete taxonomy, clustering, fetch, or report improvements

This workflow treats the project as a running system first, then uses artifacts and metrics to drive code or generated-taxonomy changes.

## When To Use

Use this workflow when any of the following are true:

- the user asks whether the project actually works on a real bookmark export
- the user wants quality assessment based on artifacts, reports, and logs
- the change is behavior-sensitive and cannot be judged from unit tests alone
- the agent needs to propose taxonomy, clustering, report, or fetch improvements grounded in a real run

## Core Principles

- run against a real bookmark export, not only fixtures
- keep source inputs unchanged
- redirect generated state into an isolated runtime directory under `/tmp` unless the user explicitly wants tracked outputs replaced
- preserve stage logs and key snapshots
- prefer sequential execution for dependent stages
- separate network problems from taxonomy problems before proposing fixes
- use reports and metrics, not intuition alone

## Read First

Before a real evaluation, read:

1. `README.md`
2. `RUNBOOK.md`
3. `QUICK_REFERENCE.md`
4. `AGENTS.md`
5. `TODO_RUNTIME_FOLLOWUP.md`
6. `skill_config.json`
7. `scripts/common.py`
8. `scripts/3_fetch_webpage_info.py`
9. `scripts/4_classify_bookmarks.py`
10. `scripts/5_cluster_bookmarks.py`
11. `tests/test_pipeline.py`

Focus on:

- input and output contracts
- reset behavior
- fetch cache semantics
- trusted-access policy
- `signal_pack/v2` fields
- current report semantics
- latest real-run baseline and unresolved issues

## Recommended Workflow

### 1. Build Context First

Confirm:

- which bookmark export is being evaluated
- whether generated files may overwrite tracked outputs
- whether proxy access is needed
- whether this run is exploratory or intended to become the new baseline

### 2. Prefer An Isolated Runtime Workspace

For behavior-sensitive work, use a fresh directory under `/tmp`.

Example:

```bash
mktemp -d /tmp/bookmark_eval_XXXXXX
```

Create at least:

```text
data/
output/reports/
logs/
runtime_logs/
snapshots/
```

### 3. Materialize A Runtime Config

Start from `skill_config.json`, then rewrite all generated paths into the runtime workspace:

- copied bookmark file
- parsed file
- fetch cache
- classified file
- clustering file
- output HTML
- all reports
- log file

Keep generated user taxonomy paths isolated in the runtime workspace:

- `data/generated/user_taxonomy.json`
- `data/generated/bookmark_taxonomy_assignments.json`

Save the derived config as:

```text
/tmp/<run>/skill_config.runtime.json
```

### 4. Run Preflight Validation

Save validation output into `runtime_logs/` before the real run.

Commands:

```bash
python3 -m py_compile scripts/common.py scripts/1_copy_bookmark.py scripts/2_parse_bookmarks.py scripts/3_fetch_webpage_info.py scripts/4_classify_bookmarks.py scripts/5_cluster_bookmarks.py scripts/6_generate_html.py scripts/generate_taxonomy_bootstrap.py scripts/apply_taxonomy_response.py scripts/reset_pipeline_state.py
python3 -c "import json; json.load(open('skill_config.json'))"
pytest -q
```

Why:

- `py_compile` catches syntax issues immediately
- JSON loading catches config drift
- tests provide the code baseline before the real-input run

### 5. Reset Runtime State And Run Copy/Parse

Always reset the runtime workspace, not the tracked repo outputs.

Then run:

```bash
python3 scripts/reset_pipeline_state.py --config /tmp/<run>/skill_config.runtime.json --reset-all --source /path/to/bookmarks.html
python3 scripts/1_copy_bookmark.py --config /tmp/<run>/skill_config.runtime.json --source /path/to/bookmarks.html
python3 scripts/2_parse_bookmarks.py --config /tmp/<run>/skill_config.runtime.json
```

Capture:

- parsed bookmark count
- duplicate URL count
- unique domain count
- duplicate report sample

### 6. Run Fetch

Current recommended fetch mode is built-in proxy-first plus automatic direct retry:

```bash
export https_proxy=http://127.0.0.1:7897
export http_proxy=http://127.0.0.1:7897
export all_proxy=socks5://127.0.0.1:7897

python3 scripts/3_fetch_webpage_info.py \
  --config /tmp/<run>/skill_config.runtime.json \
  --use-proxy \
  --trust-env \
  --direct-retry-after-proxy
```

Why this is now preferred:

- first-class two-pass support is already implemented in `scripts/3_fetch_webpage_info.py`
- fetch stats record `multi_pass_mode`, `pass_summaries`, and `pass_deltas`
- successful proxy-pass rows are reused during direct retry

If you need explicit per-pass snapshots, copy `bookmarks_with_info.json`, `broken_links.json`, and `review_queue.json` after the proxy pass and again after the final pass.

### 7. Evaluate Fetch Before Moving On

Do not jump straight to classification.

Compare:

- success count
- broken count
- fail count
- review count
- trusted override count
- reason-code distribution
- domains that dominate review failures
- pass deltas between proxy and direct retry

Interpretation:

- high `http_error` counts usually mean site policy or anti-bot friction, not DNS failure
- high `dns_connection` or `timeout` counts suggest transport or routing issues
- high trusted-override counts mean review noise is being intentionally suppressed
- low direct-retry gain means proxy and direct paths see similar site behavior

### 8. Run Classify, Cluster, Generate Sequentially

If the run has no usable `data/generated/user_taxonomy.json`, generate the
bootstrap prompt before treating classification quality as final:

```bash
python3 scripts/generate_taxonomy_bootstrap.py --config /tmp/<run>/skill_config.runtime.json
```

Give `taxonomy_bootstrap_prompt.md` to the external model, save its strict JSON
response as `data/generated/taxonomy_response.json` in the runtime workspace,
then apply it:

```bash
python3 scripts/apply_taxonomy_response.py \
  --config /tmp/<run>/skill_config.runtime.json \
  --response /tmp/<run>/data/generated/taxonomy_response.json
```

Run in order:

```bash
python3 scripts/4_classify_bookmarks.py --config /tmp/<run>/skill_config.runtime.json
python3 scripts/5_cluster_bookmarks.py --config /tmp/<run>/skill_config.runtime.json
python3 scripts/6_generate_html.py --config /tmp/<run>/skill_config.runtime.json --output /tmp/<run>/output/organized_bookmarks.html
```

Important:

- do not run steps 4, 5, and 6 in parallel
- step 5 depends on step 4 output
- step 6 depends on step 5 output

### 9. Review The Artifacts Together

Inspect:

```text
data/parsed_bookmarks.json
data/bookmarks_with_info.json
data/classified_bookmarks.json
data/clustering_result.json
output/organized_bookmarks.html
output/reports/duplicates.json
output/reports/broken_links.json
output/reports/review_queue.json
output/reports/needs_confirmation.json
output/reports/rule_suggestions.json
output/reports/quality_report.json
output/reports/signal_audit.json
logs/bookmarks_organizer.log
runtime_logs/*.log
```

Do not evaluate quality from one report alone.

### 10. Translate Evidence Into Changes

Split findings by layer:

- fetch layer: network, parsing, retry, trusted-access behavior
- classification layer: rule coverage, confidence handling, topic-candidate quality
- clustering layer: cluster purity, root routing, label usefulness
- report layer: whether reports explain the failure modes well
- storage layer: artifact size and duplication

## Required Evidence To Preserve

At minimum, keep:

- the source bookmark HTML path used
- the runtime config
- validation logs
- reset, copy, parse, fetch, classify, cluster, and generate logs
- final HTML
- summary metrics from parse, fetch, classify, cluster, and reports

If the run becomes the new baseline, write a continuation document similar to `TODO_RUNTIME_FOLLOWUP.md`.

## Evaluation Checklist

### Fetch Layer

Ask:

- what is the real success rate
- how many failures are network-related vs site-policy-related
- which domains dominate `http_error`, `dns_connection`, `timeout`, and `certificate`
- did direct retry materially improve proxy-first results
- are XML or feed-like responses being handled sensibly

### Classification Layer

Ask:

- is `folder_only_classification_count == 0`
- is `low_confidence_normal_category_count == 0`
- how many items still land in `待整理`
- among `待整理`, how many are fetch failures vs rule gaps vs low-confidence items
- which `open_topic_candidates` repeat often enough to justify generated taxonomy aliases, title patterns, or assignments

### Clustering Layer

Ask:

- are normal roots useful to browse, or too flat
- are generic platforms still leaking into cluster labels
- are discovery clusters meaningful topics or mostly source/brand noise
- do mixed clusters reflect true overlap or weak similarity rules
- are fetch-blocked clusters kept out of `发现主题`

### Report Layer

Ask:

- does `needs_confirmation.json` clearly separate `fetch_blocked`, `rule_gap`, and `low_confidence`
- does `rule_suggestions.json` produce actionable next steps instead of generic noise
- does `quality_report.json` expose flat roots, discovery noise, review hotspots, and fetch-blocked clusters
- does `signal_audit.json` highlight existing unused signals worth consuming before new fetch work

### Storage Layer

Ask:

- are intermediate artifacts larger than they need to be
- is the same bookmark payload duplicated across multiple JSON trees
- is there a clean separation between operational artifacts and debug detail

## Current Baseline: 2026-04-28

Latest validated real-input run:

- source export: `data/bookmarks_2026_4_28.html`
- input bookmarks: `1014`
- duplicate URL groups: `92`
- unique domains: `439`
- fetch success: `745`
- fetch fail: `269`
- fetch review queue: `269`
- trusted overrides: `0`
- generated taxonomy categories: `41`
- generated cluster assignments: `371`
- classify `待确认`: `470`
- classify `待整理`: `407`
- cluster count: `623`
- discovery clusters: `1`
- mixed clusters: `5`
- generic-platform clusters: `235`
- largest generic-platform cluster size: `11`

Important interpretation:

- the pipeline now works on real data without checked-in category rule files
- generated user taxonomy materially improves discovery and mixed-cluster metrics
- the main problem is no longer global fetch failure or hardcoded default taxonomy
- remaining work is concentrated in:
  - generated taxonomy coverage gaps
  - generic-platform cluster naming and split quality
  - a small number of hotspot fetch domains
  - making the bootstrap prompt easier for external models to answer consistently

See `TODO_RUNTIME_FOLLOWUP.md` for the detailed continuation plan.

## What Changed Since The Older Baseline

The current code already includes improvements that older notes may still mention as future work:

- built-in proxy-first plus direct-retry fetch support
- fetch provenance in `fetch_context`
- XML-aware response parsing
- removal of operational fetch terms such as `fetched` and `success` from dynamic topic extraction
- signal audit output
- stage schema enforcement

Do not spend another round re-implementing those solved items.

## Recommended Next Focus

If you are starting a new iteration today, prioritize:

1. discovery and generic-platform cluster naming cleanup
2. generated taxonomy additions for repeated successful `rule_gap` domains/topics
3. hotspot fetch investigation for remaining `review_queue` domains
4. selective consumption of already-collected unused signals such as `status_code`, `nav_text`, and `code_languages`

## Execution Notes

- keep the full runtime directory after a meaningful real run
- if the user wants a final artifact copied elsewhere, do that after the runtime copy is complete
- when proposing changes, tie them to concrete artifacts from the run, not abstract style preferences
- if a run uncovers operational mistakes, preserve the evidence and then rerun correctly

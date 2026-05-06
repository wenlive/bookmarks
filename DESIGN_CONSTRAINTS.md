---
name: bookmark-organizer-design-constraints
description: Persistent design constraints that future code, prompts, reports, and workflow changes must continue to follow.
---

# Design Constraints

## Purpose

This file records product and implementation constraints that should survive
future refactors, taxonomy changes, prompt changes, and agent-driven
iterations.

Treat these constraints as part of the checked-in design contract, not as a
one-off discussion note.

## 1. User-General, Not User-Hardcoded

The checked-in repository should stay useful across different users with
different bookmark distributions, interests, and topic vocabularies.

Required behavior:

- do not hard-code personal topic trees, personal trusted domains, or broad
  one-off rules as the default classifier behavior
- keep tracked defaults focused on generic guardrails, resource typing,
  intent labels, quality signals, generic-platform suppression, and explainable
  confidence thresholds
- put user-specific topic constraints in generated files such as
  `data/generated/user_taxonomy.json` and
  `data/generated/bookmark_taxonomy_assignments.json`
- prefer reusable signals and scoring over direct `if domain == ... then category`
  logic whenever the decision should generalize across users

When a heuristic only works for one user's corpus, it belongs in generated
taxonomy or assignment outputs, not in tracked default logic.

## 2. LLM-Aided, Not LLM-Embedded

LLMs may improve taxonomy generation, cluster follow-up, and targeted
classification guidance, but the project should not directly embed vendor API
clients or require a specific hosted model to run.

Required behavior:

- keep LLM integration file-based and schema-based
- generate prompts, structured evidence, and strict JSON response contracts
- accept responses through local files and import scripts
- assume an external agent or skill can provide formatted LLM I/O
- do not add direct `call model API from pipeline` behavior as the default path

Good patterns:

- bootstrap prompt generation
- follow-up candidate bundles
- strict response schemas
- import-and-apply scripts

Bad patterns:

- hidden online API calls during classify or cluster
- product behavior that silently changes based on model availability
- coupling the repository to one provider's SDK

## 3. Network And Proxy Behavior Must Stay Explicit

Real-world bookmark fetching may require both VPN/proxy access and direct
access. The project must continue to support both instead of assuming one
network path is always correct.

Required behavior:

- preserve direct fetch support
- preserve proxy-enabled fetch support
- preserve proxy-first plus direct-retry behavior unless the user asks for a
  different fetch strategy
- keep fetch provenance visible in outputs and reports
- prompt the operator for proxy environment variables when real fetching is
  likely blocked without them

Canonical proxy example:

```bash
export https_proxy=http://127.0.0.1:7897
export http_proxy=http://127.0.0.1:7897
export all_proxy=socks5://127.0.0.1:7897
```

Fetch improvements should separate:

- transport problems
- site policy or anti-bot problems
- taxonomy or clustering problems

Do not hide network uncertainty by over-broad trusted-access defaults.

## 4. Extract User-Specific Structure From The User's Corpus

The system should learn from what is frequent or structurally important inside
the current user's bookmark set, even when those terms are rare globally.

Required behavior:

- prefer corpus-derived signals such as repeated product names, project names,
  domain families, cluster hints, and stable title phrases
- treat within-user high-frequency but globally niche tokens as potential
  taxonomy guidance
- surface these signals in reports or LLM prompt bundles instead of forcing
  them into tracked defaults
- prefer adding reusable signal extraction in `signal_pack/v2` over duplicating
  ad hoc extraction logic

Examples of useful user-specific structure:

- a niche database project repeatedly appearing across blogs, docs, repos, and
  papers
- a product family with multiple domains but consistent title vocabulary
- a recurring topic token that is too niche for built-in defaults but strong
  inside one user's corpus

## 5. Optimize The Final Bookmark Bar For Browsing And Retrieval

The final HTML is a browsing product, not only a clustering artifact. Output
structure should optimize for human scanning, retrieval, and re-finding.

Required behavior:

- keep the top-level folder layout balanced for real browsing
- avoid collapsing most useful topics into only a few oversized roots
- avoid exploding many tiny topics into excessive top-level folders
- avoid creating dedicated visible folders for extremely small themes unless
  they are strong and worth direct access
- use grouping and display logic to keep the result browsable on a bookmark bar
- when changing display routing, inspect whether the output became harder to
  scan even if topic purity improved

Working preference:

- a moderate number of top-level groups
- meaningful separation between normal roots, `待整理`, `发现主题`, and `待审阅`
- normal roots that are individually useful, not flat dumps
- cluster labels that are human-readable topic names, not platform or source
  noise

When changing hierarchy behavior, prefer adding or extending quality metrics
that reveal:

- overly flat normal roots
- too many singleton or tiny visible folders
- oversized roots hiding many unrelated subtopics
- deterioration in bookmark-bar scanability

## 6. Persistence, Reviewability, And Change Discipline

These constraints must remain durable across future changes.

Required behavior:

- keep this file tracked in git
- update `README.md`, `AGENTS.md`, and quick references when these constraints
  materially change
- treat changes that violate these rules as design regressions, not neutral
  refactors
- add tests or report checks when a new heuristic changes classification,
  clustering, or display routing behavior

Before finalizing a behavior change, check:

- does it add personal hard-coded topic behavior to tracked defaults
- does it depend on a built-in live model API
- does it assume proxy or direct access is always correct
- does it extract more reusable corpus-specific evidence, or merely add a
  one-off rule
- does the final visible hierarchy become easier or harder to browse

## Preferred Implementation Patterns

- generic default guardrails in tracked code
- user-specific specialization in generated taxonomy and assignment files
- prompt generation plus strict JSON import for LLM-assisted refinement
- explainable evidence in classifier and clusterer outputs
- report-driven iteration grounded in real bookmark exports

## Avoid

- broad platform domains as topic domains
- folder-only or folder-dominant topic assignment
- hidden online dependencies in the local pipeline
- personal topic defaults committed as shared repository logic
- display structures that optimize purity while becoming awkward to browse

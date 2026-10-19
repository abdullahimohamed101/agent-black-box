# ADR-081: Health Is a Set of Explained Dimensions With Recorded Thresholds, Not a Score

Status: Accepted
Date: 2026-10-11
Phase: 10

## Context
Spec §10.2 shows per-dimension scores and an `85/100` overall and says the score "should be explainable rather than magical"; the roadmap for Phase 10
says "explainable health dimensions only (no unexplained score)"; §80.3 requires heuristic conclusions to be labelled probable.

## Decision
- A run's health is computed **at read time** by a pure function over the run's derived figures, its findings and the findings state: five dimensions
  (`reliability`, `tool_reliability`, `cost_efficiency`, `loops`, `context`), each `ok | warn | bad | no_data` with the input figures, the one-sentence rule
  that decided it and the finding ids involved. `overall` is the worst dimension's status as a word and `no_data` never masks a `warn`. There is no
  numeric score and no weights; a test asserts that none appears in the response.
- The finding-based dimensions (`loops`, `context`, and the cost-spike part of `cost_efficiency`) are `no_data`, with the reason in the rule, unless the
  analysis is `current`, so a run analysed by no detector, by outdated rules or whose latest analysis failed never reads `ok`. `reliability` and the
  ratio parts read the run summary and never depend on the analysis state, so a failed analysis cannot make a failed run read healthy.
- Detector thresholds are deployment-wide settings (`ABB_FINDINGS_*`) with defaults in code; every finding records the thresholds it used in its
  evidence. Rules are rates and absolute sizes rather than ratios where a ratio would flag ordinary behaviour: a multi-turn agent's context is
  non-decreasing by construction, so context growth is reported on the increment per call and on super-linear growth or an absolute size, and `info`
  below 100,000 tokens; five spaced retries are `info`, a burst is `warning`.
- Findings are `deterministic` (restating typed events, confidence 1.0) or `probable` (interpretations, confidence from the rule's own evidence).
  Probable findings carry "Probable:" in their title and the UI never signals kind or severity by colour alone. A probable loop on a successful run is
  `info` however many times it repeats (`warning`, or `critical` from 10 repetitions, only when the run did not succeed or is still active), and the UI says that severity may change until the run ends.

## Alternatives
A weighted 0-100 score (arbitrary weights, not actionable, contradicts the roadmap); per-project thresholds now (needs a table, write route, action, UI
and audit: Phase 14); storing health on the run (a function of two derived tables; recomputation takes microseconds).

## Consequences
A reader always sees why a dimension is `bad`; comparing runs by a single number is not possible until a later phase defines one with recorded weights;
tuning thresholds is an operator action that changes future findings only (past ones keep their recorded thresholds until rebuilt). Agent-level health
across runs is a follow-up on the analytics rollups (KI-090).

## Revisit conditions
Users ask to sort or alert on a single number (define one with recorded weights); per-project thresholds are needed (KI-092).

## Amendment (2026-10-18, Phase 20, ADR-174)
While a newer analysis is queued or running (`processing`) and an earlier completed analysis of the current rules exists, the finding-based dimensions read that analysis and report `as_of` and `stale: true` instead of `no_data`. `none`, `outdated` and `failed` are unchanged.

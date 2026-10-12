# Phase 11: comparison alignment cost (AC-7)

Command: `cd apps/api && uv run python ../../scripts/bench_compare.py --repeat 5` (no database needed; seeded inputs).
Environment: Python 3.12.15, macOS 26.6 arm64 (a development laptop), median of 5 runs. A regression baseline for this
repository, not a capacity claim; CI asserts only the 3 s bound (`tests/test_comparison_align.py`).

Defaults: `ABB_COMPARE_MAX_EDITS=600`, `ABB_COMPARE_MAX_STEPS=2000`, `ABB_COMPARE_MAX_EVENTS=20000`, and a fixed work
cap of 4,000,000 comparisons and diagonal steps per alignment.

| case | median ms | quality |
| --- | ---: | --- |
| disjoint keys, 2,000 vs 2,000 | 22.4 | partial |
| shifted by one, 2,000 | 2.0 | full |
| two seeded random sequences over 3 keys, 2,000 | 28.5 | partial |
| alternating 3 tools with one inserted step, 2,000 | 0.6 | full |
| 3-key random, 20,000 vs 20,000 (the `ABB_COMPARE_MAX_STEPS=20000` mutation) | 50.3 | partial |
| 3-key random, 2,000, `max_edits` 4,000 (the work cap is what bounds it) | 130.5 | full |
| `compare_runs`, two 20,000-event runs, content projection (cut to 2,000 steps per side) | 55.3 | partial |
| `compare_runs`, two 20,000-event runs, metadata projection | 43.9 | partial |

Reading the table:

- The low-cardinality worst case of LCS-style matchers (random 3-key sequences, which is also the shape of every
  metadata projection) answers `partial` in tens of milliseconds instead of stalling: the edit distance of two random
  3-key sequences of 2,000 is about 1,400, past the budget of 600, so the middle is reported as one `left_only` and one
  `right_only` block and the response says `alignment_quality: partial`. The common prefix is aligned first, so the first
  divergence is exact whatever the quality (tested with a seeded 300-step shared prefix).
- Realistic pairs (an inserted step, a replaced edit, a shifted run) are `full` in a few milliseconds because the prefix
  and suffix strip leaves a tiny middle.
- Raising `ABB_COMPARE_MAX_STEPS` to 20,000 does not time out: the edit budget and the work cap bound the Myers diff, so a
  random pair of 20,000 answers `partial` in 50 ms (mutation check recorded in the Phase 11 plan).
- `difflib.SequenceMatcher` is not used (ADR-092): quadratic on low-cardinality input with `autojunk=False`, and
  `autojunk=True` discards every frequent key.

## Experiment metrics statement (AC-7)

`tests/test_experiments_api.py::test_metrics_over_five_hundred_runs_and_twenty_evaluators_stay_under_the_timeout`
seeds an experiment at its bounds (500 attached runs in two variants, 20 evaluators, 10,000 completed evaluation rows,
bulk inserted) and times `GET /v1/experiments/{id}` five times through the ASGI client on the same machine and Postgres
(compose, port 5433): median 61 ms for the whole request (the metrics statement, the variants, the attached runs and the
evaluator lookups), against the `ABB_ANALYTICS_TIMEOUT_SECONDS` bound of 10 s that the statement runs under
(`SET LOCAL statement_timeout`). The statement is one query: exact `percentile_cont` over the finished runs of each
variant and the latest terminal result per (run, evaluator) with `DISTINCT ON`.

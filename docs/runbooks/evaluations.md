# Runbook: evaluations, experiments and comparison

Evaluations are derived records (ADR-090): a person (or "Evaluate all" on an experiment) inserts `pending` rows, an `evaluate_run` job computes them in the worker and every row
ends `completed` or `failed`, immutable. A failed evaluation never changes a run. Comparison (ADR-092) is computed on read and stores nothing.

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `ABB_EVAL_TIMEOUT_SECONDS` | 25 | cooperative time budget of one evaluator; must be below half `ABB_WORKER_LEASE_SECONDS` (startup refuses otherwise). Unset, it is clamped to 0.4 of the lease |
| `ABB_EVAL_MAX_EVENTS` | 50000 | events read per evaluation; a bigger run fails the row `RUN_TOO_LARGE` |
| `ABB_EVAL_STALE_SECONDS` | 3600 | a `pending` row older than this (or whose job was dead-lettered) reads as `failed/JOB_FAILED` |
| `ABB_EVAL_BATCH_DELAY_SECONDS` | 5 | delay of the jobs "Evaluate all" enqueues, so summaries of live runs go first |
| `ABB_JUDGE_PROVIDER` | `none` | `none`: judge evaluators cannot be created (`409 JUDGE_NOT_CONFIGURED`); `fake`: a deterministic scorer that reads only the run's status and error count (plumbing, not a signal). `fake` needs `ABB_ENVIRONMENT` development or test, or `ABB_JUDGE_ALLOW_FAKE=1` |
| `ABB_JUDGE_COOLDOWN_SECONDS` | 300 | after a judge reply costs more than the evaluator's `max_cost_usd` (`failed/JUDGE_BUDGET`) the evaluator is refused for this long |
| `ABB_COMPARE_MAX_EVENTS`, `_MAX_STEPS`, `_MAX_EDITS` | 20000, 2000, 600 | comparison bounds (below) |

There is no real model provider yet (KI-100). Judge evaluator config: `rubric` (at most 4,000 characters), `allowed_labels`, `include_content` (false: command text and paths never reach a model),
`max_input_chars` (5,000 to 200,000), `max_output_tokens`, `max_cost_usd` (a reporting and cooldown bound, not a hard cap: the provider's reply is billed before it is measured).

## Reading the rows

- `pending`: waiting for the worker. If the worker is down, rows stay pending until `ABB_EVAL_STALE_SECONDS`, then read as `failed/JOB_FAILED` (no write); starting the evaluator again adds a new row.
- `failed`: the `error_code` says why. `TIMEOUT` (budget), `RUN_TOO_LARGE`, `EVALUATOR_ERROR` (the built-in's own message, e.g. a glob list too large), `INVALID_RESULT`, `JUDGE_BUDGET`, `JUDGE_UNAVAILABLE`, `JOB_FAILED`.
  Operators read rows with `GET /v1/runs/{id}/evaluations` (there is no CLI command for it, KI-108). A failed row is history; a new trigger makes a new row (at most 20 per run and evaluator; the 21st is `409 LIMIT_REACHED`).
- Dead-lettered `evaluate_run` jobs: see `dead-letters.md`; the rows read `failed/JOB_FAILED`. Requeueing a job whose rows are terminal changes nothing.
- A model judge's row is an opinion: `ground_truth: false`, `metadata.judge_model`. The UI labels it "model judge, not ground truth" everywhere it shows or aggregates it.

## Cost of "Evaluate all"

One click starts evaluators x runs pairs, at most 500 new rows per call (`remaining` says whether to click again), one job per run, delayed by `ABB_EVAL_BATCH_DELAY_SECONDS`. Built-in evaluators cost worker
time only (milliseconds per run). A real judge would cost money per pair; with the fake it is free. A repeat click while rows are pending enqueues nothing, and completed pairs are not redone. The worker is shared with
summaries and findings (KI-104): a very large batch delays them by about the batch size, not indefinitely.

## Comparison answers

`alignment_quality: partial` means the middle of the two runs needed more than the edit budget: the first divergence is exact, the rest is one block per side. A truncated side says how many steps were compared.
Raise the budgets only after measuring (`scripts/bench_compare.py`, `docs/benchmarks/phase-11-compare.md`). A viewer's comparison is metadata only by design (ADR-092, KI-106).

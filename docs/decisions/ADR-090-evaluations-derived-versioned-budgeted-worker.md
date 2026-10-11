# ADR-090: Evaluations are derived, versioned records with independent state, computed by the outbox worker in a budgeted thread

Status: Proposed
Date: 2026-10-11
Phase: 11

## Context
Spec §85 asks for evaluators with their own states whose failures never change a run; INV-1/INV-2 forbid editing events and require
derived state to be rebuildable; the `evaluations` skeleton table exists since migration 0005; the worker and outbox (ADR-012) give at-least-once,
leased, bounded background execution, but a handler that does not yield starves the lease heartbeat.

## Decision
An evaluation is a row produced by applying one versioned evaluator definition (with a configuration snapshot) to a run's events; a
re-run is a new row; a terminal row is never updated (`UPDATE ... WHERE state = 'pending'` is the only transition out of pending); human verdicts
are rows of kind `human` completed at insert; `ground_truth` is derived from `kind`. Evaluators are workspace definitions of built-in
implementations (deterministic, metric, human, model judge) configured by validated, byte-bounded JSON; no user code runs in the worker; path-bearing
configuration and rubrics are Class 2 content (withheld without `payload.read`). Triggering inserts a `pending` row and an `evaluate_run` job (one
per run, carrying the evaluation ids) in one transaction; the handler takes no run lock, reads at most `ABB_EVAL_MAX_EVENTS` committed events once
per run (Phase 10's capped `load_run`), runs each implementation in `asyncio.to_thread` under `asyncio.wait_for` with a cooperative `Budget` the
built-ins check, with a timeout below half the lease so the heartbeat keeps running, and records `completed` or `failed` with an error code and a
templated error message (never `str(exc)`). Evaluator exceptions, timeouts, invalid results and oversized runs are results, not job failures; only
infrastructure errors retry. The handler never writes run, span, event or cost tables and never enqueues a summary. Pending rows whose job died
read as `failed` through `summary_states(job_type)` without a write. Results carry `score`, `label`, `explanation`, `metrics` (per-implementation
key allowlist), `evidence_event_ids` (validated to the run). Free-text explanations of human and judge results need `payload.read`. Bounds under
advisory locks: 100 evaluators per workspace, 20 rows per (run, evaluator), 50 human rows per run; a non-terminal row is returned instead of
duplicated. Experiments are a read-model over `runs` and `evaluations` (one statement, exact percentiles over at most 500 runs). Automatic
evaluation on run completion is deferred (it hooks the summarizer, shared with Phases 10 and 16). 
**Residual:** a thread that ignores its budget
occupies the worker until it returns; the event cap and glob bounds are the real limits. **Revisit:** a priority column or a second worker pool when
evaluation batches measurably delay summaries.

## Alternatives
Evaluate inside `summarize_run` (couples run derivation to evaluator cost and failures; forbidden by §85.2). Store results as
events (not telemetry; different author, lifecycle and permissions). A plugin loader for user evaluators (arbitrary code in the worker; later,
behind an ADR). An append-only table (state transitions need UPDATE; terminal immutability is the `WHERE state = 'pending'` predicate plus a test).
Killing a thread on timeout (impossible in CPython; hence the cooperative budget). One job per evaluation (loads the run's events N times and floods
the shared queue).

## Consequences
Results are reproducible from (events, evaluator version, config); the worker gains one bounded handler; evaluation history grows
per re-run up to the bound (retention is Phase 19); people trigger evaluations, SDK keys cannot until a scope is added (KI); batches of evaluations
delay summaries of other runs by at most the batch length (KI).

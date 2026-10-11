# Reliability

Source: spec §44, §61, §115, §117-118, §155.

## Delivery semantics
At-least-once submission, idempotent persistence on `(workspace_id, event_id)`. `202` = committed
to PostgreSQL. Late events accepted after run completion. Derived state is rebuildable.

## Failure matrix (expected behaviour; each row gets a test)
| Failure | Agent impact | SDK | Server | User-visible |
| --- | --- | --- | --- | --- |
| API unavailable | none | bounded buffer + retry | n/a | trace delayed |
| SDK queue full | none | drop/sample low priority; P0 events last | n/a | data-loss indicator |
| Invalid event | none | record exporter error, no raise | reject with typed error | integration warning |
| PostgreSQL down | ingestion degraded | retry/backoff | retryable 503 | live trace delayed |
| Worker down | none | n/a | events persist, derived delayed | summary "processing" |
| Object store down | none unless artifact required | retry upload | keep metadata | artifact warning |
| Browser loses its connection | none | n/a | stream slot freed on disconnect | "Reconnecting" + "Partial data", resumes from the last event, history reloaded after a gap over 20 s |
| Stream listener (`LISTEN`) connection lost | none | n/a | reconnects with backoff, wakes every stream; streams poll every 2 s meanwhile | live updates up to 2 s late |
| Database error while streaming | none | n/a | in-band `STREAM_UNAVAILABLE` error, stream ends | "Reconnecting", then resumes |
| Client stops reading a stream | none | n/a | dropped after 10 s, slot freed | that viewer reconnects |
| Too many streams | none | n/a | `429 STREAM_LIMIT` + `Retry-After` | "Live updates unavailable", page polls |
| Policy service down | per capability | configured fail-open/closed | emit outage event | explicit message |

## Limits and budgets
Event 256 KB; batch 5 MB uncompressed; SDK defaults batch 100 / 250 ms / queue 10,000 / timeout 2 s.
Targets (not promises until measured): ingest ack p50 <50 ms, p95 <150 ms; live propagation p95 <1 s;
run detail p95 <500 ms; dashboard p95 <1.5 s. Measured results are recorded in `docs/benchmarks/`.
Live streams (per API process, settings): 50 total, 10 per key, 15 min lifetime, 15 s keepalive, 2 s fallback poll, 30 s resume overlap,
10 s write timeout. Measured accept-to-display p95 is about 9 ms on one machine (`docs/benchmarks/phase-5-streaming.md`).

Evaluations (ADR-090): the `evaluate_run` job is the only writer of terminal evaluation states and never touches `runs`. The evaluator runs in a worker thread under a cooperative budget (`ABB_EVAL_TIMEOUT_SECONDS`, 25 s,
validated below half the worker lease, checked every 1,000 events and per glob) while the lease heartbeat keeps running; a run above `ABB_EVAL_MAX_EVENTS` (50,000) fails the row (`RUN_TOO_LARGE`). Any exception, garbage result or timeout
becomes one `failed` row with a fixed code; a database outage inside the handler retries the job. A pending row whose job was dead-lettered, or that is older than `ABB_EVAL_STALE_SECONDS` (1 h), reads as `failed/JOB_FAILED` without a write.
Bounds: 20 results per (run, evaluator), 20 pending rows claimed per job, 500 new rows per evaluate-all call, 500 runs and 20 evaluators per experiment. Residual: a thread that ignores its budget keeps its executor thread until it
returns, which can delay a graceful worker stop (ADR-090). Comparison (ADR-092) is computed on read in a thread: at most `ABB_COMPARE_MAX_EVENTS` (20,000) events and `ABB_COMPARE_MAX_STEPS` (2,000) steps per side, a Myers diff bounded by
`ABB_COMPARE_MAX_EDITS` (600) and a fixed work cap; beyond it the answer is `partial` in tens of milliseconds, not a stall (`docs/benchmarks/phase-11-compare.md`, KI-105).

Findings (ADR-080): the `detect_findings` job is the only writer of `findings` and `run_findings`, one writer per run (an advisory lock; the loser re-enqueues), derived
and rebuildable from events. Bounds: at most `ABB_FINDINGS_MAX_EVENTS` (50,000) events read per run in canonical order (`truncated` is reported); 50 findings per detector and 200
per run; 50 event ids and 20 pattern tokens per finding; client strings truncated to 128 characters; evidence under 15 KiB; delay `ABB_FINDINGS_DELAY_SECONDS` (5 s) growing with run size
to at most 60 s. A detector exception dead-letters only its own job (fixed error text, no attribute values), the summary stays `current` and the route reports `failed` while still
serving the last good findings. Every cost rebuild also re-runs the detectors (about double the per-run worker work). Measured: about 44 microseconds per event, 1.33 s for a 30,000-event run
(`docs/benchmarks/phase-10-regression-check.md`, KI-096).

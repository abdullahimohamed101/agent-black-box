# Phase 17 step 4: ingestion latency with and without enabled alert rules (gate 8)

Measured 2026-10-14 by `scripts/alerts-e2e.sh` (stage 8, `gate8` in `scripts/alerts_e2e_check.py`) on branch `feature/phase-17-alerting`: Apple silicon laptop (macOS arm64, 15 CPUs), Postgres 16 in a
container on the same machine, `uvicorn` with one process for the API and one worker, a real project-bound API key, raw `httpx` posting to `/v1/events/batch` (the SDK's transport is not in the loop).
The laptop was **not idle** (a browser, the web build and other agents' processes were running; the load average is printed with each run), so three runs are shown: the spread is the noise.
These figures are a regression baseline for this repository on one laptop, not a capacity claim.

## Method

- The `bench` project has six alert rules, all pointing at one webhook destination on the local receiver: `run_failed` twice (agent-filtered, a 1-hour cooldown), `approval_requested`, `approval_expiring`
  (10-minute lead), `security_finding` (minimum severity `info`) and `spend_over` ($1,000,000, which never fires).
- A phase posts **30 batches of 100 events**, each batch one new run of 98 tool calls ending in `run.failed` (so the run-failed rules match and write notification rows), one batch at a time, timing each
  POST (client side, to the `202`). It then waits until every run is summarised and records how long the whole phase took (`drain`: ingestion, summaries, findings, analytics and alert jobs).
- Phases alternate **rules off, rules on, rules off, rules on** (the six rules are muted and un-muted through the management API), so drift hits both conditions equally. Each condition is 60 batches.
- Bound asserted by the script (generous; a shared CI runner): with the rules on, p50 <= 1.5 x (p50 off) + 10 ms and p95 <= 2 x (p95 off) + 50 ms.
- What the rules can cost on the ingestion path: nothing is evaluated inline. The producers (summary, findings, approvals) enqueue one small keyed outbox job in their own transaction, after ingestion has
  answered; the step-1 harness (`apps/api/tests/test_alerts_overhead.py`) measured the same at p50/p95 35.1/58.1 ms off and 33.1/36.6 ms on.

## Results (milliseconds per 100-event batch)

| | A (load average 4.0) | B (load average 1.0) | C (load average 1.6) |
| --- | --: | --: | --: |
| rules off, p50 / p95 / max | 43.3 / 62.1 / 77.1 | 65.8 / 93.5 / 153.0 | 57.8 / 78.3 / 108.7 |
| rules on (6 enabled), p50 / p95 / max | 36.3 / 57.8 / 66.3 | 69.1 / 95.7 / 129.0 | 71.6 / 94.6 / 145.1 |
| drain per phase (s), off then on, two rounds | 2.9, 2.4 / 2.4, 2.5 | 2.8, 4.2 / 3.2, 3.9 | 2.8, 3.7 / 3.0, 4.2 |

Read: with six enabled rules, of which two match every run, the median batch took -16 %, +5 % and +24 % compared with the muted rules, and the p95 -7 %, +2 % and +21 %; run A (rules on faster)
shows how large the noise is on this machine, and run C is the worst case seen. The time for the worker to finish a phase (summaries, findings, analytics and the alert jobs) is within the same spread
(2.4-2.9 s off versus 2.4-2.5 s on in A, 2.8-4.2 s versus 3.2-3.9 s in B, 2.8-3.7 s versus 3.0-4.2 s in C). Every run is inside the stated bound. The honest reading is that any cost of enabled rules on the ingestion request is
smaller than the run-to-run noise of a laptop; the per-event evaluation work happens in the worker, after the request has returned.

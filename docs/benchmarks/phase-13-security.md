# Phase 13 security benchmark (gate 7)

Gate 7 of the Phase 13 plan: scans have a size and time budget, and ingestion latency does not regress beyond a measured bound.
Measured 2026-10-13 on branch `feature/phase-13-security-observability` (the code of the step-4 commits) against `main` at `c25beaf`.
These figures are a **regression baseline for this repository on one laptop**, not a capacity claim.

## Method

`scripts/security_bench.py` talks only HTTP and needs a write key and a read key, so the same script measured both servers:

1. **Batches.** 40 sequential `POST /v1/events/batch` of 100 events each (4,000 events), every event with an inline payload of
   about 1 KB of prose (a stdout-like string); one run per batch. Client-side wall time per request; p50, p95 and max.
2. **A large run.** One run of 10,000 events (a third each shell commands, file reads and tool calls, half of them with a
   1 KB payload) posted in batches of 100: ingestion time, then the time from the last acknowledgement until the run's summary and
   findings are both `current` (includes the 1 s summary debounce, the 1 s findings delay and the worker poll).
3. The branch project captures `FULL` payloads, so every payload is scanned and redacted; `main` stores payloads as sent (it has no
   scan). The rate limiter is raised (`ABB_RATE_LIMIT_EVENTS_PER_SECOND=1000000`) so the pipeline, not the throttle, is measured.

**What was compared.** `main` (a `git worktree` of `c25beaf`, its own database and port, `uv sync --frozen`) against the branch,
alternating `main`, `branch`, `main`, `branch` ... for 4 rounds each, on the same machine (Apple M5 Pro, 15 cores; Postgres in a
Colima VM with 2 vCPU and 4 GiB; API and worker are host processes). The machine was not idle (an editor, a browser and other
agents were running), which is why single rounds differ by 30 % or more; compare medians, not rounds.

## Results (4 alternating rounds each)

| Measure | `main` median (range) | branch median (range) |
| --- | --: | --: |
| 100-event batch, p50 | 56.4 ms (44.9 - 59.2) | 53.6 ms (41.3 - 68.8) |
| 100-event batch, p95 | 70.2 ms (55.0 - 83.3) | 66.5 ms (57.1 - 95.3) |
| 100-event batch, worst single request | 83.8 - 114.6 ms | 85.7 - 516.2 ms (one outlier round) |
| 10,000-event run, ingestion | 11.6 s (8.4 - 13.4) | 11.8 s (8.8 - 16.5) |
| 10,000-event run, last ack to summary and findings `current` | 4.3 s (3.4 - 4.9) | 5.9 s (5.2 - 7.0) |
| `findings computed` of a 10,000-event run (worker log, 9 and 15 samples) | 472 ms median | 920 ms median |

Reading: the scan and the settings read are not visible in batch latency (the branch is inside the noise of `main`; ADR-130
measured the scanner at about 48 microseconds for a typical 1 KB event and a 100-event batch at 43 ms with the scan against 37 ms
without). The cost of Phase 13 shows in the findings job of a very large run: the six security detectors, the project settings
read and the artifact redaction read add about 450 ms to the job (472 ms to 920 ms for 10,000 events with about 6,600 distinct
commands and paths), which is about 1.5 s more before the run reads `current` (the job also runs again when late events arrive).
ADR-131 measured the detectors alone at 115 ms on a different mix of events; the remainder was not profiled. The job stays one run at a time per run id and is debounced, so a 10,000-event run costs the worker under a second.

The same script ran inside `scripts/security-e2e.sh` (CI shape: the full stack, other work in the same minute): 100-event batches
p50 56.3 ms, p95 61.4 ms, max 76.9 ms; 10,000-event run ingested in 8.1 s and current 6.8 s after the last acknowledgement.

## The bound the script asserts

`scripts/security-e2e.sh` runs `security_bench.py --assert`, which exits 1 when any of these is exceeded. They are about 4 to 8 times
the measured medians, so that a slow shared runner passes and a real regression (a scan that costs milliseconds per event, or a
detector that is quadratic in the run) does not:

| Bound | Value | Measured here |
| --- | --: | --: |
| 100-event batch, p50 | 250 ms | 54 - 56 ms |
| 100-event batch, p95 | 600 ms | 61 - 67 ms |
| 10,000-event run, ingestion | 60 s | 8 - 12 s |
| 10,000-event run, last ack to `current` | 60 s | 5 - 7 s |

The spec's targets for batch ingestion are p50 < 50 ms and p95 < 150 ms (spec 61.2) on target hardware; the branch is inside the
p95 target on this laptop and at the edge of the p50 target here, as `main` is. The shared-runner bound is deliberately looser.

## Scan budgets (the other half of gate 7)

Every scan has a size and a time budget (`ABB_SECURITY_SCAN_EVENT_MAX_SECONDS` 1 s, `ABB_SECURITY_SCAN_ARTIFACT_MAX_BYTES` 4 MiB,
`ABB_SECURITY_SCAN_ARTIFACT_MAX_SECONDS` 10 s) and fails closed. `scripts/security-e2e.sh` starts a second API with a
nanosecond time budget and a 2 KiB artifact cap and asserts that the payload is dropped with `redaction.dropped_reason`
`scan_failed` or `too_large`, that nothing unscanned is stored, and that the stored artifact body is a marker. Hostile-input timings
(1 MB, every pattern's worst case, under 0.4 s each) are in ADR-130 and `tests/test_security_scanner.py`.

## Reproduce

```text
make security-e2e                 # the whole stack, including this benchmark with its bounds
uv run --project apps/api python scripts/security_bench.py --api URL --key-file F --read-key-file G [--batches 40] [--run-events 10000]
```

To compare with `main`, start `main` (a worktree, its own database) and run the same script against it with `--label main`.

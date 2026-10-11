# Ingest regression check for Phase 10 (reliability findings)

Measured 2026-10-11 with `scripts/bench_ingest.py`, comparing the base commit `c212058` (main before Phase 10) with this branch
(`feature/phase-10-reliability-intelligence` at the E2E commit; the code under test is unchanged by the docs commit). Purpose: show what the
`detect_findings` job and the findings badge cost the existing paths (plan AC-11).

## Environment and method (read before comparing with other tables)

The user's compose stack (Colima VM, 2 vCPU) belongs to the user and could not be rebuilt for this check, so this is **not** the Phase 2 or Phase 7
setup and **the numbers are not comparable with `phase-2-ingestion.md` or `phase-7-regression-check.md`**. Only the before/after comparison within this
file is meaningful.

| | |
| --- | --- |
| Host | Apple Silicon Mac, macOS (Darwin 25.6) |
| Processes | **native Python processes, not containers**: for each variant one `uvicorn` API and one worker started from source (`uv run`), on its own scratch database in the shared Colima Postgres 16 on port 5433 (`abb_p10_bench_base`, `abb_p10_bench_new`, dropped afterwards) |
| Variants | `base`: `git archive c212058` extracted to a temp directory (migrations to 0047, no detector); `new`: this branch (migrations to 0060) |
| Settings | defaults, except `ABB_RATE_LIMIT_EVENTS_PER_SECOND=1000000 ABB_RATE_LIMIT_BURST_EVENTS=1000000` (so the pipeline, not the throttle, is measured) and `ABB_LOG_LEVEL=INFO`; summary debounce 1 s, findings delay 5 s (30,000 events: 15 s) |
| Workload | `bench_ingest.py`: batches of 100 events (515 bytes each), 90% `tool.call.completed` and 10% `llm.request.completed`. Invocation A: `--run-events 1000` (full: ingest, reads, freshness). Invocation B: `--ingest-only` (one 30,000-event run, then 4 and 8 clients) |
| Repeats | three, each repetition running both variants back to back (base then new) so drift hits both; the outbox was drained (no pending or running job) between every invocation |
| Caveat | Postgres is shared with the user's stack and runs in the Colima VM whose clock is about 2.2 s ahead of the host (not changed); both variants talk to the same server in the same session, so it affects both equally. Not a capacity claim |

**The workload is the worst case for the detectors.** Every run is one tool called over and over, so the loop and identical-request detectors hit their
caps (50 findings per detector, 200 per run: the worker log reports `findings: 50` for these runs). Real runs write far fewer finding rows.

## Results (three runs each; cells are `p50 / p95 / p99` ms; median of the three in bold)

| Case (worker running) | base c212058: three runs | base median | this branch: three runs | branch median |
| --- | --- | --- | --- | --- |
| 1 client, runs of 1,000 events | 31.2 / 46.3 / 53.0; 39.0 / 50.2 / 64.8; 39.9 / 51.6 / 59.2 | **39.0 / 50.2 / 59.2** | 37.8 / 50.1 / 60.9; 38.1 / 49.0 / 61.7; 38.2 / 49.5 / 64.8 | **38.1 / 49.5 / 61.7** |
| 4 clients | 87.3 / 143.6 / 398.3; 87.1 / 122.9 / 416.5; 93.4 / 129.1 / 412.6 | **87.3 / 129.1 / 412.6** | 88.5 / 148.4 / 319.7; 88.2 / 120.8 / 468.9; 93.7 / 129.0 / 406.2 | **88.5 / 129.0 / 406.2** |
| 8 clients | 173.4 / 222.3 / 284.2; 174.7 / 218.4 / 280.7; 180.7 / 224.4 / 252.0 | **174.7 / 222.3 / 280.7** | 170.9 / 218.9 / 284.9; 182.5 / 223.3 / 308.9; 183.6 / 232.6 / 295.9 | **182.5 / 223.3 / 295.9** |
| 1 client, one 30,000-event run | 37.5 / 626.8 / 3,044; 38.0 / 75.1 / 2,647; 42.8 / 698.5 / 3,270 | **38.0 / 626.8 / 3,044** | 36.9 / 60.1 / 1,958; 38.4 / 56.6 / 2,326; 42.3 / 74.7 / 2,228 | **38.4 / 60.1 / 2,228** |
| 4 clients (after the 30,000-event run) | 91.2 / 124.8 / 133.5; 92.5 / 117.9 / 134.6; 102.5 / 143.7 / 174.2 | **92.5 / 124.8 / 134.6** | 95.3 / 128.2 / 162.4; 91.6 / 117.1 / 137.4; 102.5 / 143.7 / 170.6 | **95.3 / 128.2 / 162.4** |
| 8 clients (after the 30,000-event run) | 174.8 / 209.7 / 287.9; 175.8 / 226.4 / 319.3; 187.5 / 235.2 / 323.3 | **175.8 / 226.4 / 319.3** | 182.5 / 217.3 / 281.3; 179.4 / 216.2 / 291.7; 177.2 / 234.4 / 249.8 | **179.4 / 217.3 / 281.3** |
| GET run (2,000 events) | 10.8 / 14.7 / 17.4; 5.1 / 7.4 / 10.4; 8.4 / 9.4 / 10.3 | **8.4 / 9.4 / 10.4** | 8.6 / 16.2 / 19.1; 5.4 / 6.3 / 6.5; 7.1 / 9.1 / 10.7 | **7.1 / 9.1 / 10.7** |
| GET events page (limit 500) | 33.1 / 49.0 / 53.8; 38.1 / 53.5 / 55.3; 36.9 / 54.2 / 58.2 | **36.9 / 53.5 / 55.3** | 34.0 / 50.3 / 53.0; 34.4 / 50.4 / 53.0; 31.6 / 49.6 / 55.4 | **34.0 / 50.3 / 53.0** |
| GET spans (limit 500) | 13.0 / 18.9 / 26.8; 12.6 / 24.5 / 29.5; 20.0 / 23.6 / 33.5 | **13.0 / 23.6 / 29.5** | 12.4 / 20.3 / 31.6; 13.8 / 19.3 / 31.4; 12.8 / 19.8 / 29.6 | **12.8 / 19.8 / 31.4** |
| **GET runs list (limit 50)** (badge query) | 6.8 / 8.2 / 8.8; 11.5 / 17.8 / 18.9; 11.0 / 16.6 / 17.8 | **11.0 / 16.6 / 17.8** | 10.0 / 15.2 / 15.8; 13.8 / 18.9 / 21.9; 10.0 / 21.0 / 24.1 | **10.0 / 18.9 / 21.9** |

Summary freshness (last ack until `summary_state` is `current`, includes up to a 500 ms poll and the 1 s debounce):

| | base: three runs | base median | branch: three runs | branch median |
| --- | --- | --- | --- | --- |
| 2,000 events | 1,140; 788; 1,056 ms | **1,056 ms** | 926; 770; 720 ms | **770 ms** |
| 10,000 events | 3,609; 3,491; 4,522 ms | **3,609 ms** | 3,548; 2,973; 3,605 ms | **3,548 ms** |

### The cost of this phase: the `detect_findings` job

Its own structured `duration_ms` (worker log line `findings computed`, branch only; the base has no detector). Each figure is the median over the jobs
that analysed a run of that exact size, with the minimum and maximum. The job runs after the summary, off the ingest path, once per delay window.

| Run size | Jobs | Median | Min to max |
| --- | --: | --: | --: |
| 1,000 events | 90 | **54.5 ms** | 49 to 80 ms |
| 2,000 events | 6 | **90 ms** | 89 to 146 ms |
| 10,000 events | 5 | **422 ms** | 410 to 570 ms |
| 30,000 events | 5 | **1,332 ms** | 1,299 to 1,463 ms |

The cost is linear, about 44 microseconds per event on this machine, and includes the capped event load, span derivation, every detector, the upserts of up to 50 finding rows per detector and the baseline query.

## Reading against the plan's budget

- **Realistic cases are within the noise.** One client with 1,000-event runs: p50 -2%, p95 -1%, p99 +4%. Four clients: p95 0.0%, p99 -2%. Eight clients: p95 +0.4%,
  p99 +5%. These are all inside the +-10% band and inside the spread of the three base runs (for example 4 clients p95 123 to 144 ms, p99 399 to 417 ms).
- **The 30,000-event run's p99 did not rise** (3,044 to 2,228 ms median; the base's own three runs spread 2,647 to 3,270 ms). The budget was +20%. The
  improvement is noise, not a gain (two of the base's three runs had a p95 near 0.65 s that the branch did not repeat); the supportable statement is "no
  regression". The detector runs in a separate job after the summary and does not hold the run row's lock.
- **Run listing with the badge query:** p95 median 16.6 to 18.9 ms (+14%, +2.3 ms absolute); the three base runs span 8.2 to 17.8 ms and the three branch
  runs 15.2 to 21.0 ms, so the ranges overlap, but this is the one place the data is consistent with a small real cost (the added `run_findings` lookup per page). It is a
  millisecond-scale difference far inside the 500 ms budget. Recorded as this phase's cost, not as noise.
- **Summary freshness did not move** (2,000 events 1.06 s to 0.77 s, 10,000 events 3.61 s to 3.55 s: inside the spread). The summarizer only gained one
  outbox enqueue.
- **Budget miss to record: the detect job for a 30,000-event run takes 1.33 s, above the plan's 1 s tuning trigger (AC-11).** It was not tuned away:
  the cost is linear in events, so lowering `ABB_FINDINGS_MAX_EVENTS` is the only lever, and the default of 50,000 would still allow about 2.2 s for the
  largest analysed run; the delay clamp changes how often the job runs, not how long it takes (a 30,000-event run is re-analysed at most every 15 s,
  so the worker spends at most about 9% of one run's time on it, against about 2.4 s for the summarizer per KI-016). Filed as KI-096 with a trigger instead of a silent pass. A cap of
  20,000 events would put the worst case near 0.9 s at the price of not analysing the tail of larger runs.
- **No job failed:** zero `ERROR` lines in either worker log.

## What was not measured

The Colima stack of Phase 2 and Phase 7 (two vCPU, API and worker in containers); gzip, TLS, several API processes; the findings route's own latency under
load (covered functionally by the test suite, one indexed read); a cold database; real-world run shapes (this workload saturates the finding caps, which is
the worst case for writes, not the typical case). A rerun on the compose stack (`make up && make seed && make bench`, three times before and after) would
add the comparable-with-history numbers; it is listed in KI-096 as the way to close the container half of AC-11.

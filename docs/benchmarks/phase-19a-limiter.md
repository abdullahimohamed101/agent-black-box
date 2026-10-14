# Phase 19a shared limiter: overhead on the ingestion path

Measured 2026-10-14 (code of the Phase 19a step 1 commit plus the window-edge fix described below). A **regression baseline for one laptop, not a
capacity claim**: re-measure on target hardware before quoting a figure (spec §158). Reproduce with
`cd apps/api && uv run python ../../scripts/bench_limiter.py --admin-url postgresql+asyncpg://USER:PASS@HOST:PORT/postgres --rounds 5 --json out.json`
(`--micro-only` times just the check). The script creates and drops its own database.

## Environment

| | |
| --- | --- |
| Host | Apple M5 Pro (15 cores, 24 GiB), macOS (Darwin 25.6), the developer machine, **shared with other running work**: load average 2 to 3 during the measured run (it was 15 during an earlier run that was discarded for noise) |
| Database | the Compose Postgres 16 (default config) in the Colima VM, reached over the loopback port; the API connects as the database owner |
| API | one `uvicorn` process of this checkout per configuration, started fresh for every measurement; no worker running |
| Workload | batches of 100 events (about 51 KiB JSON), HTTP keep-alive; 1 client (400 requests) and 4 clients (4 x 100 requests), run alternately `off`, `blocks`, `every-request`, **5 rounds**; the table shows the median of the five rounds |
| Limits | raised so nothing is throttled (5,000 events/s, burst 100,000 events, 100 MB/s): the cost of the check is measured, not its refusals. Shared window: 10 s, so 100,000 events and 1 GB per window |

Configurations: `off` = `ABB_LIMITS_SHARED_ENABLED=false` (the pre-19a in-memory limiter only); `blocks` = shared limiter at the default block divisor 20 (a block is
5,000 events, 50 batches); `every-request` = divisor 1000 (a block is 100 events, one batch: **a database write on every request, the worst case**).

## Results: end to end (ingestion ack latency)

| Clients | Config | p50 ms | p95 ms | p99 ms | req/s | counter writes per 1,000 requests |
| --: | --- | --: | --: | --: | --: | --: |
| 1 | off | 48.5 | 67.1 | 75.3 | 20.5 | 0 |
| 1 | blocks (default) | 48.3 | 65.0 | 74.6 | 20.7 | 27.5 |
| 1 | every-request | 52.5 | 72.8 | 81.3 | 19.2 | 1,040 |
| 4 | off | 97.6 | 134.8 | 147.0 | 39.0 | 0 |
| 4 | blocks (default) | 98.1 | 134.2 | 148.9 | 38.5 | 25 |
| 4 | every-request | 106.1 | 146.3 | 157.6 | 34.8 | 1,020 |

Zero non-202 responses in all 30 measurements. The five p95 values of each cell overlap between `off` and `blocks` (for example 4 clients: 131 to 140 ms against
129 to 149 ms), so **the default configuration is indistinguishable from no shared limiter at this noise level**. `every-request` is slower by about 4 ms at p50 and
5 to 11 ms at p95 and costs about 8% of throughput on a path that does 20 to 40 requests per second here (the HTTP stack, validation and the event insert dominate).

## Results: the check on its own

`GuardedLimiter.acquire` for a 100-event, 51.5 KB request, 2,000 sequential calls per configuration, in process against the same database (no HTTP):

| Config | p50 ms | p95 ms | p99 ms | mean ms | database calls per 2,000 requests |
| --- | --: | --: | --: | --: | --: |
| off (local bucket only) | 0.005 | 0.019 | 0.024 | 0.007 | 0 |
| blocks (default) | 0.005 | 0.029 | 4.6 | 0.121 | 51 (about 1 per 40 requests) |
| every-request | 3.0 | 5.7 | 8.5 | 3.2 | 2,232 (a call per request, two for the events and the bytes counter at block edges) |

Reading it: 97.5% of requests spend a block's credit without touching the database (microseconds); the 2.5% that reserve a new block pay one database round trip
(about 3 ms: a `set_config` for the statement timeout and the upsert, two statements). That round trip is the p99.

## Stated overhead bound and the Redis trigger

* **Default configuration:** the shared check adds **under 0.1 ms to the p95 and about 0.12 ms on average** to an ingestion request, and **about 27 counter writes per 1,000
  requests** (one per 5,000 events reserved; 27 writes a second at 1,000 requests a second of 100-event batches, against a 500 a second trigger).
* **Worst case (a write on every request):** about 3 ms p50 and 5.7 ms p95 added, about 1,040 writes per 1,000 requests.
* Writes are bounded by construction regardless of request rate: a project at its limit causes about 21 reservations per 10 s window per process, so about
  `2.1 x P x N` writes a second for P limited projects across N processes.

The ADR-152 triggers to move the counters off PostgreSQL are therefore: p95 added above **5 ms** (default config; 10 ms with a write per request), sustained counter writes above
**500 a second**, or more than **100,000 live rows** / dead tuples above 20% of the table. None is near: 0.03 ms, 27 a second at 1,000 req/s, and a handful of live rows.

## Honest notes

* A first run of this benchmark found a real bug, now fixed and covered by `tests/limits/test_guard.py::test_a_grant_from_a_window_about_to_end_still_admits_the_request_that_asked`:
  the request that triggered a reservation in the last milliseconds of a window was refused although the database had granted it (its credit had already expired when
  it was spent). It showed as one 429 per run in the `blocks` and `every-request` configurations.
* The measurement host was shared; an earlier run at load average 15 had a multi-hundred-millisecond outlier in the third round and was discarded and repeated.
* One uvicorn process and one database: the cross-process correctness is proved by `tests/limits/test_multi_process.py`, not by this benchmark, which measures cost only.
* Numbers do not include the PostgreSQL write-ahead-log or vacuum cost of the counter table at sustained load. Not measured, and worth a soak before a deployment near the trigger rate.

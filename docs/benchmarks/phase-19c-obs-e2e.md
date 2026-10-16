# Phase 19c step 6: what metrics and tracing cost an ingestion batch

Measured 2026-10-16 with `scripts/obs-e2e.sh` (`make obs-e2e`, gate 7) on the code of the Phase 19c step 6 commit. **Informational: the script prints these numbers and writes them to a file, it never asserts a latency.** What it asserts is deterministic:
every measured request in every cell was a `202` (zero 5xx, zero refusals), and the events each variant stored equal the events it was sent (plus the warm-up). A regression baseline for one laptop, not a capacity claim (spec §158): re-measure on target
hardware before quoting a figure.

## Environment

| | |
| --- | --- |
| Host | Apple M5 Pro (15 cores), macOS (Darwin 25.6), the developer machine, **shared with other running work**: 1-minute load average 3.8 to 4.5 at the start of the five rounds (3.8 before the benchmark, 5.2 after) |
| Database | PostgreSQL 16 (the Compose container in the Colima VM, host port 5433), a throwaway database; the API processes connect as a throwaway login role that is a member of `abb_runtime` |
| Processes | three `uvicorn` processes of this checkout over the same database, started fresh for the gate; the worker and the other gates' processes were not running during the benchmark rounds |
| Load generator | the same machine: stdlib asyncio and httpx in one Python process (it competes with the API for CPU; stated, not corrected for) |
| Workload | batches of 100 events (about 51 KiB), key authentication, HTTP keep-alive; **1 client x 60 batches** and **4 clients x 100 batches** per cell; rate limits and quotas raised so nothing is refused; `ABB_LOG_LEVEL=WARNING` so logging is not part of the comparison |
| Rounds | 5, the order of the three variants alternating every round (default, otel-off, on / on, otel-off, default / ...), after a warm-up cell per process |

## The variants

| Variant | What runs |
| --- | --- |
| `default` | the default install: no OpenTelemetry in the virtualenv, no metrics token (so no `/metrics` route), nobody scraping |
| `otel-off` | the `otel` virtualenv with tracing **off** and no token: the cost of merely having the extra installed |
| `on` | the `otel` virtualenv with tracing **on** at sampling 1.0 (every request traced, exported over OTLP/HTTP to a local receiver), a metrics token, and `/metrics` scraped every second |

The metrics registry counts in every variant: it has no off switch by design, because the counters are the SLI (ADR-165). "Metrics off" here means no endpoint and no scraper; the per-request cost of the counters themselves is in `phase-19c-metrics.md`.

## Result

| Variant | Clients | p50 ms | p95 ms (range over rounds) | mean ms |
| --- | --: | --: | --: | --: |
| default | 1 | 37.2 | 60.0 (59-63) | 39.3 |
| otel-off | 1 | 37.5 | 60.9 (60-62) | 39.2 |
| on | 1 | 37.6 | 62.9 (52-64) | 39.6 |
| default | 4 | 105.7 | 137.8 (135-159) | 110.8 |
| otel-off | 4 | 104.1 | 138.5 (135-142) | 109.9 |
| on | 4 | 105.3 | 144.0 (136-160) | 112.0 |

Reading it, honestly. With one client (no queueing, so the number is what one request pays) everything on added **0.4 ms to the p50 and 2.9 ms to the p95**, with the extra merely installed adding 0.3 ms and 0.9 ms; the p95 differences are inside the spread of the rounds
(52-64 ms for `on`, 59-63 ms for `default`), so they are not distinguishable from the machine's noise. With four closed-loop clients one process is saturated and latency is mostly queueing: the medians are within 1.6 ms of each other, and the p95 of `on` is 6.2 ms above
`default` inside a spread of 24 ms (136-160). Every variant's ack p95 stayed far under the spec's 150 ms budget at one client (60 to 63 ms). The scraper (81 scrapes over the run) did not fail once: every scrape was a 200. Each variant stored exactly the events it was sent (82,000 = 80,000 measured + 2,000 warm-up) and answered 800 of 800 batches with 202.

**Bound stated for review (not asserted by the script, because a wall-clock threshold is exactly what made CI flaky in 19a):** with one client, metrics and tracing at full sampling add no more than 5 ms to the ingestion p50 and keep the ack p95 under the 150 ms budget. Observed: +0.4 ms and 63 ms.

Reproduce: `OBS_E2E_STAGES=7 OBS_E2E_ROUNDS=7 OBS_E2E_BENCH_JSON=/tmp/bench.json make obs-e2e` (needs Postgres on 5433 and `uv`; no Docker for this gate).

# Phase 19a step 3: what project quotas cost on the ingestion path (ADR-154)

Measured 2026-10-14 on the final code of the quota commit. A **regression baseline for one laptop, not a capacity claim**: re-measure on target
hardware before quoting a figure. Reproduce with
`cd apps/api && uv run python ../../scripts/bench_quotas.py --admin-url postgresql+asyncpg://USER:PASS@HOST:PORT/postgres --rounds 3 --seconds 12 --json out.json`
(the script creates and drops its own database, starts the API from this checkout and alternates `ABB_QUOTAS_ENABLED=false` and the default, three rounds).

## Environment

| | |
| --- | --- |
| Host | Apple M5 Pro (15 cores, 24 GiB), macOS (Darwin 25.6), the developer machine, **shared with other running work**: load average about 2.6 during the run |
| Database | PostgreSQL 16.15 in Docker on the same host (published port, so every statement is a round trip of roughly 0.8 ms; a production network is as slow or slower, which makes the number of statements the thing to read) |
| API | one uvicorn process, one worker, default settings except the rate limits raised so they never refuse |
| Load | the generator runs on the same machine. Batches are `scripts/bench_ingest.py::make_events` (mostly tool calls, 10 percent LLM calls with small payloads), always new events and a new run per client. 3 rounds of 12 s per case and configuration, alternating off and on |

## Results (p50 / p95 of the batch request, three rounds, off then on)

| case | quotas off | quotas on | change |
| --- | --- | --- | --- |
| 1 client, batches of 100 | 48.1 to 48.5 ms / 64 to 69 ms; 2,050 to 2,090 events/s | 51.7 to 52.8 ms / 70 to 71 ms; 1,900 to 1,920 events/s | **+3.7 ms p50 (+8 %)**, +4 ms p95, -8 % events/s |
| 4 clients, batches of 100 (one project) | 97.1 to 97.9 ms / 130 to 134 ms; 3,860 to 3,910 events/s | 131.7 to 134.4 ms / 158 to 166 ms; 2,900 to 2,970 events/s | +35 ms p50 (+36 %), +28 ms p95, **-24 % events/s** |
| 8 clients, batches of 10 (one project) | 33.7 to 34.2 ms / 52 to 53 ms; 2,210 to 2,280 events/s | 51.3 to 55.6 ms / 72 to 80 ms; 1,380 to 1,490 events/s | +19 ms p50 (+57 %), +20 ms p95, **-36 % events/s** |
| replay of 100 stored events | 37.6 to 43.0 ms / 58 to 62 ms | **23.7 to 25.2 ms** / 29 to 31 ms | 40 % faster (duplicates are found by one read; no inserts are attempted) |

No request in any case answered anything but `202`.

## Reading it

* **One exporter** (the common case: an SDK sends from one process) pays about **3.7 ms per batch**: four extra statements on the hot path
  (`set_config('lock_timeout')`, the project row lock, one combined read of the quota state, the day's counters, which of the batch's events, runs and
  agents already exist, and the counter upsert), partly offset by skipping the agent and run lookups the read already answered. The first version
  paid 12 ms; folding four lookups into the one read (`ANY(array)` and a `UNION ALL`, one round trip) and skipping the agent
  lookup for known agents removed two thirds of it.
* **Several exporters of one project are serialised** by the project lock (ADR-154 point 5, KI-178): a batch holds it from the read to the commit,
  which is most of the transaction (about 35 ms for 100 events here, about 8 ms for 10). That is the whole of the 4-client loss: throughput of one
  project on this machine is capped near 2,900 events/s for 100-event batches and near 1,400 events/s for 10-event batches, down from about 3,900 and 2,250.
  The default per-project ingestion rate is 2,000 events/s sustained (burst 10,000): a project sending 100-event batches stays under the lock's ceiling
  (the ceiling is a bit above the default rate), a project sending 10-event batches from many exporters at the full default rate is at it.
  A project that needs more raises its batch size (the SDK default is larger than 10), or an operator turns quotas off (`ABB_QUOTAS_ENABLED=false`) for it; sharding the
  counters is the stated follow-up if a measured workload needs it.
* **Other projects are not affected**: the lock is per project row (tested: a held lock for one project does not delay another).
* **Replays are cheaper than before**: a batch of events that are all already stored is answered from the read, without attempting the insert.
* Day-boundary and cap behaviour were not benchmarked (a refused event costs nothing beyond the read; the refusals are in the tests).

## What was measured and what was not

The ingestion path only (batch POST through the API to commit). The usage endpoint and the settings page read three small tables and were not benchmarked. Replay was measured with
one client. A second API process against the same project was not measured; it queues on the same row lock exactly like a second client does (tested for correctness, not for latency).

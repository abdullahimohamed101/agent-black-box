# Phase 2 ingestion and read benchmark

Measured 2026-10-07 on the code of commit `e23b075`. These
numbers are a **regression baseline for this repository on one laptop**, not a capacity claim. Re-measure
on target hardware before quoting any figure externally (spec §158).

## Environment

| | |
| --- | --- |
| Host | Apple M5 Pro (15 cores, 24 GiB), macOS |
| Container runtime | Colima VM: **2 vCPU, 4 GiB**, Docker 29.5 |
| Stack | `make up`: Postgres 16 (default config, `shared_buffers` 128 MB), API (one uvicorn process), worker (one process), all in the same 2-vCPU VM; client on the host |
| Rate limiter | raised (`ABB_RATE_LIMIT_EVENTS_PER_SECOND=1000000`) so the pipeline, not the throttle, is measured; default is 2,000 events/s per project |
| Workload | batches of 100 events, ~50 KiB JSON (515 bytes/event); 90% `tool.call.completed`, 10% `llm.request.completed` with a ~330-byte inline payload; every event in its own span; HTTP keep-alive, no gzip |
| Script | `scripts/bench_ingest.py` (`make bench`); latency is client-side wall time per request |

## Results

Targets (spec §61.2, §156): batch ingest ack p50 < 50 ms and p95 < 150 ms; run-detail query p95 < 500 ms.

| Case | n | p50 | p95 | p99 | max | throughput |
| --- | --: | --: | --: | --: | --: | --: |
| **Ingest, 1 client, sequential, runs of 1,000 events, worker running, debounce 1 s** | 300 | **27.5 ms** | **43.7 ms** | 60.0 ms | 67.4 ms | 3,249 ev/s |
| Ingest, 1 client, sequential, worker stopped | 300 | 26.9 ms | 36.7 ms | 44.8 ms | 60.5 ms | 3,480 ev/s |
| Ingest, 4 clients, worker stopped | 148 | 94.4 ms | 126.6 ms | 131.2 ms | 137.4 ms | 4,080 ev/s |
| Ingest, 8 clients, worker stopped | 144 | 185.7 ms | 226.4 ms | 279.3 ms | 299.3 ms | 4,148 ev/s |
| Ingest, 4 clients, worker running | 148 | 103.8 ms | 145.3 ms | 182.5 ms | 205.2 ms | 3,588 ev/s |
| Ingest, 8 clients, worker running | 144 | 207.9 ms | 269.6 ms | 330.9 ms | 334.1 ms | 3,755 ev/s |
| Ingest, 1 client, **one 30,000-event run**, worker running, debounce 1 s | 300 | 26.8 ms | 53.4 ms | **1,683 ms** | **2,618 ms** | 1,321 ev/s |
| GET run (2,000 events) | 100 | 2.4 ms | 2.8 ms | 3.3 ms | 4.9 ms | |
| GET events page (limit 500) | 100 | 26.3 ms | 42.9 ms | 57.9 ms | 61.5 ms | |
| GET spans (limit 500) | 100 | 9.0 ms | 9.8 ms | 24.7 ms | 25.1 ms | |
| GET runs list (limit 50) | 100 | 4.0 ms | 4.6 ms | 10.1 ms | 19.5 ms | |

Summary freshness (last ack until `summary_state` is `current`, includes up to 500 ms worker poll and
the 1 s debounce): 2,000-event run **1.2 s**; 10,000-event run **2.5 s**.

## What the numbers say

- A single SDK exporter meets both ingest targets with room to spare (p50 27 ms, p95 44 ms) on this hardware.
- At 4 clients p95 is 126-145 ms (inside the target). At 8 clients p95 is 226-270 ms: the single API
  process is CPU-bound (about 4,000 events/s, roughly four times the Stage A peak of 1,000 events/s in spec §61.4)
  and the latency is queueing, not slower handling. Scaling out API processes is the lever (not measured here).
- Reads are far inside their budgets (run detail p95 2.8 ms against 500 ms; spans and event pages under 50 ms).
- **The tail is the summarizer on a huge run.** Recomputing one run that grows to 30,000 events competes with the
  API for the VM's two CPUs; a handful of requests stall for 1.7-2.6 s (p99). The same workload with realistic run
  sizes (1,000 events) shows no tail (p99 60 ms), and with the worker stopped there is none either. Full recomputation
  is O(events in run) per job (plan risk R2). It is bounded by debouncing (at most one recomputation per run per
  second) and is acceptable for realistic runs; it is **not** acceptable for runs of tens of thousands of events that are
  still receiving events. Trigger for incremental summarization: any run exceeding ~10,000 events while active, or p99
  ingest above 500 ms in production. Recorded as KI-016.

## Defects this benchmark found (all fixed, with regression tests)

1. Runs with more than ~2,500 spans failed to summarize: the span upsert exceeded asyncpg's 32,767 bind-parameter
   limit, so the job retried and was dead-lettered while the API still reported `summary_state: current`
   (the 2,000-event case sat just under the limit). Fixed by chunking; `summary_state` can now be `failed`.
2. Job scheduling compared the database clock (`available_at`) with the host clock, so fresh jobs could be invisible
   for a few milliseconds (a flaky test, and a real hazard across hosts). Scheduling now uses the database clock only.
3. Before debouncing, sequential ingest p95 was 258-315 ms because every batch triggered a full recomputation.

## Reproducing

```bash
export ABB_RATE_LIMIT_EVENTS_PER_SECOND=1000000 ABB_RATE_LIMIT_BURST_EVENTS=1000000
make up && make seed && make bench
# worker-stopped control, and realistic run sizes:
docker compose --profile app stop worker
cd apps/api && uv run python ../../scripts/bench_ingest.py --key-file ../../.local/dev-api-key --ingest-only
uv run python ../../scripts/bench_ingest.py --key-file ../../.local/dev-api-key --ingest-only --run-events 1000
```

Not measured: gzip, TLS, multiple API processes, a cold database, Postgres tuning, network latency, retention.

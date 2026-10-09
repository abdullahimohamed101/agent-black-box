# Ingest regression check after Phase 7 (KI-054)

Measured 2026-10-09 on `main` at the Phase 6 merge (Phases 7 and 8 included), with the same script, workload, container
setup and raised rate limit as `phase-2-ingestion.md` (Colima VM 2 vCPU / 4 GiB, `make up`, `make bench`). Same caveat: a regression
baseline for one laptop, not a capacity claim. Run-to-run noise is visible (4 clients, worker running, p95: 150.3, 150.6, 172.0 ms).

| Case (worker running) | Phase 2 p50 / p95 / p99 (ms) | Now p50 / p95 / p99 (ms) |
| --- | --- | --- |
| 1 client, runs of 1,000 events | 27.5 / 43.7 / 60.0 | 27.8 / 44.6 / 53.7 |
| 4 clients | 103.8 / 145.3 / 182.5 | 105.4 / 150.6 / 207.9 (other runs: 102.7 / 150.3 / 185.0; 109.2 / 172.0 / 198.0) |
| 8 clients | 207.9 / 269.6 / 330.9 | 212.6 / 268.9 / 295.1 (other runs: 229.5 / 324.2 / 356.1; 224.6 / 330.0 / 399.8) |
| 1 client, one 30,000-event run | 26.8 / 53.4 / **1,683** | 27.1 to 28.3 / 51.6 to 62.2 / **2,409 to 2,456** |
| GET run (2,000 events) | 2.4 / 2.8 / 3.3 | 5.6 / 13.5 / 22.3 |
| GET events page (limit 500) | 26.3 / 42.9 / 57.9 | 27.0 / 50.3 / 59.3 |
| Summary freshness, 2,000 events | 1.2 s | 1.2 s |
| Summary freshness, 10,000 events | 2.5 s | 3.2 s |

## Reading

- **No regression for realistic workloads.** One client with 1,000-event runs is unchanged; contended cases are within the noise.
- **The summarizer got more expensive on huge runs.** The 30,000-event run's p99 rose about 40% (1.7 s to 2.4 s) and freshness for a 10,000-event
  run about 28%: each summarization now also writes cost lines and typed run columns and queues an analytics refresh (Phase 7). Realistic runs do not show it.
  This is the KI-016 tail (full recomputation per summary), already above its 500 ms trigger for runs of tens of thousands of events; it is the case
  for incremental summarization (Phase 18 or sooner), not a new defect.
- Run detail is slower in relative terms (2.8 to 13.5 ms p95) and still far inside the 500 ms budget.
- One 2,000-event run was current only 11.1 s after its last ack in the first full run (a cold start behind the queued work of the previous 30,000-event runs).
  Not reproduced in isolation; not investigated further.

Not measured: gzip, TLS, multiple API processes, analytics refresh cost in isolation, a cold database.

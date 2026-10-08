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

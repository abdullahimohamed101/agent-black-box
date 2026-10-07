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
| Policy service down | per capability | configured fail-open/closed | emit outage event | explicit message |

## Limits and budgets
Event 256 KB; batch 5 MB uncompressed; SDK defaults batch 100 / 250 ms / queue 10,000 / timeout 2 s.
Targets (not promises until measured): ingest ack p50 <50 ms, p95 <150 ms; live propagation p95 <1 s;
run detail p95 <500 ms; dashboard p95 <1.5 s. Measured results are recorded in `docs/benchmarks/`.

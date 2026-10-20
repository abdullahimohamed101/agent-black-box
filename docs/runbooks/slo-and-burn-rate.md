# Runbook: the ingestion SLO and its burn-rate alerts

Applies to the alerts `AbbIngestErrorBudgetBurnFast`, `AbbIngestErrorBudgetBurnSustained`, `AbbIngestErrorBudgetBurnSlow`,
`AbbIngestErrorBudgetBurnBaseline` and `AbbIngestAckSlow` (`deploy/observability/prometheus/rules/slo.yml`). Dashboard row: **Overview and SLO**, then
**Ingestion**. Decision record: ADR-171.

## The indicator and its limits

Spec 117: 99.95% of valid authenticated event batches are durably accepted over a rolling 30 days. The assets measure it as

```text
SLI = committed / (committed + error)     from increase(ingest_acks_total{outcome=...})
```

- `committed`: the request was answered 2xx after its transaction committed.
- `error`: the request failed on our side (5xx, an unexpected exception, a failed commit, a database that could not be reached).
- `rejected`: a 4xx. The client's fault, so it is **left out of the denominator**.

What that leaves out, and what to watch instead:

| Not counted against the objective | Why | Where to see it |
| --- | --- | --- |
| Rate-limited batches (`429`) | The client is within the rules but over its rate; the SDK retries | `telemetry_events_rejected_total{reason="rate_limited"}`, dashboard Ingestion |
| Quota refusals | A project reached a quota we set | `quota_refusals_total`, `telemetry_events_rejected_total{reason="quota_exceeded"}` |
| Invalid, oversized, schema-unsupported batches | The client sent something we may refuse | `telemetry_events_rejected_total` by `reason` |
| Failed authentication | Never reaches the ingestion handler | `http_requests_total{status="401"}`, `auth_throttle_blocks_total` |
| Requests that never arrive (DNS, TLS, load balancer down) | Invisible to a metric the server exports | A probe from outside on `/readyz` |
| Per-event refusals inside an accepted `202` | The batch committed | `telemetry_events_rejected_total` |

The consequence to remember: **a limit or quota set too low refuses real customers without spending any error budget**. The alert
`AbbIngestionRejectionsHigh` exists for that case; see [ingestion rejections](ingestion-rejections.md).

## Error budget arithmetic

Target 99.95% leaves a budget of 1 - 0.9995 = 0.0005 (0.05%) of requests over 30 days. A burn rate of N means the error ratio is N x 0.0005 and the
budget would be gone in 30 days / N.

| Alert | Burn | Threshold (error ratio) | Windows (long AND short) | Budget spent in the long window | Severity |
| --- | --- | --- | --- | --- | --- |
| `AbbIngestErrorBudgetBurnFast` | 14.4 | 0.0072 | 1 h and 5 min | 2% | page |
| `AbbIngestErrorBudgetBurnSustained` | 6 | 0.0030 | 6 h and 30 min | 5% | page |
| `AbbIngestErrorBudgetBurnSlow` | 3 | 0.0015 | 1 d and 2 h | 10% | ticket |
| `AbbIngestErrorBudgetBurnBaseline` | 1 | 0.0005 | 3 d and 6 h | 10% | ticket |

Both windows must exceed the threshold: the long one shows the burn is sustained, the short one that it is still happening (the alert clears soon after a fix).
`tests/obs/test_observability_assets.py` recomputes the thresholds from the target.

## Fast burn (page)

More than 0.72% of ingestion requests have failed on our side for an hour and are failing now. Customers' SDKs are buffering or dropping.

1. Dashboard **Ingestion**: outcomes panel. Is `error` high, and is it all of the traffic (the API is down) or a share (one route, one process)?
2. `http_requests_total{status=~"5.."}` by `route`: which route? Then the API log filtered to `status >= 500` (fixed `error_type`, frames; never messages).
3. Dependencies: dashboard **Database and dependencies**. A breaker at 2 (`db_breaker_state`) or pool waits means [database saturation](database-saturation.md).
4. Recent deploy? Roll back first, investigate second. The artifact directory full or not writable fails ingestion only when artifacts are involved (`artifact_upload_failures_total`).
5. When it stops, write what spent the budget in the incident record; the budget remaining is on the dashboard.

## Sustained burn (page)

The same signal at 0.3% over 6 hours. Slower and easier to miss: usually one failing dependency path (a route that 500s for one project's payload shape, one of several API
replicas with a bad database connection). Compare replicas (`sum by (instance)`) and routes before suspecting the database.

## Slow burn (ticket)

0.15% over a day. Find the cause this week: look at the top routes and `error_type` values in the log for the last day. Intermittent causes: a statement timeout under a daily batch job,
retention or backup overlapping peak ingestion (run them off-peak), a slow disk under artifacts.

## Baseline burn (ticket)

0.05% over three days: the whole budget being spent on one steady cause. Per the error-budget policy (spec 117), avoid shipping risky changes until it recovers.

## Latency (`AbbIngestAckSlow`)

Not part of the durability objective. Fewer than 99% of ingestion requests were answered within 1 second. Usual causes: pool wait (`postgres_pool_wait_seconds`), a very large run being
summarised (see [worker lag](worker-lag.md) step 3), a host short of CPU. Details in [database saturation](database-saturation.md#slow-ingestion-answers).

## Tuning

Everything is in the rule files; there is no other place. To change the target edit `abb:ingest_slo:target_ratio` and the four thresholds together (the test fails if they disagree). On an
installation with very low traffic a few failed requests are a large ratio: add `for:` or a minimum request rate before paging on it. The latency alert's 1 second and 99% are defaults;
set them from your own measurements.

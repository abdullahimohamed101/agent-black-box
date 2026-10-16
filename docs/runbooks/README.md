# Runbooks

Start from the symptom or the alert name. The alerts are defined in `deploy/observability/prometheus/rules/` (every alert's `runbook_url` points into this directory; a test keeps them
honest) and the dashboard rows are in `deploy/observability/grafana/dashboards/agent-black-box.json` (`docs/OPERATIONS.md`, "Local dashboards").

## Symptom to runbook

| Symptom | Runbook | Alerts | Dashboard row |
| --- | --- | --- | --- |
| Ingestion is failing on our side; the error budget is burning | [SLO and burn rate](slo-and-burn-rate.md) | `AbbIngestErrorBudgetBurnFast`, `AbbIngestErrorBudgetBurnSustained`, `AbbIngestErrorBudgetBurnSlow`, `AbbIngestErrorBudgetBurnBaseline` | Overview and SLO |
| Ingestion answers slowly | [SLO and burn rate](slo-and-burn-rate.md), [database saturation](database-saturation.md#slow-ingestion-answers) | `AbbIngestAckSlow` | Ingestion |
| SDK users lose events; many 4xx on `/v1/events*` | [Ingestion rejections](ingestion-rejections.md) | `AbbIngestionRejectionsHigh`, `AbbArtifactUploadsFailing`, `AbbQuotaRefusals` | Ingestion |
| Runs stay `processing`; summaries missing | [Worker lag](worker-lag.md) | `AbbWorkerLagHigh`, `AbbWorkerLagSevere`, `AbbProcessorFailures` | Worker and jobs |
| Runs show `summary_state: failed` | [Dead-lettered jobs](dead-letters.md) | `AbbWorkerJobsDeadLettered` | Worker and jobs |
| `503 DEPENDENCY_UNAVAILABLE`, API not ready, database trouble | [Database saturation](database-saturation.md) | `AbbDatabaseBreakerOpen`, `AbbDatabaseBreakerTripping`, `AbbPoolWaitHigh`, `AbbReadinessCheckFailing`, `AbbReadinessFlapping`, `AbbHttpServerErrors` | Database and dependencies |
| Queue, dead-letter or retention gauges look frozen | [Database saturation](database-saturation.md#scrape-database-down) | `AbbScrapeDatabaseDown` | Database and dependencies |
| Run pages stop updating, `STREAM_LIMIT` | [Live streams](stream-issues.md) | `AbbStreamDeliveryLag`, `AbbStreamConnectionsNearLimit` | Alerts and streams |
| Notifications are not arriving; dead notifications | [Alerting](alerting.md) | `AbbAlertDeliveriesFailing`, `AbbAlertDeadLetters` | Alerts and streams |
| `429` for rate, throttle or quota; `503 BUSY`; fail-open limiter | [Abuse resistance and hardening](hardening.md) | `AbbLimiterFailingOpen`, `AbbLimiterDatabaseErrors`, `AbbAuthThrottleBlocks`, `AbbSessionThrottleBlocks`, `AbbBodyCapRefusals` | Controls |
| `SEARCH_BUSY` / `SEARCH_TIMEOUT` | [Search](search.md#searches-fail-with-503) | `AbbSearchBusy` | Controls |
| Data kept longer than policy; retention failed | [Retention](retention.md) | `AbbRetentionFailing`, `AbbRetentionStale` | Retention and backups |
| Backup is old or failing | [Backup and restore](backup-restore.md#monitoring) | `AbbBackupTooOld`, `AbbBackupVeryOld`, `AbbBackupNeverSucceeded`, `AbbBackupRunFailed` | Retention and backups |
| Alert-key rotation failed | [Secrets](secrets.md#monitoring-rotation) | `AbbRotationRunFailed` | Retention and backups |
| Database role too privileged | [Security operations](security-operations.md#privileged-database-role) | `AbbPrivilegedDatabaseRole` | Database and dependencies |
| Suspicious keys, roles, approvals, detections | [Security operations](security-operations.md) | none yet (KI-242) | Controls |
| Metrics or targets missing | [below](#metrics-are-missing-or-stale) | `AbbTargetDown` | Overview and SLO |
| A process restarts again and again | [below](#a-process-keeps-restarting) | `AbbProcessRestartLoop` | Database and dependencies |
| Traces are missing | [below](#traces-are-missing) | `AbbOtelExportFailing` | Alerts and streams |

Other runbooks: [sign-in, roles and access](auth-and-access.md), [policies and approvals](policy-and-approvals.md), [security observability](security-observability.md),
[reliability findings](findings.md), [evaluations](evaluations.md), [migrations](migrations.md), [analytics migrations](analytics-migrations.md), [secrets](secrets.md), [bumping pins by hand](bumping-pins.md).

## Metrics are missing or stale

1. `up{job=~"abb-.*"}` in Prometheus (Status, Targets). `AbbTargetDown` fires after 3 minutes.
2. `401` on the target: the token file Prometheus reads differs from `ABB_METRICS_TOKEN` (rotate both together). `404`: the API has no token set, so there is no `/metrics` route. `429`: the scrape bucket was spent by something else that can reach the port; keep the port private.
3. The worker serves metrics only with `ABB_WORKER_METRICS_PORT` set, and inside a container network with `ABB_WORKER_METRICS_BIND=0.0.0.0`.
4. Series from backups, key rotation and retention come from the node_exporter textfile directory, not from a scrape of ABB; check the file's age and that node_exporter reads that directory.
5. `abb_scrape_database_up` at 0: [database saturation](database-saturation.md#scrape-database-down).

## A process keeps restarting

`AbbProcessRestartLoop`: `process_start_time_seconds` changed three times in half an hour. Read the last lines of the process log before the restart (fixed `error_type` and frames; a configuration
error is a fixed message at start), and the container exit code (137 is an out-of-memory kill: look at `body_bytes_in_flight` and the memory limit). A crash loop right after a deploy: roll back. Counters
restart at zero on every restart, so rates under-report while it lasts.

## Traces are missing

Tracing is off unless `ABB_OTEL_ENABLED=1`. With it on, `AbbOtelExportFailing` counts failed export attempts (`otel_export_failures_total`): the collector is down, slow, or rejecting the token in
`OTEL_EXPORTER_OTLP_HEADERS`. Requests and jobs are never affected by a failing collector; spans beyond the queue are dropped. Check the endpoint from inside the container network and the collector's log.

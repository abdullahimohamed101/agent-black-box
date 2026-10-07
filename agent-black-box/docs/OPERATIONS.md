# Operations

Operating Agent Black Box itself (spec §43, §104-125). Stub until Phase 19; runbooks accumulate in
`docs/runbooks/`. Minimum set: ingestion rejection spike, worker lag, database saturation,
live-stream lag (spec §125), backup/restore drill (§123).

Environments: local (Compose), preview, staging, production. Config hierarchy: code defaults <
environment < workspace/project settings; secrets never in source; validate at startup, fail fast.
Self-observability metrics: `http_requests_total`, `telemetry_events_received_total`,
`telemetry_events_rejected_total`, `worker_job_lag_seconds`, `sse_connections`, `postgres_pool_wait_seconds`.
Recovery targets: RPO <=15 min (control-plane metadata), RTO <=4 h.

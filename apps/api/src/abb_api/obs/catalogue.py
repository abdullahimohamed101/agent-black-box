"""The metric catalogue: every metric the product exposes, declared in one place (ADR-165).

Step 1 of Phase 19c declares the whole catalogue so the later instrumentation steps only
increment. Names follow the Prometheus conventions (snake_case, ``_total`` on counters, base units
of seconds and bytes). No label is ever a tenant, user, key, address or path: workspace and
project detail stays in logs and the app (plan non-goals). Closed label sets that mirror an enum
elsewhere in the code are literals here (obs must not import the modules it instruments, or
imports would cycle) and a test pins each literal to its source enum.
"""

import time
from dataclasses import dataclass

from abb_api import __version__
from abb_api.obs.metrics import (
    BYTES_BUCKETS,
    LATENCY_BUCKETS,
    Counter,
    Gauge,
    Histogram,
    Registry,
    capped,
    closed,
)

HTTP_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
# Every status code an HTTP response can carry; a number outside it becomes "other".
HTTP_STATUSES = tuple(str(code) for code in range(100, 600))
# The route label for a request that matched no route (404 for a scanner's path).
UNMATCHED = "unmatched"

# Mirrors of enums elsewhere; tests/obs pins each to its source.
LIMITER_SCOPES = (
    "ingest_events",
    "ingest_bytes",
    "artifact_count",
    "artifact_bytes",
    "login_start",
    "session_write",
    "policy_write",
    "policy_validate",
    "policy_dry_run",
    "approval_decide_key",
    "approval_decide_workspace",
    "alert_write",
    "alert_preview",
    "alert_test",
    "retention_write",
    "retention_preview",
    "auth_fail_address",
    "auth_fail_credential",
    "auth_fail_global",
    "auth_throttle_audit",
    "session_fail_address",
    "session_fail_global",
)
RETENTION_TIERS = ("artifacts", "trace", "run", "audit", "operational", "platform")
QUOTAS = ("agents", "runs_per_day", "events_per_day", "artifact_bytes")
# Why telemetry was refused. A request-level refusal counts once; a per-event refusal inside an
# accepted (202) batch counts once per event. Authentication failures never reach the handler (the
# credential check runs first): they are in http_requests_total and auth_throttle_blocks_total.
INGEST_REJECT_REASONS = (
    "invalid",
    "too_large",
    "rate_limited",
    "quota_exceeded",
    "unauthorized",
    "unavailable",
    "redaction_failed",
    "reserved",
    "conflict",
    "schema_unsupported",
)
# `ingest_acks_total`: committed = answered 2xx after the commit; rejected = the client's fault
# (4xx); error = our fault (5xx or an unexpected exception). Accepted-event durability is
# committed / (committed + error).
INGEST_ACK_OUTCOMES = ("committed", "rejected", "error")
ARTIFACT_FAILURE_REASONS = (
    "invalid",
    "too_large",
    "rate_limited",
    "quota_exceeded",
    "conflict",
    "store_unavailable",
    "unavailable",
)
JOB_OUTCOMES = ("succeeded", "retried", "dead_letter", "lease_lost", "superseded")
ALERT_STATUSES = ("pending", "delivered", "failed", "dead", "skipped")
# The fixed vocabulary of alerts/outbound.py error codes and the skip reasons of the schema; "none"
# for a delivery that carried neither.
ALERT_REASONS = (
    "none",
    "timeout",
    "connect_failed",
    "dns_failed",
    "tls_failed",
    "blocked_address",
    "blocked_port",
    "http_3xx",
    "http_4xx",
    "http_5xx",
    "bad_response",
    "smtp_failed",
    "config_unreadable",
    "internal_error",
    "cooldown",
    "destination_paused",
    "destination_deleted",
    "rule_deleted",
    "rate_limited",
    "daily_cap",
)
ALERT_DESTINATION_STATES = ("active", "paused", "failing")
SEARCH_BUSY_REASONS = ("actor_limit", "workspace_share", "process_full")
BODY_CAP_REASONS = ("budget", "ceiling", "timeout")
# Reachable ones only: a session ends passively when it expires, and the CLI is another process.
SESSION_REVOCATION_REASONS = ("logout", "revoke_one", "revoke_others")
CALL_OUTCOMES = ("ok", "error")
BACKUP_OUTCOMES = ("succeeded", "failed")
THROTTLE_KINDS = ("address", "credential", "global")
SESSION_THROTTLE_KINDS = ("address", "global")
# The checks /readyz can fail on (health/readiness.py); the ones it only reports are not gauges.
READINESS_CHECKS = ("database", "schema", "artifacts", "role")
# Mirrors db/role_check.REASONS; tests pin it.
ROLE_REASONS = ("superuser", "bypass_rls", "events_rewritable", "events_owner", "schema_create")
RETENTION_MODES = ("off", "dry_run", "enforce")
RETENTION_DELETED_KINDS = ("runs", "events", "rows", "bytes")
# Fixed failure codes of a retention tick (retention/metrics.py maps TickResult onto them).
RETENTION_FAILURE_CODES = (
    "measure_error",
    "step_failed",
    "refused",
    "budget_exhausted",
    "tick_crashed",
)

# A job type is a handler name; there are a handful, and a cap protects against a bad value.
JOB_TYPE_CAP = 32


@dataclass(frozen=True)
class Catalogue:
    # process
    process_start_time_seconds: Gauge
    process_uptime_seconds: Gauge
    abb_build_info: Gauge
    # http
    http_requests_total: Counter
    http_request_duration_seconds: Histogram
    http_requests_in_flight: Gauge
    # telemetry intake and its SLIs (spec 116.1, 117)
    telemetry_events_received_total: Counter
    telemetry_events_rejected_total: Counter
    telemetry_bytes_received_total: Counter
    ingest_acks_total: Counter
    ingest_ack_duration_seconds: Histogram
    # worker
    worker_jobs_pending: Gauge
    worker_job_lag_seconds: Gauge
    worker_jobs_total: Counter
    worker_job_duration_seconds: Histogram
    processor_failures_total: Counter
    # streaming
    sse_connections: Gauge
    sse_delivery_lag_seconds: Histogram
    # dependencies
    postgres_pool_wait_seconds: Histogram
    artifact_upload_failures_total: Counter
    artifact_upload_bytes: Histogram
    # database safety: breaker, readiness, role re-check (Phase 19c step 3, ADR-169)
    db_breaker_state: Gauge
    db_breaker_trips_total: Counter
    readiness_check: Gauge
    readiness_flaps_total: Counter
    db_role_privileged: Gauge
    # optional tracing (obs/tracing_otel.py)
    otel_export_failures_total: Counter
    # scrape-time database sampling (obs/scrape.py)
    abb_scrape_database_up: Gauge
    # controls: limiter and throttle
    limiter_fail_open_total: Counter
    limiter_database_calls_total: Counter
    auth_throttle_blocks_total: Counter
    auth_active_keys: Gauge
    auth_active_key_refresh_failures_total: Counter
    session_throttle_blocks_total: Counter
    session_active_handles: Gauge
    session_active_refresh_failures_total: Counter
    # quotas, body cap
    quota_refusals_total: Counter
    body_cap_refusals_total: Counter
    body_bytes_in_flight: Gauge
    # retention
    retention_passes_total: Counter
    retention_deleted_total: Counter
    retention_failures_total: Counter
    retention_dry_run_candidates: Gauge
    retention_mode: Gauge
    retention_seconds_since_last_pass: Gauge
    # backups and key rotation (written to a textfile by their own processes)
    backup_last_success_timestamp_seconds: Gauge
    backup_last_duration_seconds: Gauge
    backup_bundle_bytes: Gauge
    backup_runs_total: Counter
    rotation_last_success_timestamp_seconds: Gauge
    rotation_runs_total: Counter
    # alerts
    alert_deliveries_total: Counter
    alert_dead_letters: Gauge
    alert_destinations: Gauge
    # search, sessions, invitations
    search_busy_total: Counter
    search_permits_in_use: Gauge
    session_revocations_total: Counter
    invitation_only_refusals_total: Counter


def build_catalogue(registry: Registry, *, version: str = __version__) -> Catalogue:
    """Declare every metric on ``registry`` and seal it."""
    r = registry
    started_wall = time.time()
    started_mono = time.monotonic()

    start = r.gauge("process_start_time_seconds", "Start time of the process, Unix seconds.")
    start.set(started_wall)
    uptime = r.gauge("process_uptime_seconds", "Seconds since the process started.")
    uptime.set_function(lambda: time.monotonic() - started_mono)
    build = r.gauge(
        "abb_build_info", "Always 1; the version label carries the build.", [capped("version", 2)]
    )
    build.set(1, version)

    http_labels = [closed("method", *HTTP_METHODS), capped("route", 400)]
    cat = Catalogue(
        process_start_time_seconds=start,
        process_uptime_seconds=uptime,
        abb_build_info=build,
        http_requests_total=r.counter(
            "http_requests_total",
            "HTTP requests by method, matched route template and status code.",
            [*http_labels, closed("status", *HTTP_STATUSES)],
            max_series=5000,
        ),
        http_request_duration_seconds=r.histogram(
            "http_request_duration_seconds",
            "HTTP request duration (until the response is complete) by method and route.",
            http_labels,
            max_series=3000,
        ),
        http_requests_in_flight=r.gauge(
            "http_requests_in_flight", "HTTP requests being served right now."
        ),
        telemetry_events_received_total=r.counter(
            "telemetry_events_received_total", "Telemetry events accepted for storage."
        ),
        telemetry_events_rejected_total=r.counter(
            "telemetry_events_rejected_total",
            "Telemetry events or batches refused, by reason.",
            [closed("reason", *INGEST_REJECT_REASONS)],
        ),
        telemetry_bytes_received_total=r.counter(
            "telemetry_bytes_received_total", "Request body bytes of accepted telemetry batches."
        ),
        ingest_acks_total=r.counter(
            "ingest_acks_total",
            "Ingestion requests by outcome; committed is counted after the commit.",
            [closed("outcome", *INGEST_ACK_OUTCOMES)],
        ),
        ingest_ack_duration_seconds=r.histogram(
            "ingest_ack_duration_seconds",
            "Time from the start of an ingestion request to its answer, once per request.",
        ),
        worker_jobs_pending=r.gauge(
            "worker_jobs_pending",
            "Due jobs waiting to run, by job type (sampled at scrape time).",
            [capped("type", JOB_TYPE_CAP)],
        ),
        worker_job_lag_seconds=r.gauge(
            "worker_job_lag_seconds",
            "Age of the oldest due job that has not started, by job type (sampled at scrape time).",
            [capped("type", JOB_TYPE_CAP)],
        ),
        worker_jobs_total=r.counter(
            "worker_jobs_total",
            "Jobs finished by type and outcome.",
            [capped("type", JOB_TYPE_CAP), closed("outcome", *JOB_OUTCOMES)],
        ),
        worker_job_duration_seconds=r.histogram(
            "worker_job_duration_seconds",
            "Time a handler ran, by job type.",
            [capped("type", JOB_TYPE_CAP)],
        ),
        processor_failures_total=r.counter(
            "processor_failures_total",
            "Handler failures by job type (retried or dead-lettered).",
            [capped("type", JOB_TYPE_CAP)],
        ),
        sse_connections=r.gauge("sse_connections", "Open live-stream (SSE) connections."),
        sse_delivery_lag_seconds=r.histogram(
            "sse_delivery_lag_seconds", "Time from an event being stored to its stream delivery."
        ),
        postgres_pool_wait_seconds=r.histogram(
            "postgres_pool_wait_seconds", "Time spent waiting for a database connection."
        ),
        artifact_upload_failures_total=r.counter(
            "artifact_upload_failures_total",
            "Artifact uploads that were not stored, by reason.",
            [closed("reason", *ARTIFACT_FAILURE_REASONS)],
        ),
        artifact_upload_bytes=r.histogram(
            "artifact_upload_bytes", "Size of stored artifacts.", buckets=BYTES_BUCKETS
        ),
        limiter_fail_open_total=r.counter(
            "limiter_fail_open_total",
            "Shared limiter decisions made locally because the database was unavailable.",
            [closed("scope", *LIMITER_SCOPES)],
        ),
        limiter_database_calls_total=r.counter(
            "limiter_database_calls_total",
            "Calls the shared limiter made to the database.",
            [closed("outcome", *CALL_OUTCOMES)],
        ),
        auth_throttle_blocks_total=r.counter(
            "auth_throttle_blocks_total",
            "Requests refused by the failed-authentication throttle.",
            [closed("kind", *THROTTLE_KINDS)],
        ),
        auth_active_keys=r.gauge(
            "auth_active_keys", "Size of the active API key id set the throttle consults."
        ),
        auth_active_key_refresh_failures_total=r.counter(
            "auth_active_key_refresh_failures_total",
            "Refreshes of the active API key id set that failed (the old set keeps serving).",
        ),
        session_throttle_blocks_total=r.counter(
            "session_throttle_blocks_total",
            "Requests refused (429) by the failed-session throttle, by the scope that was closed.",
            [closed("kind", *SESSION_THROTTLE_KINDS)],
        ),
        session_active_handles=r.gauge(
            "session_active_handles",
            "Size of the active session hint set the session throttle consults.",
        ),
        session_active_refresh_failures_total=r.counter(
            "session_active_refresh_failures_total",
            "Refreshes of the active session hint set that failed (the old set keeps serving).",
        ),
        db_breaker_state=r.gauge(
            "db_breaker_state",
            "Database circuit breaker in this process: 0 closed, 1 half-open (probing), 2 open.",
        ),
        db_breaker_trips_total=r.counter(
            "db_breaker_trips_total", "Times the database circuit breaker opened."
        ),
        readiness_check=r.gauge(
            "readiness_check",
            "1 when the named /readyz check passes, 0 when it fails (set when /readyz evaluates).",
            [closed("check", *READINESS_CHECKS)],
        ),
        readiness_flaps_total=r.counter(
            "readiness_flaps_total", "Changes of the overall /readyz answer between ready and not."
        ),
        db_role_privileged=r.gauge(
            "db_role_privileged",
            "1 per reason the connected database role is too privileged (periodic re-check).",
            [closed("reason", *ROLE_REASONS)],
        ),
        otel_export_failures_total=r.counter(
            "otel_export_failures_total",
            "Trace export attempts that failed (a collector that is down never affects requests).",
        ),
        abb_scrape_database_up=r.gauge(
            "abb_scrape_database_up",
            "1 when the last scrape-time database sample succeeded, 0 when its values are cached.",
        ),
        quota_refusals_total=r.counter(
            "quota_refusals_total",
            "Writes refused because a project quota was reached.",
            [closed("quota", *QUOTAS)],
        ),
        body_cap_refusals_total=r.counter(
            "body_cap_refusals_total",
            "Requests refused by the in-flight body byte budget or the body ceiling.",
            [closed("reason", *BODY_CAP_REASONS)],
        ),
        body_bytes_in_flight=r.gauge(
            "body_bytes_in_flight", "Request body bytes currently held in memory."
        ),
        retention_passes_total=r.counter(
            "retention_passes_total",
            "Retention ticks finished, by the mode they ran in.",
            [closed("mode", *RETENTION_MODES)],
        ),
        retention_deleted_total=r.counter(
            "retention_deleted_total",
            "Data removed by retention, by tier and kind (bytes are artifact file bytes).",
            [closed("tier", *RETENTION_TIERS), closed("kind", *RETENTION_DELETED_KINDS)],
        ),
        retention_failures_total=r.counter(
            "retention_failures_total",
            "Retention failures by fixed code (units: tiers, runs or ticks, see the runbook).",
            [closed("code", *RETENTION_FAILURE_CODES)],
        ),
        retention_dry_run_candidates=r.gauge(
            "retention_dry_run_candidates",
            "What a dry-run tick found it would delete (runs for run tiers, rows otherwise).",
            [closed("tier", *RETENTION_TIERS)],
        ),
        retention_mode=r.gauge(
            "retention_mode",
            "1 for the mode of the newest retention report (the effective mode), else 0.",
            [closed("mode", *RETENTION_MODES)],
        ),
        retention_seconds_since_last_pass=r.gauge(
            "retention_seconds_since_last_pass",
            "Seconds since the newest retention report was written; NaN when there is none.",
        ),
        backup_last_success_timestamp_seconds=r.gauge(
            "backup_last_success_timestamp_seconds",
            "Unix time of the newest successful backup known to the textfile; 0 if unknown.",
        ),
        backup_last_duration_seconds=r.gauge(
            "backup_last_duration_seconds", "Duration of the last successful backup."
        ),
        backup_bundle_bytes=r.gauge(
            "backup_bundle_bytes", "Total size of the last successful backup bundle."
        ),
        backup_runs_total=r.counter(
            "backup_runs_total",
            "Backup runs observed, by outcome.",
            [closed("outcome", *BACKUP_OUTCOMES)],
        ),
        rotation_last_success_timestamp_seconds=r.gauge(
            "rotation_last_success_timestamp_seconds",
            "Unix time of the newest successful alert-key rotation; 0 if unknown.",
        ),
        rotation_runs_total=r.counter(
            "rotation_runs_total",
            "Alert-key rotation runs observed, by outcome.",
            [closed("outcome", *BACKUP_OUTCOMES)],
        ),
        alert_deliveries_total=r.counter(
            "alert_deliveries_total",
            "Alert notification delivery results by status and fixed error or skip reason.",
            [closed("status", *ALERT_STATUSES), closed("reason", *ALERT_REASONS)],
        ),
        alert_dead_letters=r.gauge(
            "alert_dead_letters",
            "Alert notifications that exhausted their retries (sampled at scrape time).",
        ),
        alert_destinations=r.gauge(
            "alert_destinations",
            "Alert destinations by state (sampled at scrape time; never their names).",
            [closed("state", *ALERT_DESTINATION_STATES)],
        ),
        search_busy_total=r.counter(
            "search_busy_total",
            "Searches refused with SEARCH_BUSY, by reason.",
            [closed("reason", *SEARCH_BUSY_REASONS)],
        ),
        search_permits_in_use=r.gauge(
            "search_permits_in_use", "Search permits held by running searches in this process."
        ),
        session_revocations_total=r.counter(
            "session_revocations_total",
            "Sign-in sessions ended, by reason.",
            [closed("reason", *SESSION_REVOCATION_REASONS)],
        ),
        invitation_only_refusals_total=r.counter(
            "invitation_only_refusals_total",
            "Sign-ins refused because the installation is invitation-only.",
        ),
    )
    r.seal()
    return cat


@dataclass(frozen=True)
class Observability:
    """A registry with its catalogue; the app takes one so tests can run on an isolated pair."""

    registry: Registry
    catalogue: Catalogue


def build_observability(*, version: str = __version__) -> Observability:
    registry = Registry()
    return Observability(registry, build_catalogue(registry, version=version))


# The process-wide pair every module increments. Tests build isolated ones.
DEFAULT = build_observability()
REGISTRY = DEFAULT.registry
METRICS = DEFAULT.catalogue

__all__ = [
    "DEFAULT",
    "LATENCY_BUCKETS",
    "METRICS",
    "REGISTRY",
    "UNMATCHED",
    "Catalogue",
    "Observability",
    "build_catalogue",
    "build_observability",
]

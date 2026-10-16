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
)
RETENTION_TIERS = ("artifacts", "trace", "run", "audit", "operational", "platform")
QUOTAS = ("agents", "runs_per_day", "events_per_day", "artifact_bytes")
INGEST_REJECT_REASONS = (
    "invalid",
    "too_large",
    "rate_limited",
    "quota_exceeded",
    "unauthorized",
    "busy",
    "unavailable",
)
JOB_OUTCOMES = ("succeeded", "retried", "dead_letter", "lease_lost")
ALERT_STATUSES = ("pending", "delivered", "failed", "dead", "skipped")
SEARCH_BUSY_REASONS = ("actor_limit", "workspace_share", "process_full")
BODY_CAP_REASONS = ("budget", "ceiling", "timeout")
SESSION_REVOCATION_REASONS = ("logout", "revoke_one", "revoke_others", "member_removed", "expired")
INGEST_ACK_OUTCOMES = ("success", "failure")
CALL_OUTCOMES = ("ok", "error")
BACKUP_OUTCOMES = ("succeeded", "failed")
THROTTLE_KINDS = ("address", "credential", "global")

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
    # controls: limiter and throttle
    limiter_fail_open_total: Counter
    limiter_database_calls_total: Counter
    auth_throttle_blocks_total: Counter
    auth_active_keys: Gauge
    # quotas, body cap
    quota_refusals_total: Counter
    body_cap_refusals_total: Counter
    body_bytes_in_flight: Gauge
    # retention
    retention_batches_total: Counter
    retention_deleted_rows_total: Counter
    retention_failures_total: Counter
    retention_dry_run_candidates: Gauge
    # backups
    backup_last_success_timestamp_seconds: Gauge
    backup_runs_total: Counter
    # alerts
    alert_deliveries_total: Counter
    alert_dead_letters: Gauge
    # search, sessions, invitations
    search_busy_total: Counter
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
            "Ingestion acknowledgements (a success is counted after the commit).",
            [closed("outcome", *INGEST_ACK_OUTCOMES)],
        ),
        ingest_ack_duration_seconds=r.histogram(
            "ingest_ack_duration_seconds", "Time from ingestion request to acknowledgement."
        ),
        worker_jobs_pending=r.gauge(
            "worker_jobs_pending",
            "Due jobs waiting to run, by job type (sampled at scrape time).",
            [capped("type", JOB_TYPE_CAP)],
        ),
        worker_job_lag_seconds=r.gauge(
            "worker_job_lag_seconds", "Age of the oldest due job that has not started."
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
            "artifact_upload_failures_total", "Artifact uploads that failed after being accepted."
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
        retention_batches_total=r.counter(
            "retention_batches_total",
            "Retention delete batches committed.",
            [closed("tier", *RETENTION_TIERS)],
        ),
        retention_deleted_rows_total=r.counter(
            "retention_deleted_rows_total",
            "Rows removed by retention.",
            [closed("tier", *RETENTION_TIERS)],
        ),
        retention_failures_total=r.counter(
            "retention_failures_total",
            "Retention failures (a failed run or batch).",
            [closed("tier", *RETENTION_TIERS)],
        ),
        retention_dry_run_candidates=r.gauge(
            "retention_dry_run_candidates",
            "Rows a dry-run tick found it would delete (last tick).",
            [closed("tier", *RETENTION_TIERS)],
        ),
        backup_last_success_timestamp_seconds=r.gauge(
            "backup_last_success_timestamp_seconds",
            "Unix time of the newest successful backup known to this process; 0 if unknown.",
        ),
        backup_runs_total=r.counter(
            "backup_runs_total",
            "Backup runs observed, by outcome.",
            [closed("outcome", *BACKUP_OUTCOMES)],
        ),
        alert_deliveries_total=r.counter(
            "alert_deliveries_total",
            "Alert notification delivery results by status.",
            [closed("status", *ALERT_STATUSES)],
        ),
        alert_dead_letters=r.gauge(
            "alert_dead_letters", "Alert notifications that exhausted their retries."
        ),
        search_busy_total=r.counter(
            "search_busy_total",
            "Searches refused with SEARCH_BUSY, by reason.",
            [closed("reason", *SEARCH_BUSY_REASONS)],
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

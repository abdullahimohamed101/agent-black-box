"""Typed settings, validated once at startup (spec §108: fail fast)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Retention (Phase 19b, ADR-159). "forever" keeps a tier for good; a number is days after the run
# ended (the audit log: after the entry). The floor protects against a typo that would delete
# everything on the next tick; the ceiling is 100 years.
RETENTION_FLOOR_DAYS = 7
RETENTION_CEILING_DAYS = 36500
RetentionDays = int | Literal["forever"]


class RetentionSettings(BaseSettings):
    """The retention knobs both the API (policy defaults, preview) and the retention job read.

    Deliberately holds no database URL: the credentials of the retention role live only in
    `abb_api.retention.runner.RetentionRunnerSettings`, which the API process never loads.
    """

    model_config = SettingsConfigDict(
        env_prefix="ABB_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Defaults for a workspace that has not chosen its own.
    retention_artifacts_days: RetentionDays = 30
    retention_trace_days: RetentionDays = 90
    retention_run_days: RetentionDays = 365
    retention_audit_days: RetentionDays = 365
    # off: the job does nothing. dry_run (default): it measures and reports, deleting nothing.
    # enforce: it deletes, permanently (ADR-160): take a backup first.
    retention_mode: Literal["off", "dry_run", "enforce"] = "dry_run"
    # Bounds of one tick for one workspace: at most this many runs and this many other rows per
    # tier are considered, and the statement timeout applies to every query of the tick.
    retention_max_runs_per_tick: int = Field(default=2000, ge=1, le=1_000_000)
    retention_max_rows_per_tick: int = Field(default=100_000, ge=1, le=100_000_000)
    retention_statement_timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    # Enforcement deletes this many rows per transaction, and gives a run up after this many
    # failed attempts (until an operator runs `retry-failed`).
    retention_batch_size: int = Field(default=2000, ge=1, le=100_000)
    retention_max_attempts: int = Field(default=5, ge=1, le=100)
    # The preview endpoint is cheaper and tighter than a tick (a person is waiting).
    retention_preview_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    retention_preview_max_runs: int = Field(default=5000, ge=1, le=100_000)
    # A shortened period takes effect after this many hours; until then the longer one applies
    # (ADR-159 amendment). 0: at once.
    retention_grace_hours: int = Field(default=24, ge=0, le=720)
    # The audit log is the record of who changed the policy, so it has its own, higher minimum
    # (the other tiers keep RETENTION_FLOOR_DAYS).
    retention_audit_floor_days: int = Field(default=90, ge=RETENTION_FLOOR_DAYS, le=36500)

    @model_validator(mode="after")
    def _retention_defaults_are_a_valid_policy(self) -> "RetentionSettings":
        days = {
            "artifacts": self.retention_artifacts_days,
            "trace": self.retention_trace_days,
            "run": self.retention_run_days,
            "audit": self.retention_audit_days,
        }
        for tier, value in days.items():
            if isinstance(value, int) and not (
                RETENTION_FLOOR_DAYS <= value <= RETENTION_CEILING_DAYS
            ):
                raise ValueError(
                    f"retention_{tier}_days must be 'forever' or "
                    f"{RETENTION_FLOOR_DAYS}-{RETENTION_CEILING_DAYS} days"
                )
        audit = self.retention_audit_days
        if isinstance(audit, int) and audit < self.retention_audit_floor_days:
            raise ValueError("retention_audit_days must not be below retention_audit_floor_days")
        # A run record is deleted last, so it can never be kept for less time than the trace
        # detail, nor the trace detail for less than the artifacts (ADR-159).
        for inner, outer in (("artifacts", "trace"), ("trace", "run")):
            a, b = days[inner], days[outer]
            if b != "forever" and (a == "forever" or a > b):
                raise ValueError(f"retention_{inner}_days must not exceed retention_{outer}_days")
        return self


class Settings(RetentionSettings):
    """All configuration comes from ABB_* environment variables (or a local .env)."""

    model_config = SettingsConfigDict(
        env_prefix="ABB_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    environment: Literal["development", "test", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Required, no default: a missing database URL must stop the process, not guess.
    database_url: str = Field(min_length=1)
    cors_origins: str = "http://localhost:3000"

    # Ingestion limits (spec §71.4). Per-event size comes from the event contract itself.
    ingest_max_body_bytes: int = Field(default=5 * 1024 * 1024, ge=1024)
    ingest_max_batch_events: int = Field(default=1000, ge=1)
    # Request bodies held in memory at once by this process (Phase 19a, ADR-155, KI-026): a request
    # whose body would pass this is answered 503 BUSY with Retry-After. It must hold the largest
    # single body (validated below). A body not fully received within the read timeout is dropped.
    max_body_bytes_in_flight: int = Field(default=64 * 1024 * 1024, ge=1024 * 1024)
    body_read_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    # Artifacts (ADR-030): local filesystem store, per-artifact size cap.
    artifact_dir: str = ".artifacts"
    artifact_max_bytes: int = Field(default=8 * 1024 * 1024, ge=1024)
    # Per-project quotas (Phase 19a, ADR-154). These are the defaults; an owner or admin may
    # override each one for a project. A project at a quota has the excess refused loudly
    # (`QUOTA_EXCEEDED`); what it already stored keeps working. `quotas_enabled=false` is an
    # emergency switch (and the benchmark's baseline): nothing is checked or counted while off.
    quotas_enabled: bool = True
    quota_agents: int = Field(default=500, ge=1, le=1_000_000)
    quota_runs_per_day: int = Field(default=50_000, ge=1, le=100_000_000)
    quota_events_per_day: int = Field(default=5_000_000, ge=1, le=100_000_000_000)
    quota_artifact_bytes: int = Field(default=5 * 1024**3, ge=1024, le=1024**5)
    # Counted data of one project is written one batch at a time (a per-project row lock); a batch
    # that cannot get it within this long is answered 503 and retried by the SDK.
    quota_lock_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    # Server-side secret scan (ADR-130). An event whose strings exceed the size or time budget has
    # the offending values dropped; an artifact beyond its budget has its body dropped. Fail closed.
    security_scan_event_max_seconds: float = Field(default=1.0, gt=0)
    security_scan_artifact_max_bytes: int = Field(default=4 * 1024 * 1024, ge=1024)
    security_scan_artifact_max_seconds: float = Field(default=10.0, gt=0)
    # A change to a project's muted detectors or destination allowlist re-analyses its newest runs
    # (ADR-131 amendment, KI-133): at most this many, started within this many days. 0 runs: off.
    security_reanalyse_max_runs: int = Field(default=200, ge=0, le=5000)
    security_reanalyse_max_age_days: int = Field(default=30, ge=1)
    # At most one re-analysis wave per project per this many seconds (0: no limit).
    security_reanalyse_cooldown_seconds: float = Field(default=60.0, ge=0)
    # Background worker (spec §72)
    # Delay before a run's summary job becomes due. A burst of batches for one run coalesces into
    # a single recomputation instead of one per batch (see docs/benchmarks/phase-2-ingestion.md).
    summary_debounce_seconds: float = Field(default=1.0, ge=0)
    worker_poll_interval_seconds: float = Field(default=0.5, gt=0)
    worker_batch_size: int = Field(default=10, ge=1)
    worker_lease_seconds: float = Field(default=60.0, gt=0)
    worker_max_attempts: int = Field(default=5, ge=1)
    worker_backoff_base_seconds: float = Field(default=5.0, ge=0)
    worker_backoff_max_seconds: float = Field(default=300.0, ge=0)

    # Per-project token buckets (spec §71.3, FR-ING-007). In-process only until Phase 19.
    rate_limit_events_per_second: float = Field(default=2000.0, gt=0)
    rate_limit_burst_events: int = Field(default=10000, ge=1)
    rate_limit_bytes_per_second: float = Field(default=10 * 1024 * 1024.0, gt=0)
    rate_limit_burst_bytes: int = Field(default=50 * 1024 * 1024, ge=1)

    # Analytics queries are cut off by the database after this long (ADR-041).
    analytics_timeout_seconds: float = Field(default=10.0, gt=0)

    # Search (ADR-100): one wall-clock budget per request, and a per-process cap on concurrent
    # searches (acquired before a connection, so ingestion is never starved). Per process (KI-082).
    search_timeout_seconds: float = Field(default=5.0, gt=0)
    search_max_concurrent: int = Field(default=4, ge=1, le=14)

    # Live streams (spec §76, ADR-022). Limits are per API process until Phase 19.
    stream_max_total: int = Field(default=50, ge=1)
    stream_max_per_key: int = Field(default=10, ge=1)
    # How often an open stream re-checks that its credential, session and role still hold (KI-033).
    stream_reauth_seconds: float = Field(default=30.0, gt=0)
    stream_max_lifetime_seconds: float = Field(default=900.0, gt=0)
    stream_fallback_poll_seconds: float = Field(default=2.0, gt=0)
    stream_keepalive_seconds: float = Field(default=15.0, gt=0)
    stream_write_timeout_seconds: float = Field(default=10.0, gt=0)
    stream_end_quiet_seconds: float = Field(default=5.0, ge=0)
    stream_overlap_seconds: float = Field(default=30.0, ge=0)
    stream_page_size: int = Field(default=200, ge=1, le=500)
    # A floor between one stream's polls, and how many stream queries may use the database at once.
    # The pool is shared with ingestion: streams queue here instead of starving it.
    stream_min_poll_seconds: float = Field(default=0.1, ge=0)
    stream_db_concurrency: int = Field(default=4, ge=1)
    # How often a stream counts its overlap window to catch a row that became visible late.
    stream_window_check_seconds: float = Field(default=2.0, ge=0)

    # Reliability findings (ADR-080/081). The detector job analyses at most `findings_max_events`
    # events of a run; thresholds are deployment-wide and every finding records the ones it used.
    findings_delay_seconds: float = Field(default=5.0, ge=0)
    findings_max_events: int = Field(default=50_000, ge=100)
    findings_loop_min_repetitions: int = Field(default=3, ge=2)
    findings_loop_min_repetitions_single: int = Field(default=8, ge=2)
    findings_identical_min_count: int = Field(default=3, ge=2)
    findings_retry_storm_min: int = Field(default=5, ge=1)
    findings_retry_burst_window_seconds: int = Field(default=60, ge=1)
    findings_context_growth_min_delta_tokens: int = Field(default=4_000, ge=1)
    findings_context_growth_factor: float = Field(default=2.0, gt=1)
    findings_context_growth_warning_tokens: int = Field(default=100_000, ge=1)
    findings_cascade_min_failures: int = Field(default=3, ge=2)
    findings_cascade_window_calls: int = Field(default=10, ge=2)
    findings_cost_spike_min_baseline: int = Field(default=20, ge=1)
    findings_cost_spike_window_days: int = Field(default=30, ge=1)

    # Evaluations (ADR-090/091) and run comparison (ADR-092). The evaluator timeout must leave the
    # lease heartbeat room to run (validated below); the event caps bound memory per job/request.
    eval_timeout_seconds: float = Field(default=25.0, gt=0)
    eval_max_events: int = Field(default=50_000, ge=100)
    eval_stale_seconds: float = Field(default=3600.0, gt=0)
    eval_batch_delay_seconds: float = Field(default=5.0, ge=0)
    compare_max_events: int = Field(default=20_000, ge=100)
    compare_max_steps: int = Field(default=2_000, ge=10)
    compare_max_edits: int = Field(default=600, ge=1)
    # "none": judge evaluators cannot be created. "fake": a deterministic scorer, plumbing only.
    judge_provider: Literal["none", "fake"] = "none"
    judge_allow_fake: bool = False
    judge_cooldown_seconds: float = Field(default=300.0, ge=0)

    # Sign-in for people (ADR-060). OIDC is off until `oidc_issuer` is set; keys work regardless.
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: SecretStr | None = None
    # Extra hosts the provider's endpoints may live on besides the issuer's (Google's token and
    # key endpoints are on googleapis.com). Comma-separated; empty by default.
    oidc_extra_hosts: str = ""
    # The browser-facing origin of the web app: derives the redirect URI and cookie attributes,
    # and is the only Origin a cookie-authenticated write may carry.
    web_origin: str | None = None
    session_absolute_hours: int = Field(default=168, ge=1)
    session_idle_hours: int = Field(default=24, ge=1)
    # A backstop on the unauthenticated login endpoints, shared by every client (the API cannot
    # tell clients apart behind the web relay; per-client limits live in the web app, D15).
    login_global_per_minute: int = Field(default=6000, ge=1)
    # Revoking sessions, per person (KI-068). Reading the list is not limited.
    session_writes_per_minute: int = Field(default=30, ge=1)
    # Shared limiter (Phase 19a, ADR-152). The budgets above and below are counted per window in
    # PostgreSQL so they hold across API processes; the in-memory limiter stays in front. Off: the
    # old per-process behaviour. The call is bounded by `limits_timeout_ms` and, when it fails,
    # skipped for `limits_backoff_seconds` (fail open to the local limiter).
    limits_shared_enabled: bool = True
    limits_timeout_ms: int = Field(default=500, ge=10, le=5000)
    limits_backoff_seconds: float = Field(default=5.0, ge=0, le=300)
    # Units are reserved from the database in blocks of limit / divisor (fewer writes, up to
    # N * (block - 1) early refusals across N processes); 1 means every request writes.
    limits_block_divisor: int = Field(default=20, ge=1, le=1000)
    # Failed API-key authentication (Phase 19a, ADR-153). Only failures are counted, in fixed
    # windows shared by every API process; exceeding any of the three answers 429 AUTH_THROTTLED.
    # A valid request never spends a budget. The address budget needs `trust_proxy`.
    auth_fail_enabled: bool = True
    auth_fail_window_seconds: int = Field(default=60, ge=1, le=3600)
    auth_fail_per_address: int = Field(default=30, ge=1)
    auth_fail_per_credential: int = Field(default=10, ge=1)
    auth_fail_global: int = Field(default=600, ge=1)
    # Keys seen invalid are refused from memory for this long (no database lookup); a key seen
    # valid is exempt from the throttle gate (never from authentication) for this long.
    auth_fail_negative_seconds: float = Field(default=30.0, ge=0, le=3600)
    auth_fail_known_valid_seconds: float = Field(default=300.0, ge=0, le=3600)
    auth_fail_cache_entries: int = Field(default=10_000, ge=10, le=1_000_000)
    # The ids of active keys, refreshed in the background: while an address or global window is
    # closed, a request for one of these ids still reaches full authentication (ADR-153 point 3),
    # so a key this process has not seen yet is not locked out by a flood. Above the cap the set
    # holds an arbitrary subset and the rest behave as before (logged once).
    auth_fail_active_keys_refresh_seconds: float = Field(default=60.0, ge=1, le=3600)
    auth_fail_active_keys_cap: int = Field(default=100_000, ge=10, le=10_000_000)
    # True only when a reverse proxy that you run sets `X-Forwarded-For` (its last entry is the
    # client) and is the only way to reach the API; otherwise the header is ignored.
    trust_proxy: bool = False
    # Window of the ingestion and artifact budgets, whose rates are per second (the minute
    # budgets use a 60 s window).
    limits_ingest_window_seconds: int = Field(default=10, ge=1, le=60)
    # Denied requests by one person that are written to the audit log per minute; the rest are
    # only logged (D11), so a refused client cannot fill the table.
    audit_denials_per_minute: int = Field(default=10, ge=1)
    # Retention policy changes and the heavier preview, per workspace per minute (ADR-159).
    retention_writes_per_minute: int = Field(default=20, ge=1)
    retention_previews_per_minute: int = Field(default=10, ge=1)
    # Policy management per workspace (Phase 14, ADR-140), per minute: publishes, validations (an
    # editor validates as people type) and the heavier dry runs. Per process, like ingestion.
    policy_writes_per_minute: int = Field(default=30, ge=1)
    policy_validations_per_minute: int = Field(default=120, ge=1)
    policy_dry_runs_per_minute: int = Field(default=20, ge=1)
    # Decisions and approvals (Phase 14 step 2, ADR-141). Decision-style calls of an agent's key
    # (decide, consume, cancel, wait start) are limited per key and per workspace, per minute;
    # long polls are capped per workspace and per process, and each poll holds no database
    # connection between reads. Per process, like ingestion (KI-141).
    policy_decide_per_minute_key: int = Field(default=1200, ge=1)
    policy_decide_per_minute_workspace: int = Field(default=6000, ge=1)
    approval_waiters_per_workspace: int = Field(default=20, ge=1)
    approval_waiters_per_process: int = Field(default=200, ge=1)
    approval_wait_poll_seconds: float = Field(default=1.0, gt=0)
    approval_max_body_bytes: int = Field(default=64 * 1024, ge=1024)
    # Requests waiting for a decision: at most this many per key and per workspace, so a runaway
    # agent cannot bury the approvers. The decision endpoint refuses more with a 429.
    approval_max_open_per_key: int = Field(default=100, ge=1)
    approval_max_open_per_workspace: int = Field(default=1000, ge=1)
    # Alerting (Phase 17, ADR-150). Limits keep a workspace's configuration and write rate bounded.
    alerts_max_rules_per_workspace: int = Field(default=100, ge=1)
    alerts_max_destinations_per_workspace: int = Field(default=50, ge=1)
    alerts_max_destinations_per_rule: int = Field(default=10, ge=1)
    alerts_writes_per_minute: int = Field(default=30, ge=1)
    # How often the scheduled checks (approval about to expire, daily spend) run per workspace.
    alerts_sweep_interval_seconds: float = Field(default=60.0, gt=0)
    # At most this many `skipped` history rows per rule and destination per cooldown window; past
    # it a suppressed notification is only counted in the log, so a flood cannot fill the table.
    alerts_skipped_per_window: int = Field(default=25, ge=0)
    alerts_preview_timeout_seconds: float = Field(default=5.0, gt=0)
    # An alert write (a destination or rule change) waits at most this long for a row lock that a
    # delivery tick holds, then fails with `409 ALERT_BUSY` instead of hanging (review P2-1).
    alerts_lock_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    # Delivery (ADR-151). Every loop, queue and timeout below is bounded. Off: notifications stay
    # `pending` (an operator hold; tests that do not exercise delivery use it).
    alerts_delivery_enabled: bool = True
    alerts_request_timeout_seconds: float = Field(default=5.0, gt=0, le=30)  # webhook/Slack total
    alerts_smtp_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    alerts_max_attempts: int = Field(default=5, ge=1, le=20)  # then the alert is `dead`
    alerts_backoff_base_seconds: float = Field(default=30.0, ge=0)
    alerts_backoff_max_seconds: float = Field(default=1800.0, ge=0)
    # Claimed per tick and workspace. The tick runs the batch one row after another inside one job,
    # so its wall time is at most batch x (request timeout, or SMTP timeout for email); five keeps
    # a tick near a minute even when every row times out, and the next tick follows at once.
    alerts_deliver_batch: int = Field(default=5, ge=1, le=200)
    # State of a destination: `failing` after this many consecutive failed attempts, `paused`
    # (reason `failures`) after the second number; one success resets it.
    alerts_failing_after: int = Field(default=3, ge=1)
    alerts_pause_after_failures: int = Field(default=10, ge=1)
    alerts_rate_per_minute: int = Field(default=20, ge=1)  # deliveries per destination
    alerts_daily_cap: int = Field(default=500, ge=1)  # deliveries per destination per 24 h
    alerts_tests_per_minute: int = Field(default=10, ge=1)  # "send test" per workspace
    # Delivery-log retention: terminal rows (delivered, dead, skipped, failed) older than this go.
    alerts_retention_days: int = Field(default=30, ge=1, le=3650)
    alerts_prune_interval_seconds: float = Field(default=6 * 3600.0, gt=0)
    alerts_prune_batch: int = Field(default=2000, ge=1)
    # TEST ONLY (scripts/alerts-e2e.sh): a JSON file that replaces DNS and the TLS trust store of
    # the alert sender so local receivers can stand in for public hosts (alerts/testnet.py). Unset
    # by default and refused outside development and test.
    alerts_test_network_file: Path | None = None
    # Ports a webhook or Slack destination may use (KI-162, ADR-158). Comma-separated; 443 is the
    # default and what an https URL without a port means. Extra ports are an operator decision.
    alerts_allowed_ports: str = "443"
    # Email delivery needs an operator-provided SMTP relay (no mail credentials in the database).
    # Without `smtp_host`, email destinations cannot be created. The password is read from the
    # environment only and is never logged or returned.
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_starttls: bool = True
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None
    # Lets the CLI mint sessions without an identity provider. Honoured only in development/test.
    allow_dev_sessions: bool = False
    # /docs, /redoc and /openapi.json (KI-023). Unset: served in development, test and staging,
    # not in production. true/false overrides the environment.
    api_docs_enabled: bool | None = None
    # The API and worker refuse to start in production when their database role can alter stored
    # events (superuser, owner of the tables, ...; KI-031). 1 accepts that knowingly; a log line
    # records it on every start.
    allow_privileged_db_role: bool = False

    @model_validator(mode="after")
    def _sign_in_is_configured_safely(self) -> "Settings":
        if self.oidc_issuer is None:
            return self
        if not self.oidc_client_id or not self.web_origin:
            raise ValueError("oidc_issuer needs oidc_client_id and web_origin")
        local = self.environment in ("development", "test")
        issuer, origin = urlsplit(self.oidc_issuer), urlsplit(self.web_origin)
        if issuer.scheme != "https" and not (issuer.scheme == "http" and local):
            raise ValueError("oidc_issuer must be https (http only in development or test)")
        if origin.scheme not in ("http", "https") or not origin.hostname:
            raise ValueError("web_origin must look like https://host[:port]")
        if origin.path not in ("", "/") or origin.query or origin.fragment or origin.username:
            raise ValueError("web_origin must be an origin: scheme, host and optional port only")
        if origin.scheme == "http" and not (
            local and origin.hostname in ("localhost", "127.0.0.1")
        ):
            raise ValueError("an http web_origin is only for localhost in development or test")
        return self

    @model_validator(mode="after")
    def _smtp_is_complete(self) -> "Settings":
        if self.smtp_host is not None and not self.smtp_host.strip():
            self.smtp_host = None  # an empty ABB_SMTP_HOST means "not configured"
        if self.smtp_host is not None and not self.smtp_from:
            raise ValueError("smtp_host needs smtp_from (the sender address)")
        if (self.smtp_username is None) != (self.smtp_password is None):
            raise ValueError("smtp_username and smtp_password go together")
        return self

    @property
    def smtp_configured(self) -> bool:
        return self.smtp_host is not None

    @property
    def web_origin_normalised(self) -> str | None:
        return self.web_origin.rstrip("/").lower() if self.web_origin else None

    @property
    def dev_sessions_enabled(self) -> bool:
        return self.allow_dev_sessions and self.environment in ("development", "test")

    @model_validator(mode="after")
    def _evaluators_leave_the_heartbeat_room(self) -> "Settings":
        # The handler awaits its evaluator, so the lease heartbeat runs; a timeout of half the
        # lease or more could let the lease lapse mid-evaluation (ADR-090).
        if "eval_timeout_seconds" not in self.model_fields_set:
            # Unset: the default shrinks with a short lease (tests, small deployments) instead of
            # refusing to start; an explicit value is validated as written.
            self.eval_timeout_seconds = min(
                self.eval_timeout_seconds, self.worker_lease_seconds * 0.4
            )
        if self.eval_timeout_seconds >= self.worker_lease_seconds / 2:
            raise ValueError("eval_timeout_seconds must be < worker_lease_seconds / 2")
        return self

    @model_validator(mode="after")
    def _a_fake_judge_never_judges_production(self) -> "Settings":
        local = self.environment in ("development", "test")
        if self.judge_provider == "fake" and not (local or self.judge_allow_fake):
            raise ValueError(
                "judge_provider=fake is only for development or test (or set judge_allow_fake)"
            )
        return self

    @model_validator(mode="after")
    def _alert_ports_are_a_valid_list(self) -> "Settings":
        _ = self.alerts_allowed_port_set  # raises ValueError (a settings error) when malformed
        return self

    @property
    def alerts_allowed_port_set(self) -> frozenset[int]:
        ports: set[int] = set()
        for item in self.alerts_allowed_ports.split(","):
            text = item.strip()
            if not text.isascii() or not text.isdigit() or not 1 <= int(text) <= 65535:
                raise ValueError("alerts_allowed_ports must be a comma list of ports 1-65535")
            ports.add(int(text))
        if not ports:
            raise ValueError("alerts_allowed_ports must list at least one port")
        return frozenset(ports)

    @model_validator(mode="after")
    def _the_test_network_never_runs_in_production(self) -> "Settings":
        if self.alerts_test_network_file is not None and self.environment not in (
            "development",
            "test",
        ):
            raise ValueError("alerts_test_network_file is only for development or test")
        return self

    @model_validator(mode="after")
    def _history_outlives_the_longest_cooldown(self) -> "Settings":
        # The cooldown looks at stored notifications; pruning them sooner than a cooldown can last
        # would end the cooldown early (review P3-4).
        longest_days = 7  # alerts.model.MAX_COOLDOWN_SECONDS
        if self.alerts_retention_days < longest_days:
            raise ValueError(
                f"alerts_retention_days must be >= {longest_days} (the longest rule cooldown)"
            )
        return self

    @model_validator(mode="after")
    def _failing_comes_before_paused(self) -> "Settings":
        if self.alerts_failing_after > self.alerts_pause_after_failures:
            raise ValueError("alerts_failing_after must be <= alerts_pause_after_failures")
        return self

    @model_validator(mode="after")
    def _cascade_window_holds_its_failures(self) -> "Settings":
        if self.findings_cascade_window_calls < self.findings_cascade_min_failures:
            raise ValueError(
                "findings_cascade_window_calls must be >= findings_cascade_min_failures"
            )
        return self

    @model_validator(mode="after")
    def _bursts_fit_a_full_batch(self) -> "Settings":
        # A bucket smaller than one maximal batch could never admit it: fail at startup instead.
        if self.rate_limit_burst_events < self.ingest_max_batch_events:
            raise ValueError("rate_limit_burst_events must be >= ingest_max_batch_events")
        if self.rate_limit_burst_bytes < self.ingest_max_body_bytes:
            raise ValueError("rate_limit_burst_bytes must be >= ingest_max_body_bytes")
        if self.rate_limit_burst_bytes < self.artifact_max_bytes:
            raise ValueError("rate_limit_burst_bytes must be >= artifact_max_bytes")
        return self

    @model_validator(mode="after")
    def _the_body_budget_holds_the_largest_body(self) -> "Settings":
        if self.max_body_bytes_in_flight < self.body_ceiling_bytes:
            raise ValueError(
                "max_body_bytes_in_flight must be >= the largest body "
                "(ingest_max_body_bytes, artifact_max_bytes)"
            )
        return self

    @property
    def body_ceiling_bytes(self) -> int:
        """No request body may be larger than this, whatever the route."""
        return max(self.ingest_max_body_bytes, self.artifact_max_bytes)

    @property
    def docs_enabled(self) -> bool:
        if self.api_docs_enabled is not None:
            return self.api_docs_enabled
        return self.environment != "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # required fields come from the environment

"""Typed settings, validated once at startup (spec §108: fail fast)."""

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
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
    # Artifacts (ADR-030): local filesystem store, per-artifact size cap.
    artifact_dir: str = ".artifacts"
    artifact_max_bytes: int = Field(default=8 * 1024 * 1024, ge=1024)
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

    # Live streams (spec §76, ADR-022). Limits are per API process until Phase 19.
    stream_max_total: int = Field(default=50, ge=1)
    stream_max_per_key: int = Field(default=10, ge=1)
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
    login_global_per_minute: int = Field(default=600, ge=1)
    # Lets the CLI mint sessions without an identity provider. Honoured only in development/test.
    allow_dev_sessions: bool = False

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

    @property
    def web_origin_normalised(self) -> str | None:
        return self.web_origin.rstrip("/").lower() if self.web_origin else None

    @property
    def dev_sessions_enabled(self) -> bool:
        return self.allow_dev_sessions and self.environment in ("development", "test")

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

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # required fields come from the environment

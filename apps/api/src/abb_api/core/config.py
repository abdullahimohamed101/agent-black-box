"""Typed settings, validated once at startup (spec §108: fail fast)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
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
    # Per-project token buckets (spec §71.3, FR-ING-007). In-process only until Phase 19.
    rate_limit_events_per_second: float = Field(default=2000.0, gt=0)
    rate_limit_burst_events: int = Field(default=10000, ge=1)
    rate_limit_bytes_per_second: float = Field(default=10 * 1024 * 1024.0, gt=0)
    rate_limit_burst_bytes: int = Field(default=50 * 1024 * 1024, ge=1)

    @model_validator(mode="after")
    def _bursts_fit_a_full_batch(self) -> "Settings":
        # A bucket smaller than one maximal batch could never admit it: fail at startup instead.
        if self.rate_limit_burst_events < self.ingest_max_batch_events:
            raise ValueError("rate_limit_burst_events must be >= ingest_max_batch_events")
        if self.rate_limit_burst_bytes < self.ingest_max_body_bytes:
            raise ValueError("rate_limit_burst_bytes must be >= ingest_max_body_bytes")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # required fields come from the environment

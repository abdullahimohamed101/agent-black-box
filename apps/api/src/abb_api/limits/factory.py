"""Builders that turn the existing settings into guarded limiters (ADR-152).

The settings names, limit values and local token buckets are unchanged; this only adds the shared
tier behind them.
"""

from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.core.config import Settings
from abb_api.ingestion.ratelimit import InMemoryRateLimiter, RateLimiter
from abb_api.limits.counters import SharedCounters
from abb_api.limits.guard import Dimension, GuardedLimiter, window_limit
from abb_api.limits.scopes import Scope
from abb_api.obs.catalogue import METRICS, Catalogue

MINUTE = 60


def build_counters(
    engine: AsyncEngine, settings: Settings, metrics: Catalogue = METRICS
) -> SharedCounters | None:
    if not settings.limits_shared_enabled:
        return None
    return SharedCounters(
        engine,
        timeout_seconds=settings.limits_timeout_ms / 1000,
        backoff_seconds=settings.limits_backoff_seconds,
        metrics=metrics,
    )


def per_minute_bucket(per_minute: int) -> InMemoryRateLimiter:
    """The local guard for `per_minute` uses: refilled evenly, bursting to the whole budget."""
    return InMemoryRateLimiter(
        events_per_second=per_minute / 60,
        burst_events=per_minute,
        bytes_per_second=1.0,
        burst_bytes=1,
    )


def minute_limiter(
    counters: SharedCounters | None,
    scope: Scope,
    per_minute: int,
    *,
    block_divisor: int = 20,
    local: RateLimiter | None = None,
) -> GuardedLimiter:
    return GuardedLimiter(
        local or per_minute_bucket(per_minute),
        counters,
        [Dimension(scope, "events", per_minute, MINUTE)],
        block_divisor=block_divisor,
    )


def ingest_limiter(
    local: RateLimiter,
    counters: SharedCounters | None,
    settings: Settings,
    *,
    events: Scope,
    bytes_: Scope,
) -> GuardedLimiter:
    window = settings.limits_ingest_window_seconds
    return GuardedLimiter(
        local,
        counters,
        [
            Dimension(
                events,
                "events",
                window_limit(
                    settings.rate_limit_events_per_second, settings.rate_limit_burst_events, window
                ),
                window,
            ),
            Dimension(
                bytes_,
                "bytes",
                window_limit(
                    settings.rate_limit_bytes_per_second, settings.rate_limit_burst_bytes, window
                ),
                window,
            ),
        ],
        block_divisor=settings.limits_block_divisor,
    )


def default_ingest_bucket(settings: Settings) -> InMemoryRateLimiter:
    return InMemoryRateLimiter(
        events_per_second=settings.rate_limit_events_per_second,
        burst_events=settings.rate_limit_burst_events,
        bytes_per_second=settings.rate_limit_bytes_per_second,
        burst_bytes=settings.rate_limit_burst_bytes,
    )

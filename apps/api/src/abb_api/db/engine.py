"""Database engine and session plumbing. Repositories (Phase 2) take an AsyncSession."""

import asyncio
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool

from abb_api.obs.metrics import Histogram


def timed_pool(wait: Histogram) -> type[AsyncAdaptedQueuePool]:
    """A pool class that observes how long every checkout took (``postgres_pool_wait_seconds``).

    The timing wraps ``Pool.connect``, the public entry point every async checkout goes through:
    it covers the wait for a free connection when the pool is saturated *and*, when it is not, the
    cost of a new connection and the pre-ping, so a slow database shows here too. It is one
    ``perf_counter`` pair and one histogram observation per checkout, no allocation beyond that.
    Limits: it does not separate the three causes, and an engine built without it (tests use
    ``NullPool``) records nothing. A pool timeout is observed too (that is the interesting case).
    """

    class TimedPool(AsyncAdaptedQueuePool):
        def connect(self) -> Any:
            started = time.perf_counter()
            try:
                return super().connect()
            finally:
                wait.observe(time.perf_counter() - started)

    return TimedPool


def create_engine(database_url: str, *, pool_wait: Histogram | None = None) -> AsyncEngine:
    # pool_pre_ping detects dead connections after a database restart. connect timeout and
    # pool_timeout bound how long a caller can wait for a connection. There is deliberately no
    # global command_timeout: it would cap every future query (batch inserts, analytics).
    # Callers that need a deadline (e.g. /readyz) apply their own.
    extra: dict[str, Any] = {"poolclass": timed_pool(pool_wait)} if pool_wait is not None else {}
    return create_async_engine(
        database_url, pool_pre_ping=True, pool_timeout=5, connect_args={"timeout": 3}, **extra
    )


READINESS_TIMEOUT_SECONDS = 2.5


async def check_database(engine: AsyncEngine) -> None:
    """Raises if the database cannot answer a trivial query within the readiness deadline.

    The deadline also covers waiting for a pooled connection, so a saturated pool reads as
    "not ready" quickly instead of hanging the probe.
    """

    async def _ping() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    await asyncio.wait_for(_ping(), timeout=READINESS_TIMEOUT_SECONDS)

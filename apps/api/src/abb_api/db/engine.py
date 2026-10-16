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

from abb_api.db.breaker import DbBreaker
from abb_api.obs.metrics import Histogram
from abb_api.obs.tracing import span


def timed_pool(
    wait: Histogram | None, breaker: DbBreaker | None = None
) -> type[AsyncAdaptedQueuePool]:
    """A pool class that observes how long every checkout took (``postgres_pool_wait_seconds``).

    The timing wraps ``Pool.connect``, the public entry point every async checkout goes through:
    it covers the wait for a free connection when the pool is saturated *and*, when it is not, the
    cost of a new connection and the pre-ping, so a slow database shows here too. It is one
    ``perf_counter`` pair and one histogram observation per checkout, no allocation beyond that.
    Limits: it does not separate the three causes, and an engine built without it (tests use
    ``NullPool``) records nothing. A pool timeout is observed too (that is the interesting case).

    The same entry point is where the circuit breaker (`db/breaker.py`) watches: it refuses a
    checkout before the pool is touched while open, and learns from the outcome of every other.
    """

    class TimedPool(AsyncAdaptedQueuePool):
        def connect(self) -> Any:
            if breaker is not None:
                breaker.before_connect()  # BreakerOpen: no pool wait, no connect attempt
            started = time.perf_counter()
            try:
                with span(
                    "db.checkout", child_only=True
                ):  # wait + connect + ping; no statement, no parameters
                    connection = super().connect()
            except Exception:
                if breaker is not None:
                    breaker.record_failure()
                raise
            else:
                if breaker is not None:
                    breaker.record_success()
                return connection
            finally:
                if wait is not None:
                    wait.observe(time.perf_counter() - started)

    return TimedPool


def create_engine(
    database_url: str,
    *,
    pool_wait: Histogram | None = None,
    statement_timeout_seconds: float = 0.0,
    breaker: DbBreaker | None = None,
) -> AsyncEngine:
    """The engine of a serving process (API, worker). Migrations, retention, backup and rotation
    build their own and never carry a default statement timeout.

    pool_pre_ping detects dead connections after a database restart. connect timeout and
    pool_timeout bound how long a caller can wait for a connection. There is deliberately no
    asyncpg ``command_timeout``: it would cap every future query on the client side. The server-side
    ``statement_timeout`` (a startup parameter of each connection, 0 = none) is the default
    deadline instead; a route with a budget of its own sets ``SET LOCAL statement_timeout``, which
    wins for its transaction. A transaction-mode pooler in front of PostgreSQL may reject startup
    parameters: set 0 here and ``ALTER ROLE ... SET statement_timeout`` on the role instead.
    """
    extra: dict[str, Any] = {}
    if pool_wait is not None or breaker is not None:
        extra["poolclass"] = timed_pool(pool_wait, breaker)
    connect_args: dict[str, Any] = {"timeout": 3}
    if statement_timeout_seconds > 0:
        connect_args["server_settings"] = {
            "statement_timeout": str(max(int(statement_timeout_seconds * 1000), 1))
        }
    return create_async_engine(
        database_url, pool_pre_ping=True, pool_timeout=5, connect_args=connect_args, **extra
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

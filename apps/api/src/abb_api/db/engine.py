"""Database engine and session plumbing. Repositories (Phase 2) take an AsyncSession."""

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    create_async_engine,
)


def create_engine(database_url: str) -> AsyncEngine:
    # pool_pre_ping detects dead connections after a database restart. connect timeout and
    # pool_timeout bound how long a caller can wait for a connection. There is deliberately no
    # global command_timeout: it would cap every future query (batch inserts, analytics).
    # Callers that need a deadline (e.g. /readyz) apply their own.
    return create_async_engine(
        database_url, pool_pre_ping=True, pool_timeout=5, connect_args={"timeout": 3}
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

"""The application role gets exactly the DML the counter service uses, and no DDL."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from abb_api.limits import Scope, SharedCounters
from tests.conftest import runtime_url
from tests.limits.helpers import workspace


async def test_the_runtime_role_can_count_and_prune_but_not_change_the_table(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> None:
    runtime = create_async_engine(runtime_url(database_url), poolclass=NullPool)
    try:
        counters = SharedCounters(runtime, timeout_seconds=5)
        got = await counters.reserve(
            Scope.ALERT_WRITE, "k", 5, 60, amount=1, workspace_id=workspace()
        )
        assert got.granted == 1
        assert await counters.prune_expired() == 0
        async with runtime.connect() as conn:
            with pytest.raises(ProgrammingError):
                await conn.execute(text("ALTER TABLE rate_limit_counters ADD COLUMN x int"))
        async with runtime.connect() as conn:
            with pytest.raises(ProgrammingError):
                await conn.execute(text("DROP TABLE rate_limit_counters"))
    finally:
        await runtime.dispose()

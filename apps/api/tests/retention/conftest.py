"""Fixtures for the retention tests: a login role that inherits `abb_retention`, like production."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from tests.conftest import _admin

RETENTION_ROLE = f"abb_ret_test_{uuid.uuid4().hex[:8]}"
RETENTION_PASSWORD = "abb_retention_test_only"


def retention_url(database_url: str) -> str:
    return (
        make_url(database_url)
        .set(username=RETENTION_ROLE, password=RETENTION_PASSWORD)
        .render_as_string(hide_password=False)
    )


@pytest.fixture(scope="session")
def retention_database_url(database_url: str) -> Iterator[str]:
    asyncio.run(
        _admin(
            f"CREATE ROLE {RETENTION_ROLE} LOGIN PASSWORD '{RETENTION_PASSWORD}' "
            "IN ROLE abb_retention"
        )
    )
    try:
        yield retention_url(database_url)
    finally:
        asyncio.run(_admin(f"DROP ROLE IF EXISTS {RETENTION_ROLE}"))


@pytest.fixture
async def retention_engine(
    retention_database_url: str, engine: AsyncEngine
) -> AsyncIterator[AsyncEngine]:
    """Connects as the retention role. `engine` (the owner) truncates and seeds."""
    retention = create_async_engine(retention_database_url, poolclass=NullPool)
    yield retention
    await retention.dispose()

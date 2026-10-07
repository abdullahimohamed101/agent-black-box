"""Database engine and session plumbing. Repositories (Phase 2) take an AsyncSession."""

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(database_url: str) -> AsyncEngine:
    # pool_pre_ping detects dead connections after a database restart; short connect timeout
    # keeps /readyz fast when the database is unreachable (every external call has a timeout).
    return create_async_engine(
        database_url, pool_pre_ping=True, connect_args={"timeout": 3, "command_timeout": 3}
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def session_scope(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session


async def check_database(engine: AsyncEngine) -> None:
    """Raises if the database cannot execute a trivial query."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))

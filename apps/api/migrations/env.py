"""Alembic environment: async engine, URL from ABB_DATABASE_URL (or -x url=... for tests)."""

import asyncio
import os

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from abb_api.db.tables import metadata

config = context.config
target_metadata = metadata


def _database_url() -> str:
    url = context.get_x_argument(as_dictionary=True).get("url") or os.environ.get(
        "ABB_DATABASE_URL"
    )
    if not url:
        raise RuntimeError("ABB_DATABASE_URL is not set (or pass -x url=...).")
    return url


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    engine = create_async_engine(_database_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=_database_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_run_async())

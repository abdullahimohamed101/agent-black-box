"""Migrations against real, empty PostgreSQL databases (one per test).

These tests are synchronous on purpose: they create databases with `asyncio.run`, which cannot run
inside a test event loop.
"""

import asyncio

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from abb_api.db.tables import metadata
from tests.conftest import API_DIR, alembic, temporary_database


def revisions() -> list[str]:
    """Revision ids from the first migration to head."""
    config = Config(str(API_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(API_DIR / "migrations"))
    script = ScriptDirectory.from_config(config)
    return [r.revision for r in reversed(list(script.walk_revisions()))]


async def table_names(url: str) -> set[str]:
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        names = {r[0] for r in rows}
    await engine.dispose()
    return names


def test_there_is_a_linear_history_with_the_expected_revisions() -> None:
    assert revisions() == ["0001", "0002", "0003", "0004", "0005", "0006"]


def test_empty_database_upgrades_to_head() -> None:
    with temporary_database(migrate=False) as url:
        alembic(url, "upgrade", "head")
        assert {t.name for t in metadata.sorted_tables} <= asyncio.run(table_names(url))


@pytest.mark.parametrize("revision", revisions())
def test_each_revision_upgrades_downgrades_and_upgrades_again(revision: str) -> None:
    """Every step is reversible and repeatable, starting from the previous revision."""
    with temporary_database(migrate=False) as url:
        alembic(url, "upgrade", revision)
        alembic(url, "downgrade", "-1")
        alembic(url, "upgrade", revision)


def test_previous_head_upgrades_to_head_and_downgrade_to_base_is_clean() -> None:
    with temporary_database(migrate=False) as url:
        alembic(url, "upgrade", "head-1")
        alembic(url, "upgrade", "head")
        alembic(url, "downgrade", "base")
        assert asyncio.run(table_names(url)) == {"alembic_version"}


async def test_migrations_match_the_table_definitions(database_url: str) -> None:
    """tables.py and the hand-reviewed migrations describe the same schema (no drift)."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    async with engine.connect() as conn:
        diffs = await conn.run_sync(
            lambda sync_conn: compare_metadata(MigrationContext.configure(sync_conn), metadata)
        )
    await engine.dispose()
    assert diffs == []

"""KI-020 / INV-1: the application's database role cannot rewrite history or change the schema.

Privileges are checked by connecting as `abb_runtime` for real (not by `SET ROLE`), so a grant
that only works for a superuser cannot pass.
"""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from tests.conftest import runtime_url
from tests.ingest_helpers import build_event, make_run_ids, make_tenant
from tests.test_event_store import ingest


@pytest.fixture
async def runtime_engine(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> AsyncIterator[AsyncEngine]:
    runtime = create_async_engine(runtime_url(database_url), poolclass=NullPool)
    yield runtime
    await runtime.dispose()


async def current_user(runtime_engine: AsyncEngine) -> str:
    async with runtime_engine.connect() as conn:
        return str((await conn.execute(text("SELECT current_user"))).scalar_one())


async def test_the_runtime_engine_really_is_the_restricted_role(
    runtime_engine: AsyncEngine,
) -> None:
    assert await current_user(runtime_engine) == "abb_runtime"
    async with runtime_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles "
                    "WHERE rolname = current_user"
                )
            )
        ).one()
    assert tuple(row) == (False, False, False)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE events SET status = 'error'",
        "DELETE FROM events",
        "TRUNCATE events",
        "DROP TABLE events",
        "ALTER TABLE events ADD COLUMN x int",
        "CREATE TABLE sneaky (id int)",
        "SELECT * FROM alembic_version",
        "ALTER ROLE abb_runtime SUPERUSER",
    ],
)
async def test_runtime_role_cannot_rewrite_events_or_change_the_schema(
    runtime_engine: AsyncEngine, statement: str
) -> None:
    with pytest.raises(ProgrammingError, match=r"permission denied|must be owner"):
        async with runtime_engine.begin() as conn:
            await conn.execute(text(statement))


async def test_runtime_role_can_append_and_read_events_and_use_the_other_tables(
    engine: AsyncEngine, runtime_engine: AsyncEngine
) -> None:
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    await ingest(runtime_engine, tenant, [build_event(tenant, run, n=1)])
    async with runtime_engine.begin() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM events"))).scalar_one() == 1
        await conn.execute(text("UPDATE runs SET name = 'renamed'"))  # derived state stays mutable
        await conn.execute(text("DELETE FROM outbox_jobs"))


async def test_every_table_except_events_is_fully_writable_by_the_runtime_role(
    engine: AsyncEngine,
) -> None:
    """Guards against a future migration adding an append-only table without saying so."""
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT c.relname, "
                "  has_table_privilege('abb_runtime', c.oid, 'UPDATE') AS can_update, "
                "  has_table_privilege('abb_runtime', c.oid, 'DELETE') AS can_delete, "
                "  has_table_privilege('abb_runtime', c.oid, 'TRUNCATE') AS can_truncate "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname <> 'alembic_version'"
            )
        )
        privileges = {r.relname: (r.can_update, r.can_delete, r.can_truncate) for r in rows}
    assert privileges.pop("events") == (False, False, False)
    assert all(p[:2] == (True, True) for p in privileges.values()), privileges

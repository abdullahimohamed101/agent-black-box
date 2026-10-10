"""KI-020 / INV-1: the application's database role cannot rewrite history or change the schema.

Privileges are checked by connecting as `abb_runtime` for real (not by `SET ROLE`), so a grant
that only works for a superuser cannot pass.
"""

import importlib.util
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from tests.conftest import API_DIR, RUNTIME_ROLE, runtime_url
from tests.ingest_helpers import build_event, make_run_ids, make_tenant
from tests.test_event_store import ingest


def _append_only_tables() -> tuple[str, ...]:
    """Declared by the migration that made the latest table append-only (not by the test)."""
    path = next((API_DIR / "migrations" / "versions").glob("0047_*.py"))
    spec = importlib.util.spec_from_file_location("migration_0047", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return tuple(module.APPEND_ONLY_TABLES)


APPEND_ONLY_TABLES = _append_only_tables()


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
    assert await current_user(runtime_engine) == RUNTIME_ROLE
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
        "UPDATE audit_log SET action = 'x'",
        "DELETE FROM audit_log",
        "TRUNCATE audit_log",
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


async def test_append_only_tables_are_exactly_the_declared_ones_and_the_rest_are_writable(
    engine: AsyncEngine,
) -> None:
    """Guards against a future migration adding an append-only table without saying so."""
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT c.relname, "
                "  has_table_privilege('abb_runtime', c.oid, 'UPDATE') AS can_update, "
                "  has_table_privilege('abb_runtime', c.oid, 'DELETE') AS can_delete, "
                "  has_table_privilege('abb_runtime', c.oid, 'TRUNCATE') AS can_truncate, "
                "  has_table_privilege('abb_runtime', c.oid, 'SELECT') AS can_select, "
                "  has_table_privilege('abb_runtime', c.oid, 'INSERT') AS can_insert "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname <> 'alembic_version'"
            )
        )
        privileges = {r.relname: r for r in rows}
    assert set(APPEND_ONLY_TABLES) == {"events", "audit_log"}
    for name in APPEND_ONLY_TABLES:
        row = privileges.pop(name)
        assert (row.can_update, row.can_delete, row.can_truncate) == (False, False, False), name
        assert (row.can_select, row.can_insert) == (True, True), name
    deletes_blocked = {"runs", "agents", "projects", "workspaces"}  # parents of events (0008)
    for name, row in privileges.items():
        assert row.can_update and row.can_delete == (name not in deletes_blocked), name


@pytest.mark.parametrize("table", ["runs", "agents", "projects", "workspaces"])
async def test_runtime_role_cannot_delete_a_parent_of_events_even_to_cascade(
    engine: AsyncEngine, runtime_engine: AsyncEngine, table: str
) -> None:
    """INV-1: deleting a run/project/workspace must not be a back door to deleting events."""
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    await ingest(runtime_engine, tenant, [build_event(tenant, run, n=1)])
    with pytest.raises(ProgrammingError, match=r"permission denied"):
        async with runtime_engine.begin() as conn:
            await conn.execute(text("DELETE FROM " + table))  # noqa: S608  (fixed test names)
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM events"))).scalar_one() == 1


async def test_even_the_owner_cannot_cascade_delete_events_through_a_run(
    engine: AsyncEngine,
) -> None:
    """The foreign key is RESTRICT: removing history needs an explicit, deliberate events delete."""
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    await ingest(engine, tenant, [build_event(tenant, run, n=1)])
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM runs"))

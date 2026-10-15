"""ADR-159: the retention role holds exactly the privileges the job needs, and no more; the
application's role still cannot rewrite or delete history, nor forge what the job reports.

Privileges are read from the catalog for the migration-created role (the exact set is pinned
here, deliberately not imported from the migration) and exercised by connecting for real.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from tests.conftest import runtime_url
from tests.ingest_helpers import build_event, make_run_ids, make_tenant
from tests.retention.conftest import RETENTION_ROLE
from tests.retention.helpers import NOW, seed_run
from tests.test_event_store import ingest

SELECT = {
    "workspaces", "projects", "runs", "spans", "events", "artifacts", "findings", "run_findings",
    "run_search", "cost_calculations", "evaluations", "outbox_jobs", "audit_log",
    "rate_limit_counters", "project_usage_daily", "project_artifact_usage", "workspace_members",
    "invitations", "retention_policies", "retention_reports", "retention_tombstones",
}  # fmt: skip
SELECT_COLUMNS = {
    ("sessions", "id"), ("sessions", "user_id"), ("sessions", "created_at"),
    ("sessions", "expires_at"), ("sessions", "idle_expires_at"), ("sessions", "revoked_at"),
    ("users", "id"), ("users", "email"), ("users", "created_at"),
    ("approvals", "requested_by_user_id"), ("approvals", "decided_by_user_id"),
}  # fmt: skip
DELETE = {
    "events", "spans", "cost_calculations", "findings", "run_findings", "run_search",
    "evaluations", "artifacts", "runs", "outbox_jobs", "audit_log", "rate_limit_counters",
    "project_usage_daily", "sessions", "users", "retention_reports",
}  # fmt: skip
INSERT = {"audit_log", "retention_reports", "retention_tombstones"}


async def table_privileges(engine: AsyncEngine, role: str) -> set[tuple[str, str]]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
                "WHERE grantee = :role AND table_schema = 'public'"
            ),
            {"role": role},
        )
        return {(r[0], r[1]) for r in rows}


async def test_the_retention_role_has_exactly_the_pinned_privileges(engine: AsyncEngine) -> None:
    granted = await table_privileges(engine, "abb_retention")
    expected = (
        {(t, "SELECT") for t in SELECT}
        | {(t, "DELETE") for t in DELETE}
        | {(t, "INSERT") for t in INSERT}
    )
    assert granted == expected
    async with engine.connect() as conn:
        columns = {
            (r[0], r[1])
            for r in await conn.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.column_privileges "
                    "WHERE grantee = 'abb_retention' AND table_schema = 'public' "
                    "AND privilege_type = 'SELECT' "
                    "AND table_name IN ('sessions', 'users', 'approvals')"
                )
            )
        }
    assert columns == SELECT_COLUMNS


async def test_the_retention_role_has_nothing_on_credentials_or_configuration(
    engine: AsyncEngine,
) -> None:
    granted = {table for table, _ in await table_privileges(engine, "abb_retention")}
    for table in ("api_keys", "server_secrets", "alert_destinations", "alert_rules", "policies",
                  "policy_versions", "approvals", "login_states", "alembic_version"):  # fmt: skip
        assert table not in granted, table
    # No UPDATE, TRUNCATE or REFERENCES anywhere, and no default privileges for tables to come.
    assert not {p for _, p in await table_privileges(engine, "abb_retention")} - {
        "SELECT",
        "DELETE",
        "INSERT",
    }
    async with engine.connect() as conn:
        defaults = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM pg_default_acl "
                    "WHERE defaclacl::text LIKE '%abb_retention%'"
                )
            )
        ).scalar_one()
        flags = (
            await conn.execute(
                text(
                    "SELECT rolsuper, rolcreatedb, rolcreaterole, rolcanlogin, rolbypassrls "
                    "FROM pg_roles WHERE rolname = 'abb_retention'"
                )
            )
        ).one()
    assert defaults == 0
    assert tuple(flags) == (False, False, False, False, False)  # NOLOGIN until given a password


async def test_a_table_created_later_is_invisible_to_the_retention_role(
    engine: AsyncEngine,
) -> None:
    """No default privileges: a future table needs an explicit grant in its own migration."""
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE zz_later (id int)"))
        try:
            granted = (
                await conn.execute(
                    text("SELECT has_table_privilege('abb_retention', 'zz_later', 'SELECT')")
                )
            ).scalar_one()
            runtime = (
                await conn.execute(
                    text("SELECT has_table_privilege('abb_runtime', 'zz_later', 'DELETE')")
                )
            ).scalar_one()
        finally:
            await conn.execute(text("DROP TABLE zz_later"))
    assert (granted, runtime) == (False, True)


async def test_the_runtime_role_cannot_write_what_the_job_reports(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT c.relname, has_table_privilege('abb_runtime', c.oid, 'SELECT'), "
                "has_table_privilege('abb_runtime', c.oid, 'INSERT'), "
                "has_table_privilege('abb_runtime', c.oid, 'UPDATE'), "
                "has_table_privilege('abb_runtime', c.oid, 'DELETE') "
                "FROM pg_class c WHERE c.relname IN "
                "('retention_reports', 'retention_tombstones', 'retention_policies') "
                "AND c.relkind = 'r'"
            )
        )
        privileges = {r[0]: tuple(r[1:]) for r in rows}
    assert privileges == {
        "retention_reports": (True, False, False, False),
        "retention_tombstones": (True, False, False, False),
        "retention_policies": (True, True, True, False),  # policies are written, never deleted
    }


@pytest.fixture
async def runtime_engine(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> AsyncEngine:
    return create_async_engine(runtime_url(database_url), poolclass=NullPool)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE events SET status = 'error'",
        "DELETE FROM events",
        "TRUNCATE events",
        "DELETE FROM runs",
        "INSERT INTO retention_reports (tick_id, tier, mode, started_at, finished_at, "
        "workspace_id) VALUES (gen_random_uuid(), 'audit', 'dry_run', now(), now(), NULL)",
        "DELETE FROM retention_reports",
        "INSERT INTO retention_tombstones (workspace_id, run_id, tier, mode) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), 'trace', 'enforce')",
        "DELETE FROM retention_policies",
        "ALTER ROLE abb_retention LOGIN PASSWORD 'x'",
    ],
)
async def test_the_application_role_cannot_erase_history_or_forge_reports(
    runtime_engine: AsyncEngine, statement: str
) -> None:
    try:
        with pytest.raises(ProgrammingError, match=r"permission denied|must be owner"):
            async with runtime_engine.begin() as conn:
                await conn.execute(text(statement))
    finally:
        await runtime_engine.dispose()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE events SET status = 'error'",
        "UPDATE runs SET name = 'x'",
        "TRUNCATE events",
        "INSERT INTO runs (workspace_id, id, project_id, trace_id, status, started_at) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), "
        "'RUNNING', now())",
        "DELETE FROM api_keys",
        "SELECT * FROM api_keys",
        "SELECT * FROM server_secrets",
        "DELETE FROM server_secrets",
        "SELECT * FROM alert_destinations",
        "SELECT token_hash FROM sessions",
        "SELECT * FROM approvals",
        "UPDATE retention_policies SET artifacts_days = 7",
        "DELETE FROM retention_policies",
        "DELETE FROM workspaces",
        "DELETE FROM projects",
        "SELECT * FROM alembic_version",
        "CREATE TABLE sneaky (id int)",
        "DROP TABLE events",
        "ALTER TABLE events ADD COLUMN x int",
        "ALTER ROLE abb_runtime SUPERUSER",
    ],
)
async def test_the_retention_role_cannot_do_more_than_delete_what_is_retained(
    retention_engine: AsyncEngine, statement: str
) -> None:
    with pytest.raises(ProgrammingError, match=r"permission denied|must be owner"):
        async with retention_engine.begin() as conn:
            await conn.execute(text(statement))


async def test_the_retention_role_really_is_the_restricted_role(
    retention_engine: AsyncEngine,
) -> None:
    async with retention_engine.connect() as conn:
        assert (await conn.execute(text("SELECT current_user"))).scalar_one() == RETENTION_ROLE
        row = (
            await conn.execute(
                text(
                    "SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles "
                    "WHERE rolname = current_user"
                )
            )
        ).one()
    assert tuple(row) == (False, False, False)


async def test_the_retention_role_can_delete_events_and_runs_in_order_and_write_a_report(
    engine: AsyncEngine, retention_engine: AsyncEngine
) -> None:
    """The one thing the application's role may not do (INV-1, ADR-159)."""
    tenant = await make_tenant(engine, "acme")
    seeded = await seed_run(engine, tenant, ended=NOW, events=2)
    ws = tenant.context.workspace_id
    async with retention_engine.begin() as conn:
        await conn.execute(text("DELETE FROM events WHERE run_id = :r"), {"r": seeded.run_id})
        await conn.execute(text("DELETE FROM runs WHERE id = :r"), {"r": seeded.run_id})
        await conn.execute(
            text(
                "INSERT INTO retention_reports (tick_id, workspace_id, tier, mode, started_at, "
                "finished_at) VALUES (gen_random_uuid(), :w, 'trace', 'dry_run', now(), now())"
            ),
            {"w": ws},
        )
        await conn.execute(
            text(
                "INSERT INTO audit_log (workspace_id, actor_kind, actor_id, action, outcome) "
                "VALUES (:w, 'system', 'retention', 'retention.batch', 'allowed')"
            ),
            {"w": ws},
        )
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM events"))).scalar_one() == 0
        assert (await conn.execute(text("SELECT count(*) FROM runs"))).scalar_one() == 0
        # Cascades removed the derived rows without any grant on them.
        assert (await conn.execute(text("SELECT count(*) FROM spans"))).scalar_one() == 0


async def test_the_runtime_role_still_cannot_delete_an_event_even_with_a_run_present(
    engine: AsyncEngine, runtime_engine: AsyncEngine
) -> None:
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    await ingest(runtime_engine, tenant, [build_event(tenant, run, n=1)])
    try:
        with pytest.raises(ProgrammingError, match="permission denied"):
            async with runtime_engine.begin() as conn:
                await conn.execute(text("DELETE FROM events"))
    finally:
        await runtime_engine.dispose()
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM events"))).scalar_one() == 1

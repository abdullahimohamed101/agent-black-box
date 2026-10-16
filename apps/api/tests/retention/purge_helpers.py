"""Builders and the reusable "no row of the scope in any backend" check for the purge tests.

`assert_scope_gone` is the verification of ADR-163: after a purge, for EVERY table that has a
`project_id` / `workspace_id` column (read from information_schema, not from the inventory) no row
of the scope is left; the tables reached only through another table are checked with the
inventory's predicates; no artifact file under the artifact directory belongs to the scope; the
search entries and rollups are gone; and every row of every other scope is byte-identical.
"""

import hashlib
import secrets
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.analytics.rollup import refresh_day
from abb_api.artifacts.store import LocalFsArtifactStore
from abb_api.retention import purge_inventory as inv
from abb_api.tenancy import TenantContext
from tests.ingest_helpers import Tenant
from tests.retention.helpers import NOW, SeededRun, days_ago, seed_run

# Columns a rewrite legitimately changes without changing the data (rollups are rebuilt).
VOLATILE = ("updated_at", "computed_at", "refreshed_at", "last_used_at")


@dataclass
class ProjectWorld:
    runs: list[SeededRun] = field(default_factory=list)
    key_id: str = ""
    project_id: uuid.UUID = field(default_factory=uuid.uuid4)

    @property
    def artifact_keys(self) -> list[str]:
        return [k for r in self.runs for k in r.artifact_keys]


def new_key_id() -> str:
    return "abb_" + secrets.token_hex(6)


async def seed_secret(engine: AsyncEngine) -> int:
    """The platform sealing key that destinations point at (kept by every purge)."""
    async with engine.begin() as conn:
        return int(
            (
                await conn.execute(
                    text("INSERT INTO server_secrets (name, secret) VALUES (:n, :s) RETURNING id"),
                    {"n": "k" + secrets.token_hex(4), "s": secrets.token_bytes(32)},
                )
            ).scalar_one()
        )


async def seed_destination(engine: AsyncEngine, tenant: Tenant, key_id: int) -> uuid.UUID:
    destination = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO alert_destinations (workspace_id, id, name, type, config_encrypted, "
                "key_id, masked_target, created_by, updated_by) VALUES (:w, :id, :n, 'slack', "
                ":c, :k, 'https://hooks.example/***', 'test', 'test')"
            ),
            {
                "w": tenant.context.workspace_id,
                "id": destination,
                "n": "dest-" + secrets.token_hex(3),
                "c": secrets.token_bytes(40),
                "k": key_id,
            },
        )
    return destination


async def seed_project_world(
    engine: AsyncEngine,
    tenant: Tenant,
    project: str,
    store: LocalFsArtifactStore,
    *,
    runs: int = 2,
    destination: uuid.UUID | None = None,
) -> ProjectWorld:
    """Rows in every table a project purge must empty, files on disk, rollups built."""
    ws = tenant.context.workspace_id
    pid = tenant.project_uuids[project]
    world = ProjectWorld(project_id=pid)
    for n in range(runs):
        world.runs.append(
            await seed_run(
                engine,
                tenant,
                ended=days_ago(5 + n),
                project=project,
                store=store,
                full=True,
                artifact_sizes=(100 + n, 200),
                events=3,
                spans=2,
            )
        )
    first = world.runs[0].run_id
    agent, evaluator, experiment, variant, rule = (uuid.uuid4() for _ in range(5))
    world.key_id = new_key_id()
    async with engine.begin() as conn:
        p = {"w": ws, "p": pid, "s": secrets.token_hex(3)}
        await conn.execute(
            text(
                "INSERT INTO agents (workspace_id, id, project_id, slug) VALUES (:w, :a, :p, 'a')"
            ),
            {**p, "a": agent},
        )
        await conn.execute(
            text(
                "INSERT INTO agent_versions (workspace_id, id, agent_id, fingerprint) "
                "VALUES (:w, gen_random_uuid(), :a, 'f')"
            ),
            {**p, "a": agent},
        )
        await conn.execute(
            text(
                "INSERT INTO api_keys (id, key_id, workspace_id, project_id, secret_hash, scopes) "
                "VALUES (gen_random_uuid(), :k, :w, :p, :h, ARRAY['events:write'])"
            ),
            {**p, "k": world.key_id, "h": secrets.token_bytes(32)},
        )
        await conn.execute(
            text(
                "INSERT INTO approvals (workspace_id, id, project_id, run_id, agent_id, "
                "capability, intent_hash, policy_rule_id, policy_version, allow_self_approval, "
                "on_expiry, requested_at, expires_at, requested_by_key_id, state) VALUES "
                "(:w, gen_random_uuid(), :p, :r, 'a', 'c', :h, 'r', 1, false, 'deny', :t, :e, "
                "'key:x', 'REQUESTED')"
            ),
            {**p, "r": first, "h": "0" * 64, "t": NOW, "e": NOW + timedelta(days=1)},
        )
        await conn.execute(
            text(
                "INSERT INTO evaluators (workspace_id, id, project_id, name, kind, implementation, "
                "version) VALUES (:w, :e, :p, 'ev-' || :s, 'deterministic', 'impl', 1)"
            ),
            {**p, "e": evaluator},
        )
        await conn.execute(
            text(
                "INSERT INTO experiments (workspace_id, id, project_id, name) "
                "VALUES (:w, :x, :p, 'exp-' || :s)"
            ),
            {**p, "x": experiment},
        )
        await conn.execute(
            text(
                "INSERT INTO experiment_variants (workspace_id, id, experiment_id, name, role) "
                "VALUES (:w, :v, :x, 'base-' || :s, 'baseline')"
            ),
            {**p, "x": experiment, "v": variant},
        )
        await conn.execute(
            text(
                "INSERT INTO experiment_runs (workspace_id, experiment_id, run_id, variant_id) "
                "VALUES (:w, :x, :r, :v)"
            ),
            {**p, "x": experiment, "r": first, "v": variant},
        )
        await conn.execute(
            text(
                "INSERT INTO policies (workspace_id, id, project_id, name, rule, action) "
                "VALUES (:w, gen_random_uuid(), :p, 'pol-' || :s, '{}'::jsonb, 'allow')"
            ),
            p,
        )
        await conn.execute(
            text(
                "INSERT INTO policy_versions (workspace_id, id, project_id, version, "
                "document_text, document_sha256, rule_count, created_by) VALUES "
                "(:w, gen_random_uuid(), :p, 1, 'doc', :h, 0, 'test')"
            ),
            {**p, "h": hashlib.sha256(b"doc").hexdigest()},
        )
        await conn.execute(
            text(
                "INSERT INTO pricing_overrides (workspace_id, id, project_id, model_pattern, "
                "input_per_million, output_per_million, valid_from) VALUES "
                "(:w, gen_random_uuid(), :p, 'm', 1, 1, :t)"
            ),
            {**p, "t": NOW},
        )
        await conn.execute(
            text(
                "INSERT INTO alert_rules (workspace_id, id, project_id, name, trigger, "
                "created_by, updated_by) VALUES (:w, :r, :p, 'rule-' || :s, 'run_failed', 't', 't')"
            ),
            {**p, "r": rule},
        )
        if destination is not None:
            await conn.execute(
                text(
                    "INSERT INTO alert_rule_destinations (workspace_id, rule_id, destination_id) "
                    "VALUES (:w, :r, :d)"
                ),
                {**p, "r": rule, "d": destination},
            )
        await conn.execute(
            text(
                "INSERT INTO alert_notifications (workspace_id, id, project_id, rule_id, trigger, "
                "dedupe_key, severity, payload_json) VALUES (:w, gen_random_uuid(), :p, :r, "
                "'run_failed', :k, 'high', '{}'::jsonb)"
            ),
            {**p, "r": rule, "k": "k" + secrets.token_hex(4)},
        )
        await conn.execute(
            text(
                "INSERT INTO project_quota_overrides (workspace_id, project_id, updated_by) "
                "VALUES (:w, :p, 'test')"
            ),
            p,
        )
        await conn.execute(
            text(
                "INSERT INTO project_usage_daily (workspace_id, project_id, day, runs_created, "
                "events_accepted) VALUES (:w, :p, :d, 1, 1)"
            ),
            {**p, "d": NOW.date()},
        )
        await conn.execute(
            text(
                "INSERT INTO rate_limit_counters (scope, workspace_id, key, window_start, count, "
                "expires_at) VALUES ('ingest_events', :w, :k, :t, 1, :e)"
            ),
            {"w": ws, "k": str(pid), "t": NOW, "e": NOW + timedelta(days=30)},
        )
        await conn.execute(
            text(
                "INSERT INTO analytics_runs_daily (workspace_id, project_id, day, agent_slug, "
                "status) VALUES (:w, :p, :d, 'a', 'SUCCESS')"
            ),
            {**p, "d": NOW.date()},
        )
    async with engine.begin() as conn:  # the rollups of the days the runs touched
        for day in {(NOW - timedelta(days=5 + n)).date() for n in range(runs)}:
            await refresh_day(conn, TenantContext(ws), day)
    return world


async def seed_workspace_extras(
    engine: AsyncEngine, tenant: Tenant, key_id: int
) -> dict[str, uuid.UUID]:
    """Workspace-level rows: destinations, invitations, audit, a non-run job, members' users."""
    ws = tenant.context.workspace_id
    destination = await seed_destination(engine, tenant, key_id)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO invitations (workspace_id, id, email, role, token_hash, expires_at) "
                "VALUES (:w, gen_random_uuid(), :e, 'VIEWER', :h, :x)"
            ),
            {
                "w": ws,
                "e": f"invitee-{secrets.token_hex(3)}@example.com",
                "h": secrets.token_bytes(32),
                "x": datetime.now(UTC) + timedelta(days=3),
            },
        )
        for n in range(3):
            await conn.execute(
                text(
                    "INSERT INTO audit_log (workspace_id, actor_kind, actor_id, action, outcome) "
                    "VALUES (:w, 'system', 'test', :a, 'allowed')"
                ),
                {"w": ws, "a": f"test.event{n}"},
            )
        await conn.execute(
            text(
                "INSERT INTO outbox_jobs (id, job_type, workspace_id, dedupe_key) "
                "VALUES (gen_random_uuid(), 'analytics_refresh', :w, :k)"
            ),
            {"w": ws, "k": f"analytics:{ws}:2026-10-10"},
        )
        await conn.execute(
            text(
                "INSERT INTO rate_limit_counters (scope, workspace_id, key, window_start, count, "
                "expires_at) VALUES ('alert_write', :w, 'workspace', :t, 1, :e)"
            ),
            {"w": ws, "t": NOW, "e": NOW + timedelta(days=30)},
        )
    return {"destination": destination}


async def seed_user(
    engine: AsyncEngine,
    email: str,
    *,
    member_of: Iterable[tuple[Tenant, str]] = (),
    live_session: bool = False,
    dead_session: bool = False,
) -> uuid.UUID:
    user = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO users (id, email) VALUES (:u, :e)"), {"u": user, "e": email}
        )
        for tenant, role in member_of:
            await conn.execute(
                text(
                    "INSERT INTO workspace_members (workspace_id, user_id, role) "
                    "VALUES (:w, :u, :r)"
                ),
                {"w": tenant.context.workspace_id, "u": user, "r": role},
            )
        now = datetime.now(UTC)
        if live_session:
            await conn.execute(
                text(
                    "INSERT INTO sessions (id, user_id, token_hash, expires_at, idle_expires_at) "
                    "VALUES (gen_random_uuid(), :u, :h, :x, :x)"
                ),
                {"u": user, "h": secrets.token_bytes(32), "x": now + timedelta(days=1)},
            )
        if dead_session:
            await conn.execute(
                text(
                    "INSERT INTO sessions (id, user_id, token_hash, expires_at, idle_expires_at) "
                    "VALUES (gen_random_uuid(), :u, :h, :x, :x)"
                ),
                {"u": user, "h": secrets.token_bytes(32), "x": now - timedelta(days=1)},
            )
    return user


# ---------------------------------------------------------------------- the verification helper

BINDS = ("w", "p", "pk", "job_types")


def _params(workspace: uuid.UUID, project: uuid.UUID | None) -> dict[str, Any]:
    return {
        "w": workspace,
        "p": project,
        "pk": str(project),
        "job_types": list(inv.JOB_TYPES),
    }


async def tables_with_column(engine: AsyncEngine, column: str) -> list[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT c.table_name FROM information_schema.columns c "
                "JOIN information_schema.tables t ON t.table_name = c.table_name "
                "AND t.table_schema = c.table_schema AND t.table_type = 'BASE TABLE' "
                "WHERE c.table_schema = 'public' AND c.column_name = :c ORDER BY 1"
            ),
            {"c": column},
        )
        return [r[0] for r in rows]


def _not_compared(scope: str) -> set[str]:
    """Tables a purge legitimately changes outside the scope, checked by their own tests: the
    record itself, the people (user rule), the audit row and the run tombstones of a project."""
    skipped = {"alembic_version", "purge_tombstones", "retention_reports", "users", "sessions"}
    if scope == "project":
        skipped |= {"audit_log", "retention_tombstones"}
    return skipped


def _use(sql: str, params: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in params.items() if f":{k}" in sql}


def _row_hash(table: str, where: str) -> str:
    volatile = ", ".join(f"'{c}'" for c in VOLATILE)
    row = f"md5((to_jsonb(x) - ARRAY[{volatile}])::text)"
    return (
        f"SELECT coalesce(md5(string_agg({row}, '|' ORDER BY {row})), ''), count(*) "  # noqa: S608
        f'FROM "{table}" x WHERE {where}'
    )


@dataclass
class Snapshot:
    """Count and content hash, per table, of the rows OUTSIDE the scope."""

    outside: dict[str, tuple[str, int]]
    inside: dict[str, int]
    files: set[str]


async def _predicate_sets(
    engine: AsyncEngine, scope: str, workspace: uuid.UUID, project: uuid.UUID | None
) -> dict[str, str]:
    """For each table, the SQL selecting the rows IN the scope."""
    out: dict[str, str] = {}
    for table, entry in inv.CLASSIFICATION.items():
        where = entry.project_where if scope == "project" else entry.workspace_where
        if where is not None:
            out[table] = where
    return out


async def snapshot(
    engine: AsyncEngine, scope: str, workspace: uuid.UUID, project: uuid.UUID | None, root: Path
) -> Snapshot:
    """Take before a purge: what lies outside the scope (to be unchanged) and what is inside."""
    where_in = await _predicate_sets(engine, scope, workspace, project)
    params = _params(workspace, project)
    outside: dict[str, tuple[str, int]] = {}
    inside: dict[str, int] = {}
    async with engine.connect() as conn:
        names = (
            await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")
            )
        ).all()
        for (table,) in names:
            if table in _not_compared(scope):
                continue
            inner = where_in.get(table)
            sql = _row_hash(table, f"NOT ({inner})" if inner else "true")
            row = (await conn.execute(text(sql), _use(sql, params))).one()
            outside[table] = (row[0], int(row[1]))
            if inner:
                cnt = f"SELECT count(*) FROM {table} WHERE {inner}"  # noqa: S608
                inside[table] = int((await conn.execute(text(cnt), _use(cnt, params))).scalar_one())
    return Snapshot(outside, inside, files_of_scope(root))


def files_of_scope(root: Path) -> set[str]:
    return (
        {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}
        if root.exists()
        else set()
    )


async def assert_scope_gone(
    engine: AsyncEngine,
    before: Snapshot,
    scope: str,
    workspace: uuid.UUID,
    project: uuid.UUID | None,
    root: Path,
    *,
    scope_files: Iterable[str],
    expect_root_gone: bool = True,
) -> None:
    """The check itself (see the module docstring). Raises AssertionError with the table named."""
    params = _params(workspace, project)
    column, value = ("project_id", project) if scope == "project" else ("workspace_id", workspace)
    # 1. Generic scan: EVERY table with the scope's column, straight from information_schema.
    scan = await tables_with_column(engine, column)
    leftovers: dict[str, int] = {}
    async with engine.connect() as conn:
        for table in scan:
            n = int(
                (
                    await conn.execute(
                        text(f'SELECT count(*) FROM "{table}" WHERE {column} = :v'),  # noqa: S608
                        {"v": value},
                    )
                ).scalar_one()
            )
            keep = table == "purge_tombstones" or (
                scope == "project" and table == "retention_tombstones"
            )
            if n and not keep and not (table == "projects" and not expect_root_gone):
                leftovers[table] = n
        # 2. Tables reached only through another table (no project_id column): the inventory's
        #    predicates, evaluated now. An id that no longer exists gives zero.
        for table, entry in inv.CLASSIFICATION.items():
            where = entry.project_where if scope == "project" else entry.workspace_where
            if where is None or table in scan or table == "workspaces":
                continue
            sql = f"SELECT count(*) FROM {table} WHERE {where}"  # noqa: S608
            n = int((await conn.execute(text(sql), _use(sql, params))).scalar_one())
            if n:
                leftovers[table] = n
        if scope == "workspace":
            sql = "SELECT count(*) FROM workspaces WHERE id = :w"
            n = int((await conn.execute(text(sql), {"w": workspace})).scalar_one())
            if n and expect_root_gone:
                leftovers["workspaces"] = n
    assert leftovers == {}, f"rows of the purged {scope} are left: {leftovers}"
    # 3. No artifact file of the scope is left on disk.
    left_files = files_of_scope(root) & set(scope_files)
    assert left_files == set(), (
        f"artifact files of the purged {scope} are left: {sorted(left_files)}"
    )
    # 4. Everything outside the scope is byte-identical (volatile columns of rebuilt rollups
    #    excluded), and the other scopes' files are all still there.
    after = await snapshot(engine, scope, workspace, project, root)
    changed = {
        t: (before.outside[t], after.outside[t])
        for t in before.outside
        if t in after.outside and before.outside[t] != after.outside[t]
    }
    assert changed == {}, f"rows outside the purged {scope} changed: {sorted(changed)}"
    assert before.files - set(scope_files) <= after.files, "files of another scope were removed"

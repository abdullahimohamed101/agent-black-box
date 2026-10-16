"""Owner-run purge of one project or of a whole workspace (ADR-163, resolves KI-170).

A purge is an explicit operator action, never an API endpoint and never reachable from the API
process: `python -m abb_api.retention purge-project|purge-workspace`, run with the retention role's
credentials. It works whatever `ABB_RETENTION_MODE` says. The default is a DRY RUN that counts per
backend and per table and deletes nothing; deletion needs `--confirm` with the exact slug.

The procedure, in this order (every step idempotent, so a crash at any point is completed by
running the same command again):

    mark          `purge_state = 'purging'` and the `purge_tombstones` row, in one transaction.
                  From here the project's keys are refused, the project (or the workspace) reads
                  as not found and ingestion stops; nothing is half visible.
    measure       counts per table and per backend, stored in the tombstone (after the mark, so
                  the figures are stable).
    runs          every run through the retention engine's whole-run procedure (ADR-160): artifact
                  files through the ArtifactStore contract, search entry, events/spans/derived
                  rows, the run record and its tombstone; per-backend progress in
                  `retention_run_progress`.
    artifacts     files of the project that have no run (orphans), through the same contract.
    analytics     the rollups of every affected day rebuilt from what is left.
    rows          alert rules (KI-170), approvals, policies, agents, keys, ... in bounded batches.
    project row   (project purge) the leftover check, one counts-only audit row and the project
                  row, last, in ONE transaction with the tombstone's completion.
    workspace     (workspace purge) every project as above, then the workspace-level rows, the
                  people (the user rule) and the workspace row, last, with the completion.

Fail closed: before the project or workspace row goes, every table of the inventory is checked for
rows of the scope; any left refuses the final step (fixed code `internal`) instead of deleting an
incomplete purge's root. The tombstone holds ids, a hash of the typed slug and counts: no names,
no content. Failure codes are fixed strings, never error text (INV-5).
"""

import asyncio
import hashlib
import logging
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from abb_api.artifacts.store import ArtifactStore
from abb_api.db import tables as t
from abb_api.retention import purge_inventory as inv
from abb_api.retention.batches import Control, Halt, Hook, no_hook
from abb_api.retention.enforce import (
    FAIL_DATABASE,
    FAIL_INTERNAL,
    Counts,
    Enforcer,
    Progress,
    StepFailure,
    _progress,
    failure_code,
)
from abb_api.retention.measure import stranger_predicate

logger = logging.getLogger(__name__)

TOOL_VERSION: Final = "1"
PROJECT_SCOPE: Final = "project"
WORKSPACE_SCOPE: Final = "workspace"
AUDIT_ACTION: Final = "retention.purge.project"

# Exit codes of the commands (documented in docs/OPERATIONS.md).
EXIT_OK: Final = 0
EXIT_USAGE: Final = 2  # wrong or missing confirmation, unusable configuration
EXIT_REFUSED: Final = 3  # nothing to purge by that name, or another process holds the scope
EXIT_FAILED: Final = 4  # a step failed (fixed code printed); run the same command again
EXIT_STOPPED: Final = 5  # told to stop; stopped at a batch boundary; run it again to finish

P = t.retention_run_progress
T = t.purge_tombstones

_BIND = re.compile(r":([a-z_]+)")


class PurgeRefused(Exception):
    """Nothing was changed. `code` is `not_found`, `locked` or `confirm`."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PurgeFailed(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PurgeStopped(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class PurgeRequest:
    scope: str  # `project` or `workspace`
    workspace_slug: str
    project_slug: str | None = None
    dry_run: bool = False
    confirm: str | None = None


@dataclass(frozen=True)
class Target:
    scope: str
    workspace_id: uuid.UUID
    workspace_slug: str
    project_id: uuid.UUID | None = None
    project_slug: str | None = None

    @property
    def slug(self) -> str:
        """The slug the operator must type to confirm."""
        if self.scope == PROJECT_SCOPE:
            assert self.project_slug is not None
            return self.project_slug
        return self.workspace_slug

    @property
    def scope_hash(self) -> str:
        name = self.workspace_slug + ("/" + self.project_slug if self.project_slug else "")
        return hashlib.sha256(name.encode()).hexdigest()


@dataclass
class Plan:
    """What the scope holds right now: rows per table, and the figures per backend."""

    tables: dict[str, int] = field(default_factory=dict)
    artifact_bytes: int = 0
    people_candidates: int = 0
    people_removable: int = 0
    resuming: bool = False

    @property
    def backends(self) -> dict[str, int]:
        return {
            "artifact_files": self.tables.get("artifacts", 0),
            "artifact_bytes": self.artifact_bytes,
            "search_entries": self.tables.get("run_search", 0),
            "rollup_rows": sum(n for k, n in self.tables.items() if k.startswith("analytics_")),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Plan":
        backends = data.get("backends", {})
        people = data.get("people", {})
        return cls(
            tables=dict(data.get("tables", {})),
            artifact_bytes=int(backends.get("artifact_bytes", 0)),
            people_candidates=int(people.get("candidates", 0)),
            people_removable=int(people.get("removable", 0)),
        )

    def as_json(self) -> dict[str, Any]:
        return {
            "tables": {k: v for k, v in sorted(self.tables.items()) if v},
            "backends": self.backends,
            "people": {"candidates": self.people_candidates, "removable": self.people_removable},
        }


# ---------------------------------------------------------------------- resolve and measure


async def resolve_target(engine: AsyncEngine, request: PurgeRequest) -> Target:
    """The ids behind the slugs, or `not_found` (one answer for every missing case)."""
    async with engine.connect() as conn:
        ws = (
            await conn.execute(
                select(t.workspaces.c.id).where(t.workspaces.c.slug == request.workspace_slug)
            )
        ).first()
        if ws is None:
            raise PurgeRefused("not_found")
        if request.scope == WORKSPACE_SCOPE:
            return Target(WORKSPACE_SCOPE, ws[0], request.workspace_slug)
        project = (
            await conn.execute(
                select(t.projects.c.id).where(
                    t.projects.c.workspace_id == ws[0],
                    t.projects.c.slug == request.project_slug,
                )
            )
        ).first()
        if project is None:
            raise PurgeRefused("not_found")
        return Target(
            PROJECT_SCOPE, ws[0], request.workspace_slug, project[0], request.project_slug
        )


def _binds(target: Target) -> dict[str, Any]:
    return {
        "w": target.workspace_id,
        "p": target.project_id,
        "pk": str(target.project_id),
        "job_types": list(inv.JOB_TYPES),
    }


def predicate_of(entry: inv.Entry, target: Target) -> str | None:
    return entry.project_where if target.scope == PROJECT_SCOPE else entry.workspace_where


def _only_used(sql: str, binds: dict[str, Any]) -> dict[str, Any]:
    names = set(_BIND.findall(sql))
    return {k: v for k, v in binds.items() if k in names}


async def count_table(conn: AsyncConnection, table: str, where: str, target: Target) -> int:
    sql = f"SELECT count(*) FROM {table} WHERE {where}"  # noqa: S608 - constants of this package
    return int((await conn.execute(text(sql), _only_used(sql, _binds(target)))).scalar_one())


async def measure(engine: AsyncEngine, target: Target) -> Plan:
    """Exact counts of the scope right now (the dry run, and the figures the tombstone keeps)."""
    plan = Plan()
    async with engine.connect() as conn:
        for table, entry in inv.CLASSIFICATION.items():
            where = predicate_of(entry, target)
            if where is None or entry.kind not in (inv.Kind.PROJECT, inv.Kind.WORKSPACE):
                continue
            plan.tables[table] = await count_table(conn, table, where, target)
        if "artifacts" in plan.tables:
            where = predicate_of(inv.CLASSIFICATION["artifacts"], target)
            sql = f"SELECT COALESCE(sum(size_bytes), 0) FROM artifacts WHERE {where}"  # noqa: S608
            plan.artifact_bytes = int(
                (await conn.execute(text(sql), _only_used(sql, _binds(target)))).scalar_one()
            )
        if target.scope == WORKSPACE_SCOPE:
            plan.people_candidates = len(await _candidates(conn, target.workspace_id))
            plan.people_removable = int(
                (
                    await conn.execute(
                        text(_REMOVABLE_PEOPLE_SQL),
                        {"w": target.workspace_id, "now": datetime.now(UTC)},
                    )
                ).scalar_one()
            )
        plan.resuming = (await _running(conn, target)) is not None
    return plan


async def _candidates(conn: AsyncConnection, workspace_id: uuid.UUID) -> list[uuid.UUID]:
    """People tied to the workspace: its members and the people who invited or accepted."""
    rows = await conn.execute(
        text(
            "SELECT user_id FROM workspace_members WHERE workspace_id = :w "
            "UNION SELECT invited_by FROM workspace_members WHERE workspace_id = :w "
            "AND invited_by IS NOT NULL "
            "UNION SELECT invited_by FROM invitations WHERE workspace_id = :w "
            "AND invited_by IS NOT NULL "
            "UNION SELECT accepted_by FROM invitations WHERE workspace_id = :w "
            "AND accepted_by IS NOT NULL"
        ),
        {"w": workspace_id},
    )
    return sorted(r[0] for r in rows)


# What a dry run says about people: members of this workspace with no other membership and no live
# session. An upper bound (a person something else still references is kept).
_REMOVABLE_PEOPLE_SQL = """
SELECT count(*) FROM (
  SELECT DISTINCT m.user_id FROM workspace_members m
   WHERE m.workspace_id = :w
     AND NOT EXISTS (SELECT 1 FROM workspace_members o
                      WHERE o.user_id = m.user_id AND o.workspace_id <> :w)
     AND NOT EXISTS (SELECT 1 FROM sessions s
                      WHERE s.user_id = m.user_id AND s.revoked_at IS NULL
                        AND s.expires_at > :now AND s.idle_expires_at > :now)
) x
"""


async def _running(conn: AsyncConnection, target: Target) -> Any:
    query = select(T).where(
        T.c.scope == target.scope, T.c.workspace_id == target.workspace_id, T.c.state == "running"
    )
    if target.project_id is not None:
        query = query.where(T.c.project_id == target.project_id)
    return (await conn.execute(query)).mappings().first()


# ---------------------------------------------------------------------- the procedure


async def leftovers(conn: AsyncConnection, target: Target) -> dict[str, int]:
    """Rows of the scope still in any table that the purge was to empty (cascaded tables and the
    root row excluded: they go with the root in the same transaction)."""
    found: dict[str, int] = {}
    skip = {"projects", "workspaces", *inv.PROJECT_CASCADED}
    if target.scope == WORKSPACE_SCOPE:
        skip |= {
            "retention_policies",
            "retention_reports",
            "retention_tombstones",
            "analytics_dirty",
        }
    for table, entry in inv.CLASSIFICATION.items():
        where = predicate_of(entry, target)
        if where is None or table in skip:
            continue
        if entry.kind is inv.Kind.WORKSPACE and target.scope == PROJECT_SCOPE:
            continue
        n = await count_table(conn, table, where, target)
        if n:
            found[table] = n
    return found


class Purger(Enforcer):
    """Runs one purge. Reuses the engine's per-run procedure; adds the project and workspace
    rows. One instance per command."""

    def __init__(
        self,
        engine: AsyncEngine,
        store: ArtifactStore,
        control: Control,
        target: Target,
        *,
        batch_size: int,
        timeout_ms: int,
        hook: Hook = no_hook,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        super().__init__(
            engine,
            store,
            control,
            batch_size=batch_size,
            max_runs=batch_size,
            max_attempts=10**6,
            timeout_ms=timeout_ms,
            hook=hook,
        )
        self.target = target
        self._now = now
        self.purge_id: uuid.UUID = uuid.UUID(int=0)
        self._done: set[str] = set()
        self._candidate_ids: list[uuid.UUID] = []
        self.plan = Plan()

    # ---- the ledger (purge_tombstones is the resume state)

    async def open(self) -> None:
        """Find the running purge of this scope or start one, marking the scope `purging` in the
        same transaction: there is never a purging scope without a record, nor a record without
        the mark."""
        target = self.target
        async with self._tx() as conn:
            row = await _running(conn, target)
            if row is None:
                purge_id = uuid.uuid4()
                await conn.execute(
                    insert(T).values(
                        id=purge_id,
                        scope=target.scope,
                        workspace_id=target.workspace_id,
                        project_id=target.project_id,
                        scope_hash=target.scope_hash,
                        tool_version=TOOL_VERSION,
                    )
                )
                row = (await conn.execute(select(T).where(T.c.id == purge_id))).mappings().one()
            await self._mark(conn)
        self.purge_id = row["id"]
        self._done = set(row["steps_done"])
        self._candidate_ids = list(row["user_candidates"])
        await self._hook("purge:marked")

    async def _mark(self, conn: AsyncConnection) -> None:
        target = self.target
        if target.scope == PROJECT_SCOPE:
            await conn.execute(
                update(t.projects)
                .where(
                    t.projects.c.workspace_id == target.workspace_id,
                    t.projects.c.id == target.project_id,
                )
                .values(purge_state="purging")
            )
        else:
            await conn.execute(
                update(t.workspaces)
                .where(t.workspaces.c.id == target.workspace_id)
                .values(purge_state="purging")
            )

    async def _step(self, name: str, work: Callable[[], Any]) -> None:
        """Run a coarse step once; its completion is recorded after it, so a crash repeats it."""
        if name in self._done:
            return
        await self.control.checkpoint()
        await work()
        async with self._tx() as conn:
            await self._record_step(conn, name)
        self._done.add(name)
        await self._hook(f"purge:{name}")

    async def _record_step(self, conn: AsyncConnection, name: str) -> None:
        await conn.execute(
            update(T)
            .where(T.c.id == self.purge_id, ~T.c.steps_done.contains([name]))
            .values(steps_done=func.array_append(T.c.steps_done, name))
        )

    async def _fail(self, code: str) -> None:
        try:
            async with self._tx() as conn:
                await conn.execute(
                    update(T)
                    .where(T.c.id == self.purge_id)
                    .values(attempts=T.c.attempts + 1, failure_code=code)
                )
        except Exception:  # never raise from bookkeeping; the command reports the code anyway
            logger.warning("could not record a purge failure", extra={"failure_code": code})

    # ---- entry point

    async def run(self) -> Plan:
        try:
            await self.open()
            await self._step("measured", self._measure)
            if self.target.scope == PROJECT_SCOPE:
                await self._project()
            else:
                await self._workspace()
        except Halt as halt:
            raise PurgeStopped(halt.reason) from None
        except PurgeStopped:
            raise
        except Exception as error:
            code = failure_code(error)
            logger.warning(
                "purge step failed",
                extra={
                    "purge_id": str(self.purge_id),
                    "failure_code": code,
                    "error_type": type(error).__name__,
                },
            )
            await self._fail(code)
            raise PurgeFailed(code) from None
        return self.plan

    async def _measure(self) -> None:
        self.plan = await measure(self._engine, self.target)
        async with self._tx() as conn:
            await conn.execute(
                update(T).where(T.c.id == self.purge_id).values(counts=self.plan.as_json())
            )

    # ---- project

    async def _project(self) -> None:
        pid = self.target.project_id
        assert pid is not None
        await self._project_data(pid)
        await self.control.checkpoint()
        await self._finish_project(pid)

    async def _project_data(self, project_id: uuid.UUID) -> None:
        """Everything of a project except its row: runs, files, rollups, rows."""
        ws = self.target.workspace_id
        await self._runs(ws, project_id)
        await self._hook("purge:runs")
        await self._project_artifacts(ws, project_id)
        await self._hook("purge:artifacts")
        await self._analytics(ws, project_id)
        await self._hook("purge:analytics")
        for table, key in inv.PROJECT_DRAINS:
            where = inv.CLASSIFICATION[table].project_where
            assert where is not None
            await self._delete_where(
                table, key, where, {"w": ws, "p": project_id, "pk": str(project_id)}
            )
            await self._hook(f"purge:{table}")

    async def _runs(self, workspace_id: uuid.UUID, project_id: uuid.UUID) -> None:
        r = t.runs
        while True:
            await self.control.checkpoint()
            async with self._engine.connect() as conn:
                ids = [
                    row[0]
                    for row in await conn.execute(
                        select(r.c.id)
                        .where(r.c.workspace_id == workspace_id, r.c.project_id == project_id)
                        .order_by(r.c.id)
                        .limit(self._batch)
                    )
                ]
            if not ids:
                return
            for run_id in ids:
                await self.control.checkpoint()
                progress = await self._begin_run(workspace_id, project_id, run_id)
                if progress is None:
                    continue
                await self._hook("begun")
                await self._steps(workspace_id, progress, Counts())

    async def _begin_run(
        self, workspace_id: uuid.UUID, project_id: uuid.UUID, run_id: uuid.UUID
    ) -> Progress | None:
        """The run's `run`-tier progress row (created if missing), whatever its age: this is not
        an expiry, so no cutoff applies."""
        r = t.runs
        async with self._tx() as conn:
            run = (
                await conn.execute(
                    select(r.c.started_at)
                    .where(r.c.workspace_id == workspace_id, r.c.id == run_id)
                    .with_for_update()
                )
            ).first()
            if run is None:
                return None
            await conn.execute(
                insert(P)
                .values(
                    workspace_id=workspace_id,
                    run_id=run_id,
                    tier="run",
                    project_id=project_id,
                    started_day=run[0].astimezone(UTC).date(),
                )
                .on_conflict_do_nothing()
            )
            row = (
                (
                    await conn.execute(
                        select(P).where(
                            P.c.workspace_id == workspace_id,
                            P.c.run_id == run_id,
                            P.c.tier == "run",
                        )
                    )
                )
                .mappings()
                .one()
            )
        return _progress(row)

    async def _project_artifacts(self, workspace_id: uuid.UUID, project_id: uuid.UUID) -> None:
        """Files of the project whose run is gone (or never arrived): files first, then rows."""
        a = t.artifacts
        while True:
            await self.control.checkpoint()
            async with self._engine.connect() as conn:
                batch = (
                    await conn.execute(
                        select(a.c.id, a.c.storage_uri)
                        .where(a.c.workspace_id == workspace_id, a.c.project_id == project_id)
                        .order_by(a.c.id)
                        .limit(self._batch)
                    )
                ).all()
            if not batch:
                return
            await self._remove_artifacts(workspace_id, batch, None)
            await self._hook("artifact_batch")

    async def _analytics(self, workspace_id: uuid.UUID, project_id: uuid.UUID) -> None:
        """Rebuild the rollups of every day the purged runs touched; a failure is fatal here (the
        project's contribution must not linger in a rollup)."""
        failed = Counts()
        await self.refresh_rollups(workspace_id, failed)
        async with self._engine.connect() as conn:
            left = (
                await conn.execute(
                    select(P.c.failure_code).where(
                        P.c.workspace_id == workspace_id,
                        P.c.project_id == project_id,
                        P.c.tier == "run",
                        ~P.c.analytics_done,
                    )
                )
            ).all()
        if left:
            raise StepFailure(next((r[0] for r in left if r[0]), FAIL_INTERNAL))

    async def _delete_where(
        self,
        table: str,
        key: tuple[str, ...] | None,
        where: str,
        params: dict[str, Any],
    ) -> int:
        """All rows of `table` matching `where`, a batch per transaction.

        Tables the role may read only by column are batched by their key columns (system columns
        like ctid need table-wide SELECT); the others by ctid, like the retention clean-ups."""
        binds = {**params, "job_types": list(inv.JOB_TYPES)}
        params = _only_used(where, binds)
        if key is None:
            return await self._drain(table, where, params, point=None)
        columns = ", ".join(key)
        same = " AND ".join(f"x.{c} = d.{c}" for c in key)
        sql = text(
            f"DELETE FROM {table} x USING "  # noqa: S608 - constants of this package
            f"(SELECT {columns} FROM {table} WHERE {where} LIMIT :batch_size) d WHERE {same}"
        )
        total = 0
        while True:
            await self.control.checkpoint()
            async with self._tx() as conn:
                n = int((await conn.execute(sql, {**params, "batch_size": self._batch})).rowcount)
            total += n
            self.control.spend(n)
            if n < self._batch:
                return total

    # ---- leftovers (fail closed)

    async def _require_empty(self, conn: AsyncConnection, target: Target | None = None) -> None:
        left = await leftovers(conn, target or self.target)
        if left:
            # Table names and counts only (constants and numbers).
            logger.warning(
                "purge refused to delete its root: rows are left",
                extra={"purge_id": str(self.purge_id), "tables": sorted(left)},
            )
            raise StepFailure(FAIL_INTERNAL)

    async def _complete(self, conn: AsyncConnection) -> None:
        await conn.execute(
            update(T)
            .where(T.c.id == self.purge_id)
            .values(
                state="completed",
                completed_at=func.now(),
                failure_code=None,
                user_candidates=[],
                steps_done=func.array_append(T.c.steps_done, "completed"),
            )
        )

    async def _finish_project(self, project_id: uuid.UUID) -> None:
        ws = self.target.workspace_id
        # A request that was in flight when the mark committed may have left a run behind.
        await self._runs(ws, project_id)
        await self._project_artifacts(ws, project_id)
        await self.control.checkpoint()
        async with self._tx() as conn:
            await self._require_empty(conn)
            plan = (await conn.execute(select(T.c.counts).where(T.c.id == self.purge_id))).scalar()
            await conn.execute(
                t.audit_log.insert().values(
                    workspace_id=ws,
                    actor_kind="system",
                    actor_id="retention",
                    action=AUDIT_ACTION,
                    resource_kind="project",
                    resource_id=str(project_id),
                    outcome="allowed",
                    details={
                        "purge_id": str(self.purge_id),
                        "rows": sum((plan or {}).get("tables", {}).values()),
                        "artifact_bytes": (plan or {}).get("backends", {}).get("artifact_bytes", 0),
                    },
                )
            )
            await conn.execute(
                delete(t.projects).where(
                    t.projects.c.workspace_id == ws, t.projects.c.id == project_id
                )
            )
            await self._complete(conn)
        await self._hook("purge:completed")

    # ---- workspace

    async def _workspace(self) -> None:
        ws = self.target.workspace_id
        await self._step("candidates", self._remember_people)
        await self._step("projects", self._all_projects)
        await self._step("workspace_rows", self._workspace_rows)
        await self._step("people", self._people)
        await self.control.checkpoint()
        await self._finish_workspace(ws)

    async def _remember_people(self) -> None:
        async with self._tx() as conn:
            ids = await _candidates(conn, self.target.workspace_id)
            await conn.execute(update(T).where(T.c.id == self.purge_id).values(user_candidates=ids))
        self._candidate_ids = ids

    async def _all_projects(self) -> None:
        ws = self.target.workspace_id
        while True:
            await self.control.checkpoint()
            async with self._engine.connect() as conn:
                ids = [
                    r[0]
                    for r in await conn.execute(
                        select(t.projects.c.id)
                        .where(t.projects.c.workspace_id == ws)
                        .order_by(t.projects.c.id)
                        .limit(self._batch)
                    )
                ]
            if not ids:
                return
            for project_id in ids:
                await self._project_data(project_id)
                await self._finish_one_project(ws, project_id)

    async def _finish_one_project(self, ws: uuid.UUID, project_id: uuid.UUID) -> None:
        """Inside a workspace purge a project's row goes as soon as its data does (no audit row:
        the workspace's audit log goes too, the tombstone is the record)."""
        await self._runs(ws, project_id)
        await self._project_artifacts(ws, project_id)
        await self.control.checkpoint()
        async with self._tx() as conn:
            await self._require_empty(conn, Target(PROJECT_SCOPE, ws, "", project_id, ""))
            await conn.execute(
                delete(t.projects).where(
                    t.projects.c.workspace_id == ws, t.projects.c.id == project_id
                )
            )
        await self._hook("purge:project_row")

    async def _workspace_rows(self) -> None:
        ws = self.target.workspace_id
        for table, key in inv.WORKSPACE_DRAINS:
            where = inv.CLASSIFICATION[table].workspace_where
            assert where is not None
            await self._delete_where(table, key, where, {"w": ws})
            await self._hook(f"purge:{table}")

    async def _people(self) -> None:
        """The user rule (ADR-163): a person this workspace tied is removed ONLY when they have no
        membership anywhere, no live session, no open invitation elsewhere and nothing that still
        references them. Their expired sessions go with them; one savepoint per person."""
        now = self._now()
        u, s = t.users, t.sessions
        for start in range(0, len(self._candidate_ids), self._batch):
            await self.control.checkpoint()
            chunk = self._candidate_ids[start : start + self._batch]
            gone_here = 0
            async with self._tx() as conn:
                for user_id in chunk:
                    try:
                        async with conn.begin_nested():
                            await conn.execute(
                                delete(s).where(
                                    s.c.user_id == user_id,
                                    (s.c.expires_at <= now)
                                    | (s.c.idle_expires_at <= now)
                                    | s.c.revoked_at.is_not(None),
                                )
                            )
                            gone = await conn.execute(
                                delete(u).where(
                                    u.c.id == user_id, stranger_predicate(now, min_age_days=None)
                                )
                            )
                            if gone.rowcount != 1:
                                raise _Kept
                            gone_here += 1
                    except (_Kept, IntegrityError):
                        pass  # kept: the savepoint also restores the sessions removed above
                if gone_here:
                    await conn.execute(
                        text(
                            "UPDATE purge_tombstones SET counts = jsonb_set(counts, '{people}', "
                            "COALESCE(counts->'people', '{}'::jsonb) || jsonb_build_object("
                            "'removed', COALESCE((counts->'people'->>'removed')::int, 0) + :n)) "
                            "WHERE id = :id"
                        ),
                        {"n": gone_here, "id": self.purge_id},
                    )

    async def _finish_workspace(self, ws: uuid.UUID) -> None:
        async with self._tx() as conn:
            # Late audit rows (a denied request raced the mark) go with the workspace.
            await conn.execute(delete(t.audit_log).where(t.audit_log.c.workspace_id == ws))
            await self._require_empty(conn)
            await conn.execute(delete(t.workspaces).where(t.workspaces.c.id == ws))
            await self._complete(conn)
        await self._hook("purge:completed")


class _Kept(Exception):
    pass


# ---------------------------------------------------------------------- the command


def _lines(request: PurgeRequest, target: Target, plan: Plan) -> list[str]:
    label = "workspace" if request.scope == WORKSPACE_SCOPE else "project"
    head = f"workspace={request.workspace_slug}" + (
        f" project={request.project_slug}" if request.project_slug else ""
    )
    out = [f"purge-{label} {head}"]
    if plan.resuming:
        out.append("a purge of this scope is in progress: running again resumes it")
    out.append("postgres, rows per table:")
    shown = {k: v for k, v in sorted(plan.tables.items()) if v}
    out += [f"  {name} {n}" for name, n in shown.items()] or ["  (none)"]
    backends = plan.backends
    out.append(
        f"artifact store: files={backends['artifact_files']} bytes={backends['artifact_bytes']}"
    )
    out.append(f"search index: entries={backends['search_entries']}")
    out.append(f"rollups: rows={backends['rollup_rows']}")
    if request.scope == WORKSPACE_SCOPE:
        out.append(
            f"people: tied to the workspace={plan.people_candidates} "
            f"removable (no other membership, no live session)<={plan.people_removable}"
        )
    return out


async def execute(
    engine: AsyncEngine,
    store: ArtifactStore | None,
    request: PurgeRequest,
    *,
    batch_size: int,
    timeout_ms: int,
    stop: asyncio.Event | None = None,
    hook: Hook = no_hook,
    out: Callable[[str], None] = print,
    monotonic: Callable[[], float] = time.monotonic,
) -> int:
    """Run the command and return its exit code. Prints counts, slugs the operator typed and fixed
    codes: never a name taken from the data, never an error text."""
    label = "purge-workspace" if request.scope == WORKSPACE_SCOPE else "purge-project"
    try:
        target = await resolve_target(engine, request)
        dry = request.dry_run or request.confirm is None
        if not dry and request.confirm != target.slug:
            # Nothing is read, locked or changed: the typed slug must be exactly the scope's.
            out(
                f"{label}: refused: --confirm does not match the {request.scope} slug "
                "(nothing was deleted)"
            )
            return EXIT_USAGE
        plan = await measure(engine, target)
        if dry:
            for line in _lines(request, target, plan):
                out(line)
            out("dry run: nothing was deleted")
            out(
                f"to purge, run the same command with --confirm {target.slug} "
                "(this is permanent: take a backup first)"
            )
            return EXIT_OK
        if store is None:
            raise PurgeFailed(FAIL_INTERNAL)
        async with engine.connect() as lock_conn:
            key = f"abb_retention:{target.workspace_id}"
            got = (
                await lock_conn.execute(
                    text("SELECT pg_try_advisory_lock(hashtextextended(:k, 0))"), {"k": key}
                )
            ).scalar_one()
            await lock_conn.commit()  # the session lock outlives the transaction
            if not got:
                raise PurgeRefused("locked")
            try:

                async def always_enforce() -> str:
                    return "enforce"  # a purge is an explicit action: the retention mode is moot

                control = Control(always_enforce, monotonic() + 10**9, monotonic, 2**62, stop)
                purger = Purger(
                    engine,
                    store,
                    control,
                    target,
                    batch_size=batch_size,
                    timeout_ms=timeout_ms,
                    hook=hook,
                )
                await purger.run()
            finally:
                await lock_conn.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:k, 0))"), {"k": key}
                )
                await lock_conn.commit()
        out(f"{label}: done purge_id={purger.purge_id}; removed (counts when the purge began):")
        async with engine.connect() as conn:
            counts = (
                await conn.execute(select(T.c.counts).where(T.c.id == purger.purge_id))
            ).scalar_one()
        for line in _lines(request, target, Plan.from_json(counts)):
            out(line)
        return EXIT_OK
    except PurgeRefused as refused:
        out(f"{label}: refused: {refused.code} (nothing was deleted by this command)")
        return EXIT_REFUSED
    except PurgeStopped as stopped:
        out(f"{label}: stopped at a batch boundary ({stopped.reason}); run the same command again")
        return EXIT_STOPPED
    except PurgeFailed as failed:
        out(f"{label}: failed: {failed.code}; run the same command again to resume")
        return EXIT_FAILED


__all__ = [
    "EXIT_FAILED",
    "EXIT_OK",
    "EXIT_REFUSED",
    "EXIT_STOPPED",
    "EXIT_USAGE",
    "FAIL_DATABASE",
    "Plan",
    "PurgeFailed",
    "PurgeRefused",
    "PurgeRequest",
    "PurgeStopped",
    "Purger",
    "Target",
    "measure",
    "resolve_target",
]

"""Quota state and counters. Every statement carries the tenant and the project (INV-3).

Concurrency rests on one lock: `lock_state` takes the project row `FOR NO KEY UPDATE` (the same
lock settings changes take; it does not block the key-share locks foreign keys take), and every
writer of quota-counted data (batch ingestion, run creation) holds it from before it reads the
counters until it commits. So the counters a writer reads cannot move under it, a second writer
waits, and the quota cannot be passed by a race, for agents or anything else. Artifact bytes use
one conditional upsert instead (the counter row itself is the lock).
"""

import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import (
    BigInteger,
    LargeBinary,
    Text,
    any_,
    bindparam,
    cast,
    func,
    literal_column,
    null,
    select,
    text,
    union_all,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID, insert
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.core.domain import NotFoundError
from abb_api.db import tables as t
from abb_api.quotas.model import Quota, QuotaLimits, QuotaOverrides
from abb_api.tenancy import TenantContext

_OVERRIDE_COLUMNS = {
    Quota.AGENTS: "max_agents",
    Quota.RUNS_PER_DAY: "max_runs_per_day",
    Quota.EVENTS_PER_DAY: "max_events_per_day",
    Quota.ARTIFACT_BYTES: "max_artifact_bytes",
}


@dataclass(frozen=True)
class ProjectState:
    """The overrides and the day's counters, read under the project lock."""

    overrides: QuotaOverrides
    runs_today: int
    events_today: int

    def limits(self, defaults: QuotaLimits) -> QuotaLimits:
        return self.overrides.resolve(defaults)


@dataclass(frozen=True)
class Snapshot:
    """What one ingest decision needs, read in one statement after the project lock is held."""

    state: ProjectState
    stored: dict[uuid.UUID, bytes]  # event id -> content hash, for the ids asked about
    run_owners: dict[uuid.UUID, uuid.UUID]  # run id -> owning project, for the runs that exist
    known_agents: set[str]


def _overrides(row: object) -> QuotaOverrides:
    return QuotaOverrides(
        agents=getattr(row, "max_agents", None),
        runs_per_day=getattr(row, "max_runs_per_day", None),
        events_per_day=getattr(row, "max_events_per_day", None),
        artifact_bytes=getattr(row, "max_artifact_bytes", None),
    )


DEFAULT_LOCK_TIMEOUT_MS = 5000
_SET_LOCK_TIMEOUT = text("SELECT set_config('lock_timeout', :ms, true)")


def is_lock_timeout(exc: BaseException) -> bool:
    """The project lock was not available within the bounded wait (SQLSTATE 55P03)."""
    return getattr(getattr(exc, "orig", None), "sqlstate", None) == "55P03"


class QuotaRepository:
    def __init__(
        self,
        conn: AsyncConnection,
        tenant: TenantContext,
        *,
        lock_timeout_ms: int = DEFAULT_LOCK_TIMEOUT_MS,
    ) -> None:
        self._conn = conn
        self._tenant = tenant
        self._lock_timeout_ms = lock_timeout_ms

    async def state(self, project_id: uuid.UUID, day: date, *, lock: bool) -> ProjectState | None:
        """Overrides and the day's counters in one statement; None if the project is not here.

        With `lock` the project row is locked first (see the module docstring); without it the
        read is a plain snapshot, for the usage screen. The lock is its own statement on purpose:
        a joined `SELECT ... FOR UPDATE` that waited re-checks only the locked row, so the usage
        rows it joined would still be the ones from before the wait.
        """
        if lock:
            await self.lock_project_row(project_id)
        p, o, u = t.projects, t.project_quota_overrides, t.project_usage_daily
        statement = (
            select(
                o.c.max_agents,
                o.c.max_runs_per_day,
                o.c.max_events_per_day,
                o.c.max_artifact_bytes,
                func.coalesce(u.c.runs_created, 0).label("runs_today"),
                func.coalesce(u.c.events_accepted, 0).label("events_today"),
            )
            .select_from(
                p.outerjoin(
                    o,
                    (o.c.workspace_id == p.c.workspace_id) & (o.c.project_id == p.c.id),
                ).outerjoin(
                    u,
                    (u.c.workspace_id == p.c.workspace_id)
                    & (u.c.project_id == p.c.id)
                    & (u.c.day == day),
                )
            )
            .where(p.c.workspace_id == self._tenant.workspace_id, p.c.id == project_id)
        )
        row = (await self._conn.execute(statement)).first()
        if row is None:
            return None
        return ProjectState(_overrides(row), int(row.runs_today), int(row.events_today))

    async def snapshot(
        self,
        project_id: uuid.UUID,
        day: date,
        *,
        event_ids: Sequence[uuid.UUID],
        run_ids: Sequence[uuid.UUID],
        slugs: Sequence[str],
    ) -> Snapshot | None:
        """The project's quota state together with which of the given events, runs and agent
        slugs already exist: one round trip (every statement costs one on the hot path).

        Call it after `lock_project_row` and never before: it is a plain read, exact only because
        the lock keeps every other counted writer of the project out (see the module docstring).
        `ANY(array)` instead of `IN (...)` keeps one statement text however many ids there are.
        """
        w = self._tenant.workspace_id
        e, r, a = t.events, t.runs, t.agents
        p, o, u = t.projects, t.project_quota_overrides, t.project_usage_daily
        kind = literal_column
        no_uuid, no_bytes = cast(null(), UUID(as_uuid=True)), cast(null(), LargeBinary)
        no_text, no_num = cast(null(), Text), cast(null(), BigInteger)
        nums = (no_num,) * 6
        branches = [
            select(kind("'e'"), e.c.event_id, no_uuid, e.c.content_hash, no_text, *nums).where(
                e.c.workspace_id == w,
                e.c.event_id == any_(bindparam("event_ids", list(event_ids), ARRAY(UUID))),
            ),
            select(kind("'r'"), r.c.id, r.c.project_id, no_bytes, no_text, *nums).where(
                r.c.workspace_id == w,
                r.c.id == any_(bindparam("run_ids", list(run_ids), ARRAY(UUID))),
            ),
            select(kind("'a'"), no_uuid, no_uuid, no_bytes, a.c.slug, *nums).where(
                a.c.workspace_id == w,
                a.c.project_id == project_id,
                a.c.slug == any_(bindparam("slugs", list(slugs), ARRAY(Text))),
            ),
            select(
                kind("'s'"),
                no_uuid,
                no_uuid,
                no_bytes,
                no_text,
                cast(o.c.max_agents, BigInteger),
                cast(o.c.max_runs_per_day, BigInteger),
                o.c.max_events_per_day,
                o.c.max_artifact_bytes,
                func.coalesce(u.c.runs_created, 0),
                func.coalesce(u.c.events_accepted, 0),
            )
            .select_from(
                p.outerjoin(
                    o, (o.c.workspace_id == p.c.workspace_id) & (o.c.project_id == p.c.id)
                ).outerjoin(
                    u,
                    (u.c.workspace_id == p.c.workspace_id)
                    & (u.c.project_id == p.c.id)
                    & (u.c.day == day),
                )
            )
            .where(p.c.workspace_id == w, p.c.id == project_id),
        ]
        state: ProjectState | None = None
        stored: dict[uuid.UUID, bytes] = {}
        owners: dict[uuid.UUID, uuid.UUID] = {}
        agents: set[str] = set()
        for row in await self._conn.execute(union_all(*branches)):
            tag = row[0]
            if tag == "e":
                stored[row[1]] = bytes(row[3])
            elif tag == "r":
                owners[row[1]] = row[2]
            elif tag == "a":
                agents.add(row[4])
            else:
                state = ProjectState(
                    QuotaOverrides(row[5], row[6], row[7], row[8]), int(row[9]), int(row[10])
                )
        if state is None:
            return None
        return Snapshot(state, stored, owners, agents)

    async def count_agents(self, project_id: uuid.UUID, cap: int) -> int:
        """Agents of the project, counted no further than `cap`."""
        a = t.agents
        inner = (
            select(a.c.id)
            .where(a.c.workspace_id == self._tenant.workspace_id, a.c.project_id == project_id)
            .limit(cap)
            .subquery()
        )
        return int((await self._conn.execute(select(func.count()).select_from(inner))).scalar_one())

    async def known_agents(self, project_id: uuid.UUID, slugs: Collection[str]) -> set[str]:
        if not slugs:
            return set()
        a = t.agents
        rows = await self._conn.execute(
            select(a.c.slug).where(
                a.c.workspace_id == self._tenant.workspace_id,
                a.c.project_id == project_id,
                a.c.slug.in_(sorted(slugs)),
            )
        )
        return {r.slug for r in rows}

    async def add_usage(self, project_id: uuid.UUID, day: date, *, runs: int, events: int) -> None:
        """Add what a committed batch stored to the day's counters (the project lock is held)."""
        if runs <= 0 and events <= 0:
            return
        table = t.project_usage_daily
        statement = insert(table).values(
            workspace_id=self._tenant.workspace_id,
            project_id=project_id,
            day=day,
            runs_created=runs,
            events_accepted=events,
        )
        await self._conn.execute(
            statement.on_conflict_do_update(
                index_elements=["workspace_id", "project_id", "day"],
                set_={
                    "runs_created": table.c.runs_created + statement.excluded.runs_created,
                    "events_accepted": table.c.events_accepted + statement.excluded.events_accepted,
                },
            )
        )

    # ------------------------------------------------------------------ artifact bytes

    async def artifact_bytes(self, project_id: uuid.UUID) -> int:
        row = (
            await self._conn.execute(
                select(t.project_artifact_usage.c.bytes_stored).where(
                    t.project_artifact_usage.c.workspace_id == self._tenant.workspace_id,
                    t.project_artifact_usage.c.project_id == project_id,
                )
            )
        ).first()
        return int(row.bytes_stored) if row else 0

    async def reserve_artifact_bytes(self, project_id: uuid.UUID, size: int, limit: int) -> bool:
        """Count `size` more stored bytes unless that passes `limit`. Exact under concurrency.

        One upsert: the conflict branch only updates while the sum stays within the limit, and the
        row lock it takes serialises concurrent uploads of the project. The first-ever row is
        inserted only if `size` itself fits (an insert has no WHERE).
        """
        if size > limit:
            return False
        table = t.project_artifact_usage
        statement = insert(table).values(
            workspace_id=self._tenant.workspace_id, project_id=project_id, bytes_stored=size
        )
        row = (
            await self._conn.execute(
                statement.on_conflict_do_update(
                    index_elements=["workspace_id", "project_id"],
                    set_={"bytes_stored": table.c.bytes_stored + statement.excluded.bytes_stored},
                    where=table.c.bytes_stored + statement.excluded.bytes_stored <= limit,
                ).returning(table.c.bytes_stored)
            )
        ).first()
        return row is not None

    # ------------------------------------------------------------------ overrides

    async def overrides(self, project_id: uuid.UUID) -> tuple[QuotaOverrides, str | None, object]:
        """The stored overrides, who set them and when (defaults and None for a project without)."""
        o = t.project_quota_overrides
        row = (
            await self._conn.execute(
                select(o).where(
                    o.c.workspace_id == self._tenant.workspace_id, o.c.project_id == project_id
                )
            )
        ).first()
        if row is None:
            return QuotaOverrides(), None, None
        return _overrides(row), row.updated_by, row.updated_at

    async def write_overrides(
        self, project_id: uuid.UUID, values: QuotaOverrides, *, updated_by: str
    ) -> None:
        table = t.project_quota_overrides
        columns = {_OVERRIDE_COLUMNS[q]: values.of(q) for q in Quota}
        statement = insert(table).values(
            workspace_id=self._tenant.workspace_id,
            project_id=project_id,
            updated_by=updated_by,
            **columns,
        )
        await self._conn.execute(
            statement.on_conflict_do_update(
                index_elements=["workspace_id", "project_id"],
                set_={**columns, "updated_by": updated_by, "updated_at": func.now()},
            )
        )

    async def lock_project(self, project_id: uuid.UUID) -> None:
        """Serialise changes to one project's overrides (and wait out running ingestion)."""
        if not await self.lock_project_row(project_id):
            raise NotFoundError("project not found")

    async def lock_project_row(self, project_id: uuid.UUID) -> bool:
        # Bounded wait (SQLSTATE 55P03 after it): a stuck holder must not pile up every batch of the
        # project. Local to this transaction, so it also bounds the row waits that follow.
        await self._conn.execute(_SET_LOCK_TIMEOUT, {"ms": str(self._lock_timeout_ms)})
        found = await self._conn.execute(
            select(t.projects.c.id)
            .where(
                t.projects.c.workspace_id == self._tenant.workspace_id,
                t.projects.c.id == project_id,
            )
            .with_for_update(key_share=True)
        )
        return found.first() is not None

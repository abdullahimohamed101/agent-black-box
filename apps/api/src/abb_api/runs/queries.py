"""Read side of runs: lookups and keyset-paginated listings, always inside one tenant.

A project-bound API key may only see its own project; `project_scope` carries that restriction
into every statement, so a run of another project is simply not found.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.db import tables as t
from abb_api.jobs.outbox import SUMMARIZE_RUN
from abb_api.tenancy import TenantContext


@dataclass(frozen=True)
class RunRecord:
    id: uuid.UUID
    project_id: uuid.UUID
    name: str | None
    status: str
    agent_slug: str | None
    trace_id: uuid.UUID
    started_at: datetime
    completed_at: datetime | None
    duration_ms: float | None
    ordering_mode: str
    summary: dict[str, Any]
    summary_version: int
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


_COLUMNS = (
    t.runs.c.id,
    t.runs.c.project_id,
    t.runs.c.name,
    t.runs.c.status,
    t.runs.c.agent_slug,
    t.runs.c.trace_id,
    t.runs.c.started_at,
    t.runs.c.completed_at,
    t.runs.c.duration_ms,
    t.runs.c.ordering_mode,
    t.runs.c.summary,
    t.runs.c.summary_version,
    t.runs.c.metadata,
    t.runs.c.created_at,
    t.runs.c.updated_at,
)


def _record(row: Any) -> RunRecord:
    return RunRecord(**{column.name: getattr(row, column.name) for column in _COLUMNS})


class RunQueries:
    def __init__(
        self, conn: AsyncConnection, tenant: TenantContext, project_scope: uuid.UUID | None = None
    ) -> None:
        self._conn = conn
        self._tenant = tenant
        self._scope = project_scope

    def _base(self) -> Select[Any]:
        statement = select(*_COLUMNS).where(t.runs.c.workspace_id == self._tenant.workspace_id)
        if self._scope is not None:
            statement = statement.where(t.runs.c.project_id == self._scope)
        return statement

    async def get(self, run_id: uuid.UUID) -> RunRecord | None:
        row = (await self._conn.execute(self._base().where(t.runs.c.id == run_id))).first()
        return _record(row) if row else None

    async def page(
        self,
        *,
        statuses: Sequence[str] = (),
        agent_slug: str | None = None,
        project_id: uuid.UUID | None = None,
        started_after: datetime | None = None,
        started_before: datetime | None = None,
        ascending: bool = False,
        limit: int,
        after: tuple[datetime, uuid.UUID] | None = None,
    ) -> list[RunRecord]:
        """Up to `limit` runs after the cursor position (the caller asks for one extra row)."""
        statement = self._base()
        if statuses:
            statement = statement.where(t.runs.c.status.in_(list(statuses)))
        if agent_slug is not None:
            statement = statement.where(t.runs.c.agent_slug == agent_slug)
        if project_id is not None:
            statement = statement.where(t.runs.c.project_id == project_id)
        if started_after is not None:
            statement = statement.where(t.runs.c.started_at >= started_after)
        if started_before is not None:
            statement = statement.where(t.runs.c.started_at < started_before)
        key = tuple_(t.runs.c.started_at, t.runs.c.id)
        if after is not None:
            position = tuple_(
                literal(after[0], t.runs.c.started_at.type), literal(after[1], t.runs.c.id.type)
            )
            statement = statement.where(key > position if ascending else key < position)
        order = (
            (t.runs.c.started_at.asc(), t.runs.c.id.asc())
            if ascending
            else (
                t.runs.c.started_at.desc(),
                t.runs.c.id.desc(),
            )
        )
        rows = await self._conn.execute(statement.order_by(*order).limit(limit))
        return [_record(r) for r in rows]

    async def processing(self, run_ids: Sequence[uuid.UUID]) -> set[uuid.UUID]:
        """Runs with a summarize job still waiting or running: their summary is not current."""
        if not run_ids:
            return set()
        keys = {f"{self._tenant.workspace_id}:{run_id}": run_id for run_id in run_ids}
        rows = await self._conn.execute(
            select(t.outbox_jobs.c.dedupe_key).where(
                t.outbox_jobs.c.workspace_id == self._tenant.workspace_id,
                t.outbox_jobs.c.job_type == SUMMARIZE_RUN,
                t.outbox_jobs.c.status.in_(("pending", "running")),
                t.outbox_jobs.c.dedupe_key.in_(list(keys)),
            )
        )
        return {keys[r.dedupe_key] for r in rows}

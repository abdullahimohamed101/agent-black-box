"""Read side of spans (derived by the summarizer)."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.db import tables as t
from abb_api.tenancy import TenantContext


@dataclass(frozen=True)
class SpanRecord:
    id: uuid.UUID
    run_id: uuid.UUID
    trace_id: uuid.UUID
    parent_span_id: uuid.UUID | None
    name: str | None
    kind: str | None
    agent_slug: str
    status: str | None
    started_at: datetime | None
    ended_at: datetime | None
    duration_ms: float | None
    event_count: int


class SpanRepository:
    def __init__(self, conn: AsyncConnection, tenant: TenantContext) -> None:
        self._conn = conn
        self._tenant = tenant

    async def page(
        self,
        run_id: uuid.UUID,
        *,
        limit: int,
        after: tuple[datetime | None, uuid.UUID] | None = None,
    ) -> list[SpanRecord]:
        """Spans by start time (unstarted ones last), then id; `after` = previous page end."""
        c = t.spans.c
        statement = select(*c).where(
            c.workspace_id == self._tenant.workspace_id, c.run_id == run_id
        )
        if after is not None:
            started, span_id = after
            after_id = literal(span_id, c.id.type)
            if started is None:  # inside the "never saw a start event" tail
                statement = statement.where(and_(c.started_at.is_(None), c.id > after_id))
            else:
                at = literal(started, c.started_at.type)
                statement = statement.where(
                    or_(
                        c.started_at > at,
                        and_(c.started_at == at, c.id > after_id),
                        c.started_at.is_(None),
                    )
                )
        rows = await self._conn.execute(
            statement.order_by(c.started_at.asc().nulls_last(), c.id.asc()).limit(limit)
        )
        return [
            SpanRecord(
                id=r.id, run_id=r.run_id, trace_id=r.trace_id, parent_span_id=r.parent_span_id,
                name=r.name, kind=r.kind, agent_slug=r.agent_slug, status=r.status,
                started_at=r.started_at, ended_at=r.ended_at, duration_ms=r.duration_ms,
                event_count=r.event_count,
            )
            for r in rows
        ]  # fmt: skip

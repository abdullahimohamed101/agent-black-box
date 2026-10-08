"""Read side of events: ordered keyset pages that match `sort_events` exactly (spec §65.1)."""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from abb_event_schema.event import Event
from sqlalchemy import ColumnElement, Select, func, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.db import tables as t
from abb_api.db.event_rows import row_to_event
from abb_api.tenancy import TenantContext

_SEQUENCE = func.coalesce(t.events.c.sequence, 0)
# Everything except `payload`: list views never read large inline payloads.
_LIGHT_COLUMNS = [c for c in t.events.c if c.name not in ("payload", "content_hash")]


def order_columns(mode: str) -> list[ColumnElement[Any]]:
    """Canonical order: by sequence when every event has one, otherwise by time first."""
    if mode == "sequence":
        return [_SEQUENCE, t.events.c.occurred_at, t.events.c.received_at, t.events.c.event_id]
    return [t.events.c.occurred_at, _SEQUENCE, t.events.c.received_at, t.events.c.event_id]


class EventQueries:
    def __init__(self, conn: AsyncConnection, tenant: TenantContext) -> None:
        self._conn = conn
        self._tenant = tenant

    def _scoped(self, run_id: uuid.UUID) -> Select[Any]:
        return select(*_LIGHT_COLUMNS, t.events.c.payload.is_not(None).label("has_payload")).where(
            t.events.c.workspace_id == self._tenant.workspace_id, t.events.c.run_id == run_id
        )

    async def page(
        self,
        run_id: uuid.UUID,
        *,
        mode: str,
        event_types: Sequence[str] = (),
        statuses: Sequence[str] = (),
        span_id: uuid.UUID | None = None,
        limit: int,
        after: Sequence[Any] | None = None,
    ) -> list[tuple[Event, bool]]:
        """Up to `limit` events as (event without payload, has_payload)."""
        statement = self._scoped(run_id)
        if event_types:
            statement = statement.where(t.events.c.event_type.in_(list(event_types)))
        if statuses:
            statement = statement.where(t.events.c.status.in_(list(statuses)))
        if span_id is not None:
            statement = statement.where(t.events.c.span_id == span_id)
        columns = order_columns(mode)
        if after is not None:
            position = tuple_(
                *[literal(value, column.type) for value, column in zip(after, columns, strict=True)]
            )
            statement = statement.where(tuple_(*columns) > position)
        rows = await self._conn.execute(statement.order_by(*columns).limit(limit))
        return [(row_to_event(r), bool(r.has_payload)) for r in rows]

    async def get(self, run_id: uuid.UUID, event_id: uuid.UUID) -> Event | None:
        row = (
            await self._conn.execute(
                select(t.events).where(
                    t.events.c.workspace_id == self._tenant.workspace_id,
                    t.events.c.run_id == run_id,
                    t.events.c.event_id == event_id,
                )
            )
        ).first()
        return row_to_event(row) if row else None

    async def arrival_of(self, run_id: uuid.UUID, event_id: uuid.UUID) -> datetime | None:
        """When the server received an event of this run , else None."""
        row = (
            await self._conn.execute(
                select(t.events.c.received_at).where(
                    t.events.c.workspace_id == self._tenant.workspace_id,
                    t.events.c.run_id == run_id,
                    t.events.c.event_id == event_id,
                )
            )
        ).first()
        return None if row is None else row.received_at

    async def count_window(
        self, run_id: uuid.UUID, *, since: datetime | None, upto: tuple[datetime, uuid.UUID]
    ) -> int:
        """How many events arrived in [`since`, `upto`], inclusive: a cheap check for late rows."""
        columns = [t.events.c.received_at, t.events.c.event_id]
        statement = select(func.count()).where(
            t.events.c.workspace_id == self._tenant.workspace_id,
            t.events.c.run_id == run_id,
            tuple_(*columns)
            <= tuple_(literal(upto[0], columns[0].type), literal(upto[1], columns[1].type)),
        )
        if since is not None:
            statement = statement.where(t.events.c.received_at >= since)
        return int((await self._conn.execute(statement)).scalar_one())

    async def last_lifecycle(self, run_id: uuid.UUID, kinds: Sequence[str]) -> str | None:
        """The type of the run's most recently received event among `kinds`, if any."""
        row = (
            await self._conn.execute(
                select(t.events.c.event_type)
                .where(
                    t.events.c.workspace_id == self._tenant.workspace_id,
                    t.events.c.run_id == run_id,
                    t.events.c.event_type.in_(list(kinds)),
                )
                .order_by(t.events.c.received_at.desc(), t.events.c.event_id.desc())
                .limit(1)
            )
        ).first()
        return None if row is None else str(row.event_type)

    async def arrived_since(
        self,
        run_id: uuid.UUID,
        *,
        since: datetime | None,
        after: tuple[datetime, uuid.UUID] | None = None,
        limit: int,
    ) -> list[tuple[Event, bool]]:
        """Up to `limit` events in arrival order (`received_at`, `event_id`): (event, has_payload).

        `since` is inclusive (None: from the start). `after` continues from a page's last row.
        """
        statement = self._scoped(run_id)
        if since is not None:
            statement = statement.where(t.events.c.received_at >= since)
        columns = [t.events.c.received_at, t.events.c.event_id]
        if after is not None:
            statement = statement.where(
                tuple_(*columns)
                > tuple_(literal(after[0], columns[0].type), literal(after[1], columns[1].type))
            )
        rows = await self._conn.execute(statement.order_by(*columns).limit(limit))
        return [(row_to_event(r), bool(r.has_payload)) for r in rows]

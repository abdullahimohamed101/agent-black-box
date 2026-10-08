"""Run query use cases: authorisation scoping, cursors, and mapping rows to API shapes."""

import uuid
from datetime import UTC, datetime
from typing import Any

from abb_event_schema.event import Event
from abb_event_schema.ids import IdKind, from_uuid, new_id, to_uuid
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from abb_api.clock import Clock
from abb_api.core.errors import AppError, ErrorCategory
from abb_api.ids import parse_public_id
from abb_api.projects.repository import ProjectRepository
from abb_api.runs import cursors
from abb_api.runs.event_queries import EventQueries
from abb_api.runs.queries import RunQueries, RunRecord
from abb_api.runs.repository import RunRepository
from abb_api.runs.schemas import (
    CreateRunRequest,
    EventOut,
    EventPage,
    RunOut,
    RunPage,
    SpanOut,
    SpanPage,
)
from abb_api.tenancy import Principal
from abb_api.traces.repository import SpanRecord, SpanRepository


def _not_found(code: str, what: str) -> AppError:
    return AppError(code, f"{what} not found.", category=ErrorCategory.NOT_FOUND, status_code=404)


def run_not_found() -> AppError:
    return _not_found("RUN_NOT_FOUND", "Run")


def project_not_found() -> AppError:
    return _not_found("PROJECT_NOT_FOUND", "Project")


def event_out(event: Event, has_payload: bool, *, with_payload: bool) -> EventOut:
    wire = event.to_wire()
    wire.setdefault("attributes", {})
    wire["payload"] = wire.get("payload") if with_payload else None
    return EventOut(**wire, has_payload=has_payload)


def _run_out(record: RunRecord, state: str) -> RunOut:
    return RunOut(
        id=from_uuid(IdKind.RUN, record.id),
        project_id=from_uuid(IdKind.PROJECT, record.project_id),
        name=record.name,
        status=record.status,  # type: ignore[arg-type]
        agent_id=record.agent_slug,
        trace_id=from_uuid(IdKind.TRACE, record.trace_id),
        started_at=record.started_at,
        completed_at=record.completed_at,
        duration_ms=record.duration_ms,
        ordering_mode=record.ordering_mode,  # type: ignore[arg-type]
        summary=record.summary,
        summary_version=record.summary_version,
        summary_state=state,  # type: ignore[arg-type]
        metadata=record.metadata,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _span_out(record: SpanRecord) -> SpanOut:
    return SpanOut(
        id=from_uuid(IdKind.SPAN, record.id),
        run_id=from_uuid(IdKind.RUN, record.run_id),
        trace_id=from_uuid(IdKind.TRACE, record.trace_id),
        parent_span_id=from_uuid(IdKind.SPAN, record.parent_span_id)
        if record.parent_span_id
        else None,
        name=record.name,
        kind=record.kind,
        agent_id=record.agent_slug,
        status=record.status,
        started_at=record.started_at,
        ended_at=record.ended_at,
        duration_ms=record.duration_ms,
        event_count=record.event_count,
    )


def _aware(value: datetime | None, name: str) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise AppError(
            "REQUEST_INVALID",
            f"{name} must include a UTC offset.",
            category=ErrorCategory.VALIDATION,
            status_code=422,
        )
    if value is None:
        return None
    try:
        return value.astimezone(UTC)
    except OverflowError:  # e.g. 0001-01-01T00:00:00+14:00 has no UTC form
        raise AppError(
            "REQUEST_INVALID",
            f"{name} is outside the supported range.",
            category=ErrorCategory.VALIDATION,
            status_code=422,
        ) from None


class RunService:
    def __init__(self, engine: AsyncEngine, clock: Clock) -> None:
        self._engine = engine
        self._clock = clock

    # ---------------------------------------------------------------- writes

    async def create_run(
        self, principal: Principal, request: CreateRunRequest
    ) -> tuple[RunOut, bool]:
        assert principal.project_id is not None
        run_id = to_uuid(request.run_id or new_id(IdKind.RUN))
        trace_id = to_uuid(request.trace_id or new_id(IdKind.TRACE))
        async with self._engine.begin() as conn:
            created, owner = await RunRepository(conn, principal.tenant).create_queued(
                run_id=run_id,
                project_id=principal.project_id,
                trace_id=trace_id,
                name=request.name,
                agent_slug=request.agent_id,
                metadata=request.metadata,
                now=self._clock(),
            )
            if owner != principal.project_id:
                raise AppError(
                    "RUN_PROJECT_MISMATCH",
                    "That run id belongs to another project.",
                    category=ErrorCategory.CONFLICT,
                    status_code=409,
                )
            record = await RunQueries(conn, principal.tenant, principal.project_id).get(run_id)
        assert record is not None
        return _run_out(record, "current"), created

    # ---------------------------------------------------------------- reads

    async def list_runs(
        self,
        principal: Principal,
        *,
        statuses: list[str],
        agent_id: str | None,
        project_id: str | None,
        started_after: datetime | None,
        started_before: datetime | None,
        ascending: bool,
        limit: int,
        cursor: str | None,
    ) -> RunPage:
        after_key = self._run_cursor(cursor)
        async with self._engine.connect() as conn:
            scope_project = await self._project_filter(conn, principal, project_id)
            queries = RunQueries(conn, principal.tenant, principal.project_id)
            records = await queries.page(
                statuses=statuses,
                agent_slug=agent_id,
                project_id=scope_project,
                started_after=_aware(started_after, "started_after"),
                started_before=_aware(started_before, "started_before"),
                ascending=ascending,
                limit=limit + 1,
                after=after_key,
            )
            page = records[:limit]
            states = await queries.summary_states([r.id for r in page])
        next_cursor = None
        if len(records) > limit:
            last = page[-1]
            next_cursor = cursors.encode(
                cursors.Cursor("runs", [last.started_at.isoformat(), str(last.id)])
            )
        return RunPage(
            items=[_run_out(r, states[r.id]) for r in page],
            next_cursor=next_cursor,
        )

    async def get_run(self, principal: Principal, run_id: str) -> RunOut:
        async with self._engine.connect() as conn:
            record = await self._visible_run(conn, principal, run_id)
            states = await RunQueries(conn, principal.tenant, principal.project_id).summary_states(
                [record.id]
            )
        return _run_out(record, states[record.id])

    async def list_events(
        self,
        principal: Principal,
        run_id: str,
        *,
        event_types: list[str],
        statuses: list[str],
        span_id: str | None,
        limit: int,
        cursor: str | None,
    ) -> EventPage:
        async with self._engine.connect() as conn:
            record = await self._visible_run(conn, principal, run_id)
            mode = record.ordering_mode
            after = self._event_cursor(cursor, mode)
            span_uuid = parse_public_id(IdKind.SPAN, span_id) if span_id else None
            found = await EventQueries(conn, principal.tenant).page(
                record.id,
                mode=mode,
                event_types=event_types,
                statuses=statuses,
                span_id=span_uuid,
                limit=limit + 1,
                after=after,
            )
        page = found[:limit]
        next_cursor = None
        if len(found) > limit:
            last = page[-1][0]
            next_cursor = cursors.encode(
                cursors.Cursor("events", _event_key(last, mode), mode=mode)
            )
        return EventPage(
            items=[event_out(e, has, with_payload=False) for e, has in page],
            next_cursor=next_cursor,
            ordering_mode=mode,  # type: ignore[arg-type]
        )

    async def get_event(self, principal: Principal, run_id: str, event_id: str) -> EventOut:
        event_uuid = parse_public_id(IdKind.EVENT, event_id)
        async with self._engine.connect() as conn:
            record = await self._visible_run(conn, principal, run_id)
            event = (
                await EventQueries(conn, principal.tenant).get(record.id, event_uuid)
                if event_uuid
                else None
            )
        if event is None:
            raise _not_found("EVENT_NOT_FOUND", "Event")
        return event_out(event, event.payload is not None, with_payload=True)

    async def list_spans(
        self, principal: Principal, run_id: str, *, limit: int, cursor: str | None
    ) -> SpanPage:
        after = self._span_cursor(cursor)
        async with self._engine.connect() as conn:
            record = await self._visible_run(conn, principal, run_id)
            found = await SpanRepository(conn, principal.tenant).page(
                record.id, limit=limit + 1, after=after
            )
        page = found[:limit]
        next_cursor = None
        if len(found) > limit:
            last = page[-1]
            started = last.started_at.isoformat() if last.started_at else None
            next_cursor = cursors.encode(cursors.Cursor("spans", [started, str(last.id)]))
        return SpanPage(items=[_span_out(s) for s in page], next_cursor=next_cursor)

    # ---------------------------------------------------------------- helpers

    async def visible_run(
        self, conn: AsyncConnection, principal: Principal, run_id: str
    ) -> RunRecord:
        """The run if the caller may see it, else the same 404 as everywhere (used by streams)."""
        return await self._visible_run(conn, principal, run_id)

    async def _visible_run(
        self, conn: AsyncConnection, principal: Principal, run_id: str
    ) -> RunRecord:
        """The run if it exists in the caller's workspace (and project); otherwise 404."""
        run_uuid = parse_public_id(IdKind.RUN, run_id)
        record = (
            await RunQueries(conn, principal.tenant, principal.project_id).get(run_uuid)
            if run_uuid
            else None
        )
        if record is None:
            raise run_not_found()
        return record

    async def _project_filter(
        self, conn: AsyncConnection, principal: Principal, project_id: str | None
    ) -> uuid.UUID | None:
        if project_id is None:
            return None
        requested = parse_public_id(IdKind.PROJECT, project_id)
        if requested is None:
            raise project_not_found()
        if principal.project_id is not None:
            if requested != principal.project_id:
                raise project_not_found()  # a project key cannot even probe other projects
        elif await ProjectRepository(conn, principal.tenant).get(requested) is None:
            raise project_not_found()
        return requested

    @staticmethod
    def _run_cursor(token: str | None) -> tuple[datetime, uuid.UUID] | None:
        if token is None:
            return None
        key = cursors.decode(token, "runs", width=2).key
        return cursors.as_datetime(key[0]), cursors.as_uuid(key[1])

    @staticmethod
    def _event_cursor(token: str | None, mode: str) -> list[Any] | None:
        if token is None:
            return None
        decoded = cursors.decode(token, "events", width=4)
        if decoded.mode != mode:
            raise cursors.cursor_stale()
        key = decoded.key
        if mode == "sequence":
            return [
                cursors.as_int64(key[0]),
                cursors.as_datetime(key[1]),
                cursors.as_datetime(key[2]),
                cursors.as_uuid(key[3]),
            ]
        return [
            cursors.as_datetime(key[0]),
            cursors.as_int64(key[1]),
            cursors.as_datetime(key[2]),
            cursors.as_uuid(key[3]),
        ]

    @staticmethod
    def _span_cursor(token: str | None) -> tuple[datetime | None, uuid.UUID] | None:
        if token is None:
            return None
        key = cursors.decode(token, "spans", width=2).key
        started = cursors.as_datetime(key[0]) if key[0] is not None else None
        return started, cursors.as_uuid(key[1])


def _event_key(event: Event, mode: str) -> list[Any]:
    sequence = event.sequence or 0
    occurred, received = event.occurred_at.isoformat(), event.received_at.isoformat()
    event_id = str(to_uuid(event.event_id))
    return (
        [sequence, occurred, received, event_id]
        if mode == "sequence"
        else [occurred, sequence, received, event_id]
    )

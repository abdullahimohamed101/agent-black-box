"""Run query use cases: authorisation scoping, cursors, and mapping rows to API shapes."""

import uuid
from datetime import UTC, datetime
from typing import Any

from abb_event_schema.event import Event
from abb_event_schema.ids import IdKind, from_uuid, new_id, to_uuid
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from abb_api.authz import actions
from abb_api.authz.audit import authorize_audited
from abb_api.authz.principal import Principal
from abb_api.authz.service import PermissionDenied, holds
from abb_api.clock import Clock
from abb_api.core.errors import AppError, ErrorCategory, dependency_unavailable
from abb_api.cost.repository import CostRepository, EventCost
from abb_api.findings.detectors import DETECTORS_VERSION
from abb_api.findings.repository import Badge, FindingsRepository
from abb_api.ids import parse_public_id
from abb_api.projects.access import authorise_project
from abb_api.quotas.model import Quota, QuotaLimits, quota_exceeded
from abb_api.quotas.repository import DEFAULT_LOCK_TIMEOUT_MS, QuotaRepository, is_lock_timeout
from abb_api.runs import cursors
from abb_api.runs.content import span_name_is_content, withhold_attributes
from abb_api.runs.event_queries import EventQueries
from abb_api.runs.queries import FacetCounts, RunQueries, RunRecord
from abb_api.runs.repository import RunRepository
from abb_api.runs.schemas import (
    CreateRunRequest,
    EventOut,
    EventPage,
    FirstErrorOut,
    RunFindingsBadge,
    RunOut,
    RunPage,
    RunSecurityBadge,
    SpanOut,
    SpanPage,
)
from abb_api.runs.search_view import facets_out, matched_out, query_out
from abb_api.search.compiler import CompiledSearch, compile_query
from abb_api.search.errors import QueryError, query_invalid
from abb_api.search.parser import parse
from abb_api.search.reads import SearchGate
from abb_api.traces.repository import SpanRecord, SpanRepository


def _not_found(code: str, what: str) -> AppError:
    return AppError(code, f"{what} not found.", category=ErrorCategory.NOT_FOUND, status_code=404)


def run_not_found() -> AppError:
    return _not_found("RUN_NOT_FOUND", "Run")


def trace_expired() -> AppError:
    """The run's record is there but its trace detail was removed by retention (ADR-160)."""
    return AppError(
        "TRACE_EXPIRED",
        "The run's trace is no longer stored.",
        category=ErrorCategory.CONFLICT,
        status_code=409,
    )


def project_not_found() -> AppError:
    return _not_found("PROJECT_NOT_FOUND", "Project")


def event_out(
    event: Event,
    has_payload: bool,
    *,
    with_payload: bool,
    reads_content: bool,
    cost: EventCost | None = None,
) -> EventOut:
    """`reads_content`: the caller holds `payload.read`. Without it, command text and file paths
    in the attributes are replaced by a marker (review F3) and, on the detail endpoint, the body is
    dropped and the response says so (D10). Lists and streams never carry payloads at all, so they
    never set `payload_withheld`. The stored event is untouched (INV-1)."""
    wire = event.to_wire()
    attributes = wire.setdefault("attributes", {})
    hidden: list[str] = []
    if not reads_content:
        wire["attributes"], hidden = withhold_attributes(attributes)
    withheld = with_payload and not reads_content
    wire["payload"] = wire.get("payload") if with_payload and reads_content else None
    return EventOut(
        **wire,
        has_payload=has_payload,
        payload_withheld=withheld,
        withheld_attributes=hidden,
        cost_usd=float(cost.total) if cost else None,
        cost_unpriced=cost.unpriced if cost else False,
    )


def _badge(badge: Badge | None) -> RunFindingsBadge:
    if badge is None:
        return RunFindingsBadge(count=0, max_severity=None, state="none")
    return RunFindingsBadge(
        count=badge.count,
        max_severity=badge.max_severity,  # type: ignore[arg-type]
        state="outdated" if badge.detectors_version < DETECTORS_VERSION else "current",
    )


def _security_badge(badge: Badge | None) -> RunSecurityBadge:
    if badge is None:
        return RunSecurityBadge(count=0, max_severity=None, state="none")
    return RunSecurityBadge(
        count=badge.security_count,
        max_severity=badge.security_max_severity,  # type: ignore[arg-type]
        state="outdated" if badge.detectors_version < DETECTORS_VERSION else "current",
    )


def _first_error(summary: dict[str, Any]) -> FirstErrorOut | None:
    event_id = summary.get("first_error_event_id")
    if not isinstance(event_id, str):
        return None
    return FirstErrorOut(
        event_id=event_id,
        sequence=summary.get("first_error_sequence"),
        retries_after=summary.get("retries_after") or 0,
        seconds_after=summary.get("seconds_after") or 0.0,
        cost_after_usd=summary.get("cost_after_usd") or 0.0,
        cost_after_unpriced_calls=summary.get("cost_after_unpriced_calls") or 0,
    )


def _run_out(
    record: RunRecord,
    state: str,
    badge: RunFindingsBadge | None = None,
    security: RunSecurityBadge | None = None,
    *,
    with_agents: bool = True,
    search: CompiledSearch | None = None,
) -> RunOut:
    summary = record.summary
    if not with_agents and "agents" in summary:
        # The per-agent breakdown is for the detail page; lists (and the dashboard that polls
        # them) carry `agent_count` only (Phase 9, D8).
        summary = {k: v for k, v in summary.items() if k != "agents"}
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
        summary=summary,
        summary_version=record.summary_version,
        summary_state=state,  # type: ignore[arg-type]
        metadata=record.metadata,
        created_at=record.created_at,
        updated_at=record.updated_at,
        findings=badge,
        security_findings=security,
        first_error=_first_error(summary),
        matched=matched_out(search, record.search) if search is not None else None,
        trace_state=record.trace_state,  # type: ignore[arg-type]
    )


def _span_out(record: SpanRecord, *, reads_content: bool) -> SpanOut:
    hide = not reads_content and record.name is not None and span_name_is_content(record.kind)
    return SpanOut(
        id=from_uuid(IdKind.SPAN, record.id),
        run_id=from_uuid(IdKind.RUN, record.run_id),
        trace_id=from_uuid(IdKind.TRACE, record.trace_id),
        parent_span_id=from_uuid(IdKind.SPAN, record.parent_span_id)
        if record.parent_span_id
        else None,
        name=None if hide else record.name,
        name_withheld=hide,
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
    def __init__(
        self,
        engine: AsyncEngine,
        clock: Clock,
        quota_defaults: QuotaLimits | None = None,
        quota_lock_timeout_ms: int = DEFAULT_LOCK_TIMEOUT_MS,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._quota_defaults = quota_defaults  # None: quotas are off
        self._quota_lock_timeout_ms = quota_lock_timeout_ms

    # ---------------------------------------------------------------- writes

    async def create_run(
        self, principal: Principal, request: CreateRunRequest
    ) -> tuple[RunOut, bool]:
        assert principal.project_id is not None
        run_id = to_uuid(request.run_id or new_id(IdKind.RUN))
        trace_id = to_uuid(request.trace_id or new_id(IdKind.TRACE))
        now = self._clock()
        try:
            async with self._engine.begin() as conn:
                quotas = QuotaRepository(
                    conn, principal.tenant, lock_timeout_ms=self._quota_lock_timeout_ms
                )
                day = now.astimezone(UTC).date()
                if self._quota_defaults is not None:
                    # Same lock and same counter as batch ingestion: a run made here counts there.
                    state = await quotas.state(principal.project_id, day, lock=True)
                    known = await RunRepository(conn, principal.tenant).owners([run_id])
                    if state is not None and run_id not in known:
                        limit = state.limits(self._quota_defaults).runs_per_day
                        if state.runs_today >= limit:
                            raise quota_exceeded(
                                Quota.RUNS_PER_DAY, limit=limit, used=state.runs_today
                            )
                created, owner = await RunRepository(conn, principal.tenant).create_queued(
                    run_id=run_id,
                    project_id=principal.project_id,
                    trace_id=trace_id,
                    name=request.name,
                    agent_slug=request.agent_id,
                    metadata=request.metadata,
                    now=now,
                )
                if created and self._quota_defaults is not None:
                    await quotas.add_usage(principal.project_id, day, runs=1, events=0)
                if owner != principal.project_id:
                    raise AppError(
                        "RUN_PROJECT_MISMATCH",
                        "That run id belongs to another project.",
                        category=ErrorCategory.CONFLICT,
                        status_code=409,
                    )
                record = await RunQueries(conn, principal.tenant, principal.project_id).get(run_id)
        except Exception as exc:
            if is_lock_timeout(exc):  # the project is busy: retryable, like a batch (ADR-154)
                raise dependency_unavailable(exc) from exc
            raise
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
        q: str | None = None,
        request: Request | None = None,
        gate: SearchGate | None = None,
    ) -> RunPage:
        """The run listing. With `q` (the search grammar, §152) the listing is a search; without
        it, or with an empty one, the statements are exactly those of the plain listing."""
        query = None
        compiled: CompiledSearch | None = None
        if q is not None:
            try:
                query = parse(q, self._clock)
            except QueryError as error:
                raise query_invalid(error) from None
            compiled = compile_query(query, principal)
            if compiled.refused:
                # Before any statement: the refusal never depends on stored data (ADR-101).
                await self._refuse_content_field(request, principal, compiled)
            if not query.terms:
                compiled = None
        after_key = self._run_cursor(cursor)
        after = _aware(started_after, "started_after")
        before = _aware(started_before, "started_before")
        counts: FacetCounts | None = None
        skipped = None
        if compiled is None:
            async with self._engine.connect() as conn:
                scope_project = await authorise_project(conn, principal, project_id)
                queries = RunQueries(conn, principal.tenant, principal.project_id)
                records = await queries.page(
                    statuses=statuses,
                    agent_slug=agent_id,
                    started_after=after,
                    started_before=before,
                    project_id=scope_project,
                    ascending=ascending,
                    limit=limit + 1,
                    after=after_key,
                )
                page = records[:limit]
                states = await queries.summary_states([r.id for r in page])
                badges = await FindingsRepository(conn, principal.tenant).badges(
                    [r.id for r in page]
                )
        else:
            assert gate is not None and query is not None
            async with (
                gate.request(principal.workspace_id, principal.actor_id) as budget,
                budget.connect(self._engine) as conn,
            ):
                scope_project = await authorise_project(conn, principal, project_id)
                queries = RunQueries(conn, principal.tenant, principal.project_id)
                records = await queries.page(
                    statuses=statuses,
                    agent_slug=agent_id,
                    started_after=after,
                    started_before=before,
                    project_id=scope_project,
                    ascending=ascending,
                    limit=limit + 1,
                    after=after_key,
                    search=compiled,
                    budget=budget,
                )
                page = records[:limit]
                states = await queries.summary_states([r.id for r in page])
                badges = await FindingsRepository(conn, principal.tenant).badges(
                    [r.id for r in page]
                )
                # Last, and optional: a cancelled facets statement aborts the transaction, which
                # is fine because nothing runs after it.
                if budget.allows_facets():
                    try:
                        counts = await queries.facets_and_unindexed(
                            compiled,
                            statuses=statuses,
                            agent_slug=agent_id,
                            started_after=after,
                            started_before=before,
                            project_id=scope_project,
                            budget=budget,
                        )
                    except AppError as error:
                        if error.code != "SEARCH_TIMEOUT":
                            raise
                skipped = None if counts else "budget"
        next_cursor = None
        if len(records) > limit:
            last = page[-1]
            next_cursor = cursors.encode(
                cursors.Cursor("runs", [last.started_at.isoformat(), str(last.id)])
            )
        return RunPage(
            items=[
                _run_out(
                    r,
                    states[r.id],
                    _badge(badges.get(r.id)),
                    _security_badge(badges.get(r.id)),
                    with_agents=False,
                    search=compiled,
                )
                for r in page
            ],
            next_cursor=next_cursor,
            query=query_out(
                query, content_allowed=holds(principal, actions.PAYLOAD_READ), counts=counts
            )
            if query is not None
            else None,
            facets=facets_out(counts) if counts else None,
            facets_skipped=skipped,  # type: ignore[arg-type]
        )

    @staticmethod
    async def _refuse_content_field(
        request: Request | None, principal: Principal, compiled: CompiledSearch
    ) -> None:
        """403 through the one audited authorisation path, naming the canonical field."""
        denied: PermissionDenied | None = None
        try:
            assert request is not None
            await authorize_audited(request, principal, actions.PAYLOAD_READ)
        except PermissionDenied as error:
            denied = error
        denied = denied or PermissionDenied(principal, actions.PAYLOAD_READ)
        denied.details["field"] = compiled.content_fields[0]  # the catalogue's name, not the typed
        raise denied

    async def get_run(self, principal: Principal, run_id: str) -> RunOut:
        async with self._engine.connect() as conn:
            record = await self._visible_run(conn, principal, run_id)
            states = await RunQueries(conn, principal.tenant, principal.project_id).summary_states(
                [record.id]
            )
            badges = await FindingsRepository(conn, principal.tenant).badges([record.id])
        return _run_out(
            record,
            states[record.id],
            _badge(badges.get(record.id)),
            _security_badge(badges.get(record.id)),
        )

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
            costs = await self._costs(conn, principal, record.id, [e for e, _ in page])
        next_cursor = None
        if len(found) > limit:
            last = page[-1][0]
            next_cursor = cursors.encode(
                cursors.Cursor("events", _event_key(last, mode), mode=mode)
            )
        reads = holds(principal, actions.PAYLOAD_READ)
        return EventPage(
            items=[
                event_out(
                    e, has, with_payload=False, reads_content=reads, cost=costs.get(e.event_id)
                )
                for e, has in page
            ],
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
            costs = (
                await self._costs(conn, principal, record.id, [event]) if event is not None else {}
            )
        if event is None:
            raise _not_found("EVENT_NOT_FOUND", "Event")
        return event_out(
            event,
            event.payload is not None,
            with_payload=True,
            reads_content=holds(principal, actions.PAYLOAD_READ),
            cost=costs.get(event.event_id),
        )

    @staticmethod
    async def _costs(
        conn: AsyncConnection, principal: Principal, run_id: uuid.UUID, events: list[Event]
    ) -> dict[str, EventCost]:
        """Stored cost lines of the model calls among `events`, by public event id: one batched
        lookup per page, workspace-scoped, the same for every role (metadata only)."""
        calls = {
            to_uuid(e.event_id): e.event_id
            for e in events
            if e.event_type == "llm.request.completed"
        }
        found = await CostRepository(conn, principal.tenant).event_costs(run_id, list(calls))
        return {calls[u]: cost for u, cost in found.items()}

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
        return SpanPage(
            items=[
                _span_out(s, reads_content=holds(principal, actions.PAYLOAD_READ)) for s in page
            ],
            next_cursor=next_cursor,
        )

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

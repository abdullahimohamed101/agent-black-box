"""Run query and creation endpoints (spec §101)."""

from datetime import datetime
from typing import Annotated, Any, Literal

from abb_event_schema.ids import IdKind, id_pattern
from abb_event_schema.registry import EVENT_TYPE_PATTERN
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import StringConstraints

from abb_api.authz import actions
from abb_api.authz.dependencies import require
from abb_api.authz.principal import Principal
from abb_api.core.errors import ErrorEnvelope
from abb_api.ingestion.service import project_key_required
from abb_api.runs.schemas import (
    CreateRunRequest,
    EventOut,
    EventPage,
    RunOut,
    RunPage,
    RunStatusName,
    SpanPage,
)
from abb_api.runs.service import RunService

router = APIRouter(prefix="/v1/runs", tags=["runs"])

# Query values reach SQL: constrain them to what can match, so odd bytes never get that far.
EventTypeParam = Annotated[str, StringConstraints(pattern=EVENT_TYPE_PATTERN, max_length=64)]
EventStatusName = Literal["success", "error", "timeout", "cancelled", "blocked"]

Reader = Annotated[Principal, Depends(require(actions.RUN_READ))]
Writer = Annotated[Principal, Depends(require(actions.RUN_WRITE))]

_ERROR_TEXT: dict[int | str, dict[str, Any]] = {
    401: {"description": "Missing, malformed, unknown, revoked or expired API key."},
    403: {"description": "The key lacks the required scope."},
    404: {"description": "Not found, or not visible to this key (never reveals other tenants)."},
    422: {"description": "A parameter or the request body is invalid."},
    503: {"description": "A dependency is unavailable; retry with backoff."},
}
_ERRORS: dict[int | str, dict[str, Any]] = {
    status: {**spec, "model": ErrorEnvelope} for status, spec in _ERROR_TEXT.items()
}


def _service(request: Request) -> RunService:
    service: RunService = request.app.state.runs
    return service


@router.post(
    "",
    response_model=RunOut,
    status_code=201,
    responses={
        **_ERRORS,
        200: {"description": "The run already existed (idempotent)."},
        409: {"description": "That run id belongs to another project.", "model": ErrorEnvelope},
    },
    summary="Create a run explicitly",
    description=(
        "Optional: the first event for an unseen run id creates the run anyway. Idempotent on "
        "`run_id`. Needs a project-bound key with `events:write`."
    ),
)
async def create_run(
    body: CreateRunRequest, request: Request, response: Response, principal: Writer
) -> RunOut:
    if principal.project_id is None:
        raise project_key_required()
    run, created = await _service(request).create_run(principal, body)
    response.status_code = 201 if created else 200
    return run


@router.get(
    "",
    response_model=RunPage,
    responses=_ERRORS,
    summary="List runs",
    description="Newest first by default. Page with `next_cursor`; filters combine with AND.",
)
async def list_runs(
    request: Request,
    principal: Reader,
    status: Annotated[list[RunStatusName] | None, Query()] = None,
    agent_id: Annotated[str | None, Query(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")] = None,
    project_id: Annotated[str | None, Query(pattern=id_pattern(IdKind.PROJECT))] = None,
    started_after: Annotated[datetime | None, Query()] = None,
    started_before: Annotated[datetime | None, Query()] = None,
    sort: Literal["-started_at", "started_at"] = "-started_at",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> RunPage:
    return await _service(request).list_runs(
        principal,
        statuses=list(status or []),
        agent_id=agent_id,
        project_id=project_id,
        started_after=started_after,
        started_before=started_before,
        ascending=sort == "started_at",
        limit=limit,
        cursor=cursor,
    )


@router.get("/{run_id}", response_model=RunOut, responses=_ERRORS, summary="Get a run")
async def get_run(run_id: str, request: Request, principal: Reader) -> RunOut:
    return await _service(request).get_run(principal, run_id)


@router.get(
    "/{run_id}/events",
    response_model=EventPage,
    responses={
        **_ERRORS,
        409: {
            "description": "CURSOR_STALE: restart paging from the start.",
            "model": ErrorEnvelope,
        },
    },
    summary="List a run's events in canonical order",
    description=(
        "Ordered by sequence when every event of the run has one, otherwise by time "
        "(`ordering_mode`). Payloads are omitted; fetch one event for its payload."
    ),
)
async def list_events(
    run_id: str,
    request: Request,
    principal: Reader,
    event_type: Annotated[list[EventTypeParam] | None, Query(max_length=32)] = None,
    status: Annotated[list[EventStatusName] | None, Query(max_length=8)] = None,
    span_id: Annotated[str | None, Query(pattern=id_pattern(IdKind.SPAN))] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> EventPage:
    return await _service(request).list_events(
        principal,
        run_id,
        event_types=list(event_type or []),
        statuses=list(status or []),
        span_id=span_id,
        limit=limit,
        cursor=cursor,
    )


@router.get(
    "/{run_id}/events/{event_id}",
    response_model=EventOut,
    responses=_ERRORS,
    summary="Get one event, including its inline payload",
)
async def get_event(run_id: str, event_id: str, request: Request, principal: Reader) -> EventOut:
    return await _service(request).get_event(principal, run_id, event_id)


@router.get(
    "/{run_id}/spans",
    response_model=SpanPage,
    responses=_ERRORS,
    summary="List a run's spans",
    description="Derived from events; an empty list while the run is still `processing`.",
)
async def list_spans(
    run_id: str,
    request: Request,
    principal: Reader,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> SpanPage:
    return await _service(request).list_spans(principal, run_id, limit=limit, cursor=cursor)

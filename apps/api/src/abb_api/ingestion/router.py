"""POST /v1/events and POST /v1/events/batch (spec §71)."""

import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from abb_api.auth import scopes
from abb_api.auth.dependencies import require_principal
from abb_api.core.errors import AppError, ErrorCategory
from abb_api.core.request_context import get_request_id
from abb_api.ingestion.body import decode_body, read_body, require_json_content_type
from abb_api.ingestion.schemas import BatchResponse, EventResponse
from abb_api.ingestion.service import IngestionService
from abb_api.tenancy import Principal

router = APIRouter(prefix="/v1", tags=["ingestion"])

Writer = Annotated[Principal, Depends(require_principal(scopes.EVENTS_WRITE))]

_ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"description": "Malformed batch or unsupported schema version."},
    401: {"description": "Missing, malformed, unknown, revoked or expired API key."},
    403: {"description": "The key lacks `events:write` or is not bound to a project."},
    413: {"description": "Body (compressed or decompressed) or event too large."},
    415: {"description": "Not application/json, or an unsupported Content-Encoding."},
    422: {"description": "The event is invalid (single-event endpoint)."},
    429: {"description": "Rate limit exceeded; honour `Retry-After`."},
    503: {"description": "A dependency is unavailable; retry with backoff."},
}


async def _document(request: Request) -> bytes:
    settings = request.app.state.settings
    require_json_content_type(request)
    raw = await read_body(request, settings.ingest_max_body_bytes)
    return decode_body(raw, request.headers.get("content-encoding"), settings.ingest_max_body_bytes)


@router.post(
    "/events/batch",
    status_code=202,
    response_model=BatchResponse,
    responses=_ERRORS,
    summary="Ingest a batch of events",
    description=(
        "Accepts `{batch_id?, sent_at?, events: [...]}` (at most 1000 events, 5 MiB, optionally "
        "gzip). `202` means the valid events are committed. Invalid events are reported per "
        "event in `errors` and do not affect the others. Retrying a batch is safe."
    ),
)
async def ingest_batch(request: Request, principal: Writer) -> BatchResponse:
    service: IngestionService = request.app.state.ingestion
    return await service.ingest_batch(principal, await _document(request))


@router.post(
    "/events",
    status_code=202,
    response_model=EventResponse,
    responses=_ERRORS,
    summary="Ingest a single event",
)
async def ingest_event(request: Request, principal: Writer) -> EventResponse | JSONResponse:
    service: IngestionService = request.app.state.ingestion
    document = await _document(request)
    try:
        event = json.loads(document)
    except (ValueError, RecursionError):
        raise AppError(
            "EVENT_INVALID",
            "The body is not valid JSON.",
            category=ErrorCategory.VALIDATION,
            status_code=422,
        ) from None
    result = await service.ingest_batch(principal, json.dumps({"events": [event]}).encode("utf-8"))
    if result.errors:
        failure = result.errors[0]
        status = {
            "EVENT_TOO_LARGE": (413, "PAYLOAD_TOO_LARGE"),
            "EVENT_SCHEMA_UNSUPPORTED": (400, "EVENT_SCHEMA_UNSUPPORTED"),
            "RUN_PROJECT_MISMATCH": (409, "RUN_PROJECT_MISMATCH"),
        }.get(failure.code, (422, "EVENT_INVALID"))
        raise AppError(
            status[1],
            "The event was not accepted.",
            category=ErrorCategory.CONFLICT if status[0] == 409 else ErrorCategory.VALIDATION,
            status_code=status[0],
            details={"issues": [i.model_dump() for i in failure.issues]},
        )
    outcome: Literal["accepted", "duplicate", "conflict"] = (
        "accepted" if result.accepted else "duplicate" if result.duplicates else "conflict"
    )
    return EventResponse(
        event_id=event["event_id"],
        status=outcome,
        server_time=result.server_time,
        request_id=get_request_id(),
    )

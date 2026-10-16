"""POST /v1/events and POST /v1/events/batch (spec §71)."""

import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from abb_api.authz import actions
from abb_api.authz.dependencies import require
from abb_api.authz.principal import Principal
from abb_api.core.errors import AppError, ErrorCategory, ErrorEnvelope
from abb_api.core.request_context import get_request_id
from abb_api.ingestion.body import decode_body, read_body, require_json_content_type
from abb_api.ingestion.observe import IngestObserver
from abb_api.ingestion.schemas import BatchResponse, EventResponse
from abb_api.ingestion.service import IngestionService
from abb_api.quotas.model import Quota, quota_exceeded

router = APIRouter(prefix="/v1", tags=["ingestion"])

Writer = Annotated[Principal, Depends(require(actions.EVENT_WRITE))]

_ERROR_TEXT: dict[int | str, dict[str, Any]] = {
    400: {"description": "Malformed batch or unsupported schema version."},
    401: {"description": "Missing, malformed, unknown, revoked or expired API key."},
    403: {"description": "The key lacks `events:write` or is not bound to a project."},
    413: {
        "description": (
            "Body (compressed or decompressed) or event too large; or, on the single-event "
            "endpoint, `QUOTA_EXCEEDED` (the batch endpoint reports quotas per event instead)."
        )
    },
    415: {"description": "Not application/json, or an unsupported Content-Encoding."},
    422: {"description": "The event is invalid (single-event endpoint)."},
    429: {"description": "Rate limit exceeded; honour `Retry-After`."},
    503: {"description": "A dependency is unavailable; retry with backoff."},
}
_ERRORS: dict[int | str, dict[str, Any]] = {
    status: {**spec, "model": ErrorEnvelope} for status, spec in _ERROR_TEXT.items()
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
        "event in `errors` and do not affect the others. A project at one of its quotas (distinct "
        "agents, runs per day, events per day) has the excess events refused per event with "
        "code `QUOTA_EXCEEDED` and the closed `quota` name; events of runs and agents it "
        "already has, and replays, are unaffected. Retrying a batch is safe."
    ),
)
async def ingest_batch(request: Request, principal: Writer) -> BatchResponse:
    service: IngestionService = request.app.state.ingestion
    with IngestObserver(request) as observed:
        document = await _document(request)
        response = await service.ingest_batch(principal, document)
        observed.committed(response, len(document))  # after the commit inside the service
    return response


@router.post(
    "/events",
    status_code=202,
    response_model=EventResponse,
    responses=_ERRORS,
    summary="Ingest a single event",
)
async def ingest_event(request: Request, principal: Writer) -> EventResponse | JSONResponse:
    service: IngestionService = request.app.state.ingestion
    with IngestObserver(request) as observed:
        return await _ingest_one(request, principal, service, observed)


async def _ingest_one(
    request: Request, principal: Principal, service: IngestionService, observed: IngestObserver
) -> EventResponse | JSONResponse:
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
        # The event was refused: the endpoint answers 4xx, so this request is not an ack.
        observed.events_refused(result.errors, count_quota=False)
        failure = result.errors[0]
        if failure.quota is not None:
            raise quota_exceeded(Quota(failure.quota))
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
    observed.committed(result, len(document))
    outcome: Literal["accepted", "duplicate", "conflict"] = (
        "accepted" if result.accepted else "duplicate" if result.duplicates else "conflict"
    )
    return EventResponse(
        event_id=event["event_id"],
        status=outcome,
        server_time=result.server_time,
        request_id=get_request_id(),
    )

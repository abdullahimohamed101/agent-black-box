"""GET /v1/runs/{run_id}/stream (spec §76.1)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from abb_api.auth import scopes
from abb_api.auth.dependencies import require_principal
from abb_api.core.errors import ErrorEnvelope
from abb_api.streaming.service import StreamService
from abb_api.streaming.sse import SseResponse
from abb_api.tenancy import Principal

router = APIRouter(prefix="/v1/runs", tags=["streams"])

Reader = Annotated[Principal, Depends(require_principal(scopes.RUNS_READ))]

_ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"description": text, "model": ErrorEnvelope}
    for status, text in {
        401: "Missing, malformed, unknown, revoked or expired API key.",
        403: "The key lacks `runs:read`.",
        404: "Not found, or not visible to this key.",
        422: "A parameter is invalid.",
        429: "STREAM_LIMIT: too many open streams for this server or key; honour Retry-After.",
        503: "A dependency is unavailable; retry with backoff.",
    }.items()
}

_DESCRIPTION = """\
Live events of one run as Server-Sent Events.

Messages: `event: trace_event` (`id:` is the event id, `data:` an event without payload, as in
the list endpoint), `event: run_end` (the run finished and nothing arrived for a while: do not
reconnect) and `event: error` (reconnect). `: keepalive` comments keep idle connections open. The
server closes a stream after its maximum lifetime; reconnect with `Last-Event-ID`.

Resume with the `Last-Event-ID` header (browsers send it on reconnect) or `last_event_id` for the
first connection. The server re-sends a short window of events already seen: de-duplicate by event
id. Without either, the stream starts at the beginning of the run. Payloads are never streamed.
"""


@router.get(
    "/{run_id}/stream",
    responses={
        **_ERRORS,
        200: {"description": "An event stream.", "content": {"text/event-stream": {}}},
    },
    response_class=Response,
    summary="Stream a run's events live (SSE)",
    description=_DESCRIPTION,
)
async def stream_run(
    run_id: str,
    request: Request,
    principal: Reader,
    last_event_id_header: Annotated[
        str | None, Header(alias="Last-Event-ID", max_length=64)
    ] = None,
    last_event_id: Annotated[str | None, Query(max_length=64)] = None,
) -> Response:
    service: StreamService = request.app.state.streams
    opened = await service.open(principal, run_id, last_event_id_header or last_event_id)
    return SseResponse(
        service.frames(opened),
        write_timeout=request.app.state.settings.stream_write_timeout_seconds,
        on_close=opened.lease.release,
    )

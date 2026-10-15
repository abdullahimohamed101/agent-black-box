"""`/v1/auth/sessions`: the signed-in person's own sessions (KI-068). People only, any role."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response

from abb_api.auth import sessions as service
from abb_api.auth.service import SessionContext
from abb_api.auth.sessions import RevokedSessions, SessionList
from abb_api.authz.dependencies import require_user
from abb_api.core.errors import ErrorEnvelope

router = APIRouter(tags=["auth"])

Signed = Annotated[SessionContext, Depends(require_user())]

_ERRORS: dict[int | str, dict[str, Any]] = {
    code: {"description": text, "model": ErrorEnvelope}
    for code, text in {
        401: "No valid session (API keys have none).",
        403: "A cookie-authenticated write came from the wrong origin.",
        404: "No such session of yours.",
        409: "That is the session making the request; sign out to end it.",
        422: "The request is invalid.",
        429: "Too many session changes; honour `Retry-After`.",
    }.items()
}


@router.get(
    "/v1/auth/sessions",
    response_model=SessionList,
    responses=_ERRORS,
    summary="List your sessions",
    description=(
        "The signed-in person's own working sessions, most recently used first, at most 100. "
        "Each carries an opaque handle, timestamps and a coarse device label; never the cookie "
        "value or anything derived from it. `current` marks the session making the request."
    ),
)
async def list_sessions(request: Request, context: Signed) -> SessionList:
    return await service.list_sessions(request, context)


@router.post(
    "/v1/auth/sessions/revoke-others",
    response_model=RevokedSessions,
    responses=_ERRORS,
    summary="Sign out everywhere else",
    description=(
        "Revokes every working session of the caller except the one making the request. "
        "Takes effect at once, including on open streams (within the re-check interval)."
    ),
)
async def revoke_other_sessions(request: Request, context: Signed) -> RevokedSessions:
    return await service.revoke_other_sessions(request, context)


@router.delete(
    "/v1/auth/sessions/{session_id}",
    status_code=204,
    responses=_ERRORS,
    summary="Revoke one of your sessions",
    description=(
        "A session id that is someone else's, unknown or already ended is the same 404. The "
        "session in use is refused with 409 `CURRENT_SESSION`: sign out instead."
    ),
)
async def revoke_session(session_id: str, request: Request, context: Signed) -> Response:
    await service.revoke_session(request, context, session_id)
    return Response(status_code=204)

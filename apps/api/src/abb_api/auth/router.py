"""Sign-in endpoints and `/v1/me` (ADR-060).

The browser reaches these through the web app's `/api/auth/*` handlers, which relay `Location` and
`Set-Cookie`; the API itself never trusts a client address or forwarded header.
"""

from typing import Annotated, Any

from abb_event_schema.ids import IdKind
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.auth import cookies
from abb_api.auth.login import LoginService, auth_not_configured
from abb_api.auth.repository import SessionRepository
from abb_api.auth.service import SessionContext, authenticate_session
from abb_api.authz.dependencies import (
    enforce_same_origin,
    idle_window,
    require_user,
    session_cookie,
)
from abb_api.authz.matrix import role_actions, role_own_actions
from abb_api.core.config import Settings
from abb_api.core.errors import AppError, ErrorEnvelope
from abb_api.ids import public_id
from abb_api.ingestion.ratelimit import RateLimiter
from abb_api.ingestion.service import rate_limited
from abb_api.workspaces.repository import memberships_of

router = APIRouter(tags=["auth"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    code: {"description": text, "model": ErrorEnvelope}
    for code, text in {
        400: "The sign-in could not be completed (state, code or token refused).",
        401: "The session is missing, expired or revoked.",
        403: "A cookie-authenticated write came from the wrong origin.",
        422: "A parameter is invalid.",
        429: "Too many sign-in attempts; honour `Retry-After`.",
        503: "Sign-in is not configured or the identity provider is unavailable.",
    }.items()
}

CurrentUser = Annotated[SessionContext, Depends(require_user())]


def _login_service(request: Request) -> LoginService:
    service: LoginService | None = request.app.state.login
    if service is None:
        raise auth_not_configured()
    return service


def _throttle(request: Request) -> None:
    # Sign-in *starts* only. A callback is bound to the browser's own login cookie and a state it
    # consumes, so it is never behind a pool an anonymous client could drain (security review F1).
    # This is a ceiling that protects the database, not a fairness limit: no client address here.
    limiter: RateLimiter = request.app.state.login_limiter
    wait = limiter.acquire("auth", events=1, bytes_=0)
    if wait is not None:
        raise rate_limited(wait)


@router.get(
    "/v1/auth/login",
    status_code=302,
    responses=_ERRORS,
    summary="Start signing in",
    description=(
        "Redirects to the identity provider. `return_to` is a path inside the application "
        "(`/`, `/w/...` or `/invite/...`)."
    ),
)
async def login(request: Request, return_to: Annotated[str | None, Query()] = None) -> Response:
    settings: Settings = request.app.state.settings
    service = _login_service(request)
    _throttle(request)
    started = await service.start(return_to)
    response = RedirectResponse(started.authorize_url, status_code=302)
    response.headers["Cache-Control"] = "no-store"
    response.headers.append(
        "Set-Cookie",
        cookies.set_cookie(
            settings,
            cookies.LOGIN_COOKIE,
            started.state,
            path=cookies.LOGIN_COOKIE_PATH,
            max_age=cookies.LOGIN_TTL_SECONDS,
        ),
    )
    return response


@router.get(
    "/v1/auth/callback",
    status_code=302,
    responses=_ERRORS,
    summary="Finish signing in",
    description="The identity provider's redirect target. Sets the session cookie.",
)
async def callback(
    request: Request,
    code: Annotated[str | None, Query(max_length=2048)] = None,
    state: Annotated[str | None, Query(max_length=128)] = None,
    error: Annotated[str | None, Query(max_length=200)] = None,
) -> Response:
    settings: Settings = request.app.state.settings
    service = _login_service(request)
    clear = cookies.clear_cookie(settings, cookies.LOGIN_COOKIE, path=cookies.LOGIN_COOKIE_PATH)
    try:
        finished = await service.finish(
            code=code,
            state=state,
            cookie_state=request.cookies.get(cookies.LOGIN_COOKIE),
            idp_error=error is not None,
        )
    except AppError as exc:
        exc.headers["Set-Cookie"] = clear  # the login cookie is spent on every outcome
        exc.headers["Cache-Control"] = "no-store"
        raise
    response = RedirectResponse(finished.return_to, status_code=302)
    response.headers["Cache-Control"] = "no-store"
    response.headers.append("Set-Cookie", clear)
    response.headers.append(
        "Set-Cookie",
        cookies.set_cookie(
            settings,
            cookies.session_cookie_name(settings),
            finished.session_token,
            path="/",
            max_age=settings.session_absolute_hours * 3600,
        ),
    )
    return response


class LogoutOut(BaseModel):
    ok: bool = True


@router.post(
    "/v1/auth/logout",
    response_model=LogoutOut,
    responses=_ERRORS,
    summary="Sign out",
    description=(
        "Revokes the current session and clears the cookie. Always `200` when no session cookie "
        "is valid; a cookie-authenticated call needs the web app's `Origin`."
    ),
)
async def logout(request: Request) -> Response:
    settings: Settings = request.app.state.settings
    engine: AsyncEngine = request.app.state.engine
    token = session_cookie(request)
    if token is not None:
        enforce_same_origin(request)  # no logout by a foreign page
        async with engine.begin() as conn:
            try:
                context = await authenticate_session(
                    conn, token, request.app.state.clock, idle_window(settings)
                )
            except AppError:
                context = None  # already invalid: still a success, and the cookie is cleared
            if context is not None:
                await SessionRepository(conn).revoke(context.session.id, request.app.state.clock())
    response = JSONResponse({"ok": True})
    response.headers["Cache-Control"] = "no-store"
    response.headers.append(
        "Set-Cookie",
        cookies.clear_cookie(settings, cookies.session_cookie_name(settings), path="/"),
    )
    return response


class UserOut(BaseModel):
    id: str
    email: str
    name: str | None


class WorkspaceOut(BaseModel):
    id: str
    slug: str
    name: str


class MembershipOut(BaseModel):
    workspace: WorkspaceOut
    role: str
    permissions: list[str]
    own_permissions: list[str]


class MeOut(BaseModel):
    user: UserOut
    memberships: list[MembershipOut]


@router.get(
    "/v1/me",
    response_model=MeOut,
    responses=_ERRORS,
    summary="Who am I, and where",
    description=(
        "The signed-in user and every workspace they belong to, with their role and the actions "
        "it grants (the web app renders by `permissions`; the API stays the authority)."
    ),
)
async def me(request: Request, context: CurrentUser) -> MeOut:
    engine: AsyncEngine = request.app.state.engine
    async with engine.connect() as conn:
        found = await memberships_of(conn, context.user.id)
    return MeOut(
        user=UserOut(
            id=public_id(IdKind.USER, context.user.id),
            email=context.user.email,
            name=context.user.name,
        ),
        memberships=[
            MembershipOut(
                workspace=WorkspaceOut(
                    id=public_id(IdKind.WORKSPACE, m.workspace_id), slug=m.slug, name=m.name
                ),
                role=m.role,
                permissions=sorted(role_actions(m.role)),
                own_permissions=sorted(role_own_actions(m.role)),
            )
            for m in found
        ],
    )


__all__ = ["router"]

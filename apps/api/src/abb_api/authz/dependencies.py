"""`require(action)`: the FastAPI dependency every protected route declares (ADR-061).

Credentials, in this order: an `Authorization` header is an API key and nothing else is looked at;
otherwise the session cookie is a person; otherwise there is no credential (the uniform key 401).
A session token in a bearer header, or a key in the cookie, therefore never authenticates.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from abb_event_schema.ids import IdKind
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.auth.cookies import session_cookie_name
from abb_api.auth.keys import parse_key
from abb_api.auth.service import (
    KeyRejected,
    SessionContext,
    authenticate,
    authenticate_session,
    session_invalid,
)
from abb_api.auth.throttle import AuthThrottle
from abb_api.authz.audit import authorize_audited
from abb_api.authz.matrix import role_actions, role_own_actions
from abb_api.authz.principal import Principal
from abb_api.core.client_address import client_address
from abb_api.core.config import Settings
from abb_api.core.errors import (
    AppError,
    ErrorCategory,
    dependency_unavailable,
    is_connectivity_error,
)
from abb_api.core.request_context import set_caller
from abb_api.ids import parse_public_id, public_id
from abb_api.tenancy import TenantContext
from abb_api.workspaces.repository import MembershipRepository

logger = logging.getLogger(__name__)

_BEARER = "bearer "
WORKSPACE_HEADER = "x-abb-workspace"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

RequireDependency = Callable[[Request], Awaitable[Principal]]


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header[: len(_BEARER)].lower() != _BEARER:
        return None
    return header[len(_BEARER) :].strip() or None


def workspace_not_found() -> AppError:
    return AppError(
        "WORKSPACE_NOT_FOUND",
        "Workspace not found.",
        category=ErrorCategory.NOT_FOUND,
        status_code=404,
    )


def csrf_rejected() -> AppError:
    return AppError(
        "CSRF_REJECTED",
        "A request authenticated by cookie must come from the web application's origin.",
        category=ErrorCategory.AUTHORIZATION,
        status_code=403,
    )


def enforce_same_origin(request: Request) -> None:
    """Cookie-authenticated writes must carry an `Origin` equal to the web app's (exactly)."""
    if request.method in _SAFE_METHODS:
        return
    settings: Settings = request.app.state.settings
    expected = settings.web_origin_normalised
    origin = request.headers.get("origin")
    if expected is None or origin is None or origin.rstrip("/").lower() != expected:
        raise csrf_rejected()


def session_cookie(request: Request) -> str | None:
    return request.cookies.get(session_cookie_name(request.app.state.settings))


def uses_session(request: Request) -> bool:
    """A cookie is the credential only when no `Authorization` header is present."""
    return "authorization" not in request.headers and session_cookie(request) is not None


def idle_window(settings: Settings) -> timedelta:
    return timedelta(hours=settings.session_idle_hours)


async def _guarded(request: Request, work: Callable[[AsyncConnection], Awaitable[Any]]) -> Any:
    """Run `work` in a transaction; database trouble is a 503, never a 401."""
    try:
        async with request.app.state.engine.begin() as conn:
            return await work(conn)
    except AppError:
        raise
    except Exception as exc:
        if is_connectivity_error(exc):
            logger.warning("authentication unavailable", extra={"error_type": type(exc).__name__})
            raise dependency_unavailable() from exc
        raise


def _bad_key_response(exc: AppError) -> AppError:
    if exc.status_code == 401:
        exc.headers["WWW-Authenticate"] = 'Bearer realm="agent-black-box"'
    return exc


async def _key_principal(request: Request) -> Principal:
    settings: Settings = request.app.state.settings
    throttle: AuthThrottle = request.app.state.auth_throttle
    token = bearer_token(request)
    # Memory only: a throttled request is refused here, before any lookup (ADR-153).
    attempt = throttle.admit(token, client_address(request, trust_proxy=settings.trust_proxy))
    if parse_key(token or "") is None:
        # Missing or malformed: nothing can match, so answer without taking a pooled
        # connection. Such a request is neither counted nor gated (ADR-153, point 4), so
        # it must cost the database nothing either: hardening-e2e measured a pool
        # checkout, ping and commit (about half a transaction) per junk key.
        sampled = throttle.malformed_to_log()
        if sampled is not None:  # one line per window, with the count since the last (P3)
            logger.info(
                "api key rejected",
                extra={
                    "reason": "malformed_or_missing",
                    "key_id": None,
                    "since_last_line": sampled,
                },
            )
        raise _bad_key_response(KeyRejected())
    if attempt is not None and throttle.known_invalid(attempt):
        # Seen invalid moments ago: same 401, no database lookup, still counted as a failure.
        refused = await throttle.failed(attempt, None)
        raise _bad_key_response(refused or KeyRejected())
    try:
        principal: Principal = await _guarded(
            request,
            lambda conn: authenticate(conn, token, request.app.state.clock),
        )
    except AppError as exc:
        if attempt is not None and exc.code == "API_KEY_INVALID":
            workspace = exc.workspace_id if isinstance(exc, KeyRejected) else None
            exc = await throttle.failed(attempt, workspace) or exc
        raise _bad_key_response(exc) from None
    if attempt is not None:
        throttle.succeeded(attempt)
    chosen = request.headers.get(WORKSPACE_HEADER)
    if chosen is not None and chosen != public_id(IdKind.WORKSPACE, principal.workspace_id):
        raise workspace_not_found()  # a key is bound to its workspace; naming another is a 404
    set_caller(
        workspace_id=public_id(IdKind.WORKSPACE, principal.workspace_id),
        actor_id=principal.actor_id,
        **(
            {"project_id": public_id(IdKind.PROJECT, principal.project_id)}
            if principal.project_id
            else {}
        ),
    )
    return principal


async def _load_session(request: Request) -> SessionContext:
    settings: Settings = request.app.state.settings
    context: SessionContext = await _guarded(
        request,
        lambda conn: authenticate_session(
            conn, session_cookie(request), request.app.state.clock, idle_window(settings)
        ),
    )
    return context


async def _user_principal(request: Request) -> Principal:
    context = await _load_session(request)
    enforce_same_origin(request)
    header = request.headers.get(WORKSPACE_HEADER)
    if header is None:
        raise AppError(
            "WORKSPACE_REQUIRED",
            "Choose a workspace with the X-ABB-Workspace header.",
            category=ErrorCategory.VALIDATION,
            status_code=400,
        )
    workspace_id = parse_public_id(IdKind.WORKSPACE, header)
    if workspace_id is None:
        raise workspace_not_found()

    async def membership(conn: AsyncConnection) -> str | None:
        return await MembershipRepository(conn, TenantContext(workspace_id)).role_of(
            context.user.id
        )

    # Read on every request: a removal or a role change takes effect on the next call.
    role = await _guarded(request, membership)
    if role is None:
        raise workspace_not_found()  # not a member looks exactly like no such workspace
    actor_id = f"user:{public_id(IdKind.USER, context.user.id)}"
    set_caller(workspace_id=header, actor_id=actor_id, session_id=str(context.session.id)[:8])
    return Principal(
        kind="user",
        workspace_id=workspace_id,
        project_id=None,
        actions=role_actions(role),
        actor_id=actor_id,
        own_actions=role_own_actions(role),
        user_id=context.user.id,
        session_id=str(context.session.id),
    )


def require(action: str, *, owned: bool = False) -> RequireDependency:
    """Authenticate the caller, then require that they may perform `action`.

    The check runs as a dependency, i.e. before the handler reads a body or looks anything up, so a
    caller without the action learns nothing about which ids exist. With `owned=True`, a caller
    who holds the action only on resources they created (`Own(...)`) passes here and the handler
    must call `authorize_audited(..., resource)` once it has loaded the resource.
    """

    async def dependency(request: Request) -> Principal:
        if uses_session(request):
            principal = await _user_principal(request)
        else:
            principal = await _key_principal(request)
        if owned and action in principal.own_actions:
            return principal
        await authorize_audited(request, principal, action)
        return principal

    # Read by the route-walking completeness test: which action does this route demand?
    dependency.required_action = action  # type: ignore[attr-defined]
    return dependency


def require_user() -> Callable[[Request], Awaitable[SessionContext]]:
    """A signed-in person, in no particular workspace (`/v1/me`)."""

    async def dependency(request: Request) -> SessionContext:
        if not uses_session(request):
            raise session_invalid()
        context = await _load_session(request)
        enforce_same_origin(request)
        set_caller(
            actor_id=f"user:{public_id(IdKind.USER, context.user.id)}",
            session_id=str(context.session.id)[:8],
        )
        return context

    dependency.requires_session = True  # type: ignore[attr-defined]
    return dependency

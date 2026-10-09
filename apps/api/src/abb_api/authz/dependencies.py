"""`require(action)`: the FastAPI dependency every protected route declares (ADR-061)."""

import logging
from collections.abc import Awaitable, Callable

from abb_event_schema.ids import IdKind
from fastapi import Request

from abb_api.auth.service import authenticate
from abb_api.authz.principal import Principal
from abb_api.authz.service import authorize
from abb_api.core.errors import AppError, dependency_unavailable, is_connectivity_error
from abb_api.core.request_context import set_caller
from abb_api.ids import public_id

logger = logging.getLogger(__name__)

_BEARER = "bearer "

RequireDependency = Callable[[Request], Awaitable[Principal]]


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header[: len(_BEARER)].lower() != _BEARER:
        return None
    return header[len(_BEARER) :].strip() or None


def require(action: str) -> RequireDependency:
    """Authenticate the caller, then require that they may perform `action`.

    The check runs as a dependency, i.e. before the handler reads a body or looks anything up, so a
    caller without the action learns nothing about which ids exist.
    """

    async def dependency(request: Request) -> Principal:
        engine = request.app.state.engine
        try:
            async with engine.begin() as conn:
                principal = await authenticate(conn, bearer_token(request), request.app.state.clock)
        except AppError as exc:
            if exc.status_code == 401:
                exc.headers["WWW-Authenticate"] = 'Bearer realm="agent-black-box"'
            raise
        except Exception as exc:
            if is_connectivity_error(exc):
                logger.warning(
                    "authentication unavailable", extra={"error_type": type(exc).__name__}
                )
                raise dependency_unavailable() from exc
            raise
        set_caller(
            workspace_id=public_id(IdKind.WORKSPACE, principal.workspace_id),
            actor_id=principal.actor_id,
            **(
                {"project_id": public_id(IdKind.PROJECT, principal.project_id)}
                if principal.project_id
                else {}
            ),
        )
        authorize(principal, action)
        return principal

    # Read by the route-walking completeness test: which action does this route demand?
    dependency.required_action = action  # type: ignore[attr-defined]
    return dependency

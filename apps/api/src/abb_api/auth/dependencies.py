"""FastAPI dependencies that turn a request into an authenticated Principal."""

import logging
from collections.abc import Awaitable, Callable

from abb_event_schema.ids import IdKind
from fastapi import Request

from abb_api.auth.service import authenticate, require_scope
from abb_api.core.errors import AppError, dependency_unavailable, is_connectivity_error
from abb_api.core.request_context import set_caller
from abb_api.ids import public_id
from abb_api.tenancy import Principal

logger = logging.getLogger(__name__)

_BEARER = "bearer "


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header[: len(_BEARER)].lower() != _BEARER:
        return None
    return header[len(_BEARER) :].strip() or None


def require_principal(scope: str) -> Callable[[Request], Awaitable[Principal]]:
    """A dependency: authenticate the bearer key and require `scope`."""

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
            key_id=principal.key_id,
            **(
                {"project_id": public_id(IdKind.PROJECT, principal.project_id)}
                if principal.project_id
                else {}
            ),
        )
        require_scope(principal, scope)
        return principal

    return dependency

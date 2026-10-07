"""FastAPI dependencies that turn a request into an authenticated Principal."""

import logging
from collections.abc import Awaitable, Callable

from abb_event_schema.ids import IdKind
from fastapi import Request
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError

from abb_api.auth.service import authenticate, invalid_key, require_scope
from abb_api.core.errors import AppError, ErrorCategory
from abb_api.core.request_context import set_caller
from abb_api.ids import public_id
from abb_api.tenancy import Principal

logger = logging.getLogger(__name__)

_BEARER = "bearer "


def dependency_unavailable() -> AppError:
    return AppError(
        "DEPENDENCY_UNAVAILABLE",
        "A required dependency is temporarily unavailable.",
        category=ErrorCategory.DEPENDENCY,
        status_code=503,
        retryable=True,
        headers={"Retry-After": "2"},
    )


def is_connectivity_error(exc: BaseException) -> bool:
    """Database trouble we should report as 503 (retryable), not as a client or server bug."""
    if isinstance(exc, (OperationalError, InterfaceError, OSError, TimeoutError)):
        return True
    return isinstance(exc, DBAPIError) and exc.connection_invalidated


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


__all__ = ["invalid_key", "require_principal"]

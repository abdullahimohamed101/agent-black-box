"""Typed error taxonomy and the one error envelope (spec §101.2, §130)."""

import logging
from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import DataError, DBAPIError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException

from abb_api.core.request_context import get_request_id
from abb_api.db.breaker import BreakerOpen
from abb_api.obs.app_errors import count_app_error

logger = logging.getLogger(__name__)


class ErrorCategory(StrEnum):
    AUTHENTICATION = "AUTHENTICATION"
    AUTHORIZATION = "AUTHORIZATION"
    VALIDATION = "VALIDATION"
    RATE_LIMIT = "RATE_LIMIT"
    DEPENDENCY = "DEPENDENCY"
    TIMEOUT = "TIMEOUT"
    CONFLICT = "CONFLICT"
    NOT_FOUND = "NOT_FOUND"
    POLICY = "POLICY"
    INTERNAL = "INTERNAL"


_HTTP_STATUS_CATEGORY = {
    401: ErrorCategory.AUTHENTICATION,
    403: ErrorCategory.AUTHORIZATION,
    404: ErrorCategory.NOT_FOUND,
    409: ErrorCategory.CONFLICT,
    429: ErrorCategory.RATE_LIMIT,
    504: ErrorCategory.TIMEOUT,
}


class AppError(Exception):
    """An error the API chooses to report. Retryability is explicit, never inferred."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        category: ErrorCategory,
        status_code: int,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.category = category
        self.status_code = status_code
        self.retryable = retryable
        self.details = details or {}
        self.headers = headers or {}


class ErrorBody(BaseModel):
    code: str = Field(description="Stable machine-readable code, e.g. RUN_NOT_FOUND.")
    message: str
    category: ErrorCategory
    retryable: bool = Field(description="Whether retrying the same request may succeed.")
    request_id: str | None = Field(description="Also in the X-Request-ID response header.")
    details: dict[str, Any]


class ErrorEnvelope(BaseModel):
    """The one error shape of every non-2xx response (spec §101.2)."""

    error: ErrorBody


def dependency_unavailable(cause: BaseException | None = None) -> AppError:
    """503 with a fixed body. An open circuit breaker says when its next probe is due."""
    retry_after = cause.retry_after_seconds if isinstance(cause, BreakerOpen) else 2
    return AppError(
        "DEPENDENCY_UNAVAILABLE",
        "A required dependency is temporarily unavailable.",
        category=ErrorCategory.DEPENDENCY,
        status_code=503,
        retryable=True,
        headers={"Retry-After": str(retry_after)},
    )


def database_timeout() -> AppError:
    """The default `statement_timeout` cancelled a statement (routes with their own budget map
    theirs to their own code first). Fixed text: the SQL and its parameters are never shown."""
    return AppError(
        "DATABASE_TIMEOUT",
        "The database took too long to answer. Retry later.",
        category=ErrorCategory.TIMEOUT,
        status_code=504,
        retryable=True,
    )


def is_statement_timeout(exc: BaseException) -> bool:
    """A statement cancelled by `statement_timeout` (SQLSTATE 57014, query_canceled)."""
    if not isinstance(exc, DBAPIError):
        return False
    state = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    return state == "57014"


def is_connectivity_error(exc: BaseException) -> bool:
    """Database trouble we report as 503 (retryable), not as a client or server bug."""
    # OSError covers socket errors and timeouts; PoolTimeoutError is "no free connection in time";
    # BreakerOpen is the circuit breaker refusing a checkout it expects to fail.
    if isinstance(exc, (OperationalError, InterfaceError, OSError, PoolTimeoutError, BreakerOpen)):
        return True
    return isinstance(exc, DBAPIError) and exc.connection_invalidated


def error_response(error: AppError) -> JSONResponse:
    body = {
        "error": {
            "code": error.code,
            "message": error.message,
            "category": error.category.value,
            "retryable": error.retryable,
            "request_id": get_request_id(),
            "details": error.details,
        }
    }
    request_id = get_request_id()
    # Set here too: unhandled-exception responses are produced outside the request-ID middleware.
    headers = dict(error.headers)
    if request_id:
        headers["X-Request-ID"] = request_id
    return JSONResponse(body, status_code=error.status_code, headers=headers or None)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        observability = getattr(request.app.state, "observability", None)
        if observability is not None:  # a refusal that is also a metric (obs/app_errors.py)
            count_app_error(observability.catalogue, exc.code, exc.details)
        return error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return error_response(
            AppError(
                "REQUEST_INVALID",
                "Request validation failed.",
                category=ErrorCategory.VALIDATION,
                status_code=422,
                details={"errors": fields},
            )
        )

    @app.exception_handler(DataError)
    async def _data_error(_: Request, exc: DataError) -> JSONResponse:
        # A value the database cannot store (for example an invalid byte sequence) came from the
        # caller. Validation should have stopped it earlier, so log it loudly (type only: the
        # message would quote the value) and answer 422 instead of a retryable-looking 500.
        logger.error(
            "database rejected a request value", extra={"error_type": type(exc.orig).__name__}
        )
        return error_response(
            AppError(
                "REQUEST_INVALID",
                "A value in the request cannot be stored.",
                category=ErrorCategory.VALIDATION,
                status_code=422,
            )
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        category = _HTTP_STATUS_CATEGORY.get(exc.status_code)
        if category is None:
            category = ErrorCategory.VALIDATION if exc.status_code < 500 else ErrorCategory.INTERNAL
        return error_response(
            AppError(
                "NOT_FOUND" if exc.status_code == 404 else f"HTTP_{exc.status_code}",
                str(exc.detail),
                category=category,
                status_code=exc.status_code,
            )
        )

    def _mapped(exc: Exception) -> JSONResponse | None:
        if is_connectivity_error(exc):  # the database went away mid-request: tell clients to retry
            return error_response(dependency_unavailable(exc))
        if is_statement_timeout(exc):  # the default deadline; never the SQL
            logger.warning("statement timeout", extra={"error_type": type(exc).__name__})
            return error_response(database_timeout())
        return None

    async def _database_failure(_: Request, exc: Exception) -> JSONResponse:
        # Registered for the concrete classes, not for Exception: Starlette runs those in the
        # ExceptionMiddleware, inside the metrics and request-log layers, so the 503 and 504 are
        # recorded with the status the client saw. A handler for Exception runs outside every
        # middleware and the response would count as a 500.
        response = _mapped(exc)
        if response is None:
            raise exc  # any other database error is an internal error: the outer handler's 500
        return response

    for cls in (DBAPIError, OSError, PoolTimeoutError, BreakerOpen):
        app.add_exception_handler(cls, _database_failure)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        mapped = _mapped(exc)  # the same mapping for anything that reaches the outer handler
        if mapped is not None:
            return mapped
        # Never expose internals to clients; the stack trace is logged by the middleware.
        return error_response(
            AppError(
                "INTERNAL_ERROR",
                "An internal error occurred.",
                category=ErrorCategory.INTERNAL,
                status_code=500,
            )
        )

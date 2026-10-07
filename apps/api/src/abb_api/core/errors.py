"""Typed error taxonomy and the one error envelope (spec §101.2, §130)."""

from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from abb_api.core.request_context import get_request_id


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
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.category = category
        self.status_code = status_code
        self.retryable = retryable
        self.details = details or {}


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
    return JSONResponse(body, status_code=error.status_code)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
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

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        category = ErrorCategory.NOT_FOUND if exc.status_code == 404 else ErrorCategory.INTERNAL
        return error_response(
            AppError(
                "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR",
                str(exc.detail),
                category=category,
                status_code=exc.status_code,
            )
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, __: Exception) -> JSONResponse:
        # Never expose internals to clients; the stack trace is logged by the middleware.
        return error_response(
            AppError(
                "INTERNAL_ERROR",
                "An internal error occurred.",
                category=ErrorCategory.INTERNAL,
                status_code=500,
            )
        )

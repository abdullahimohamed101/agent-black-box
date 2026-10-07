"""Liveness and readiness (spec §106). /healthz has no dependencies; /readyz checks the DB."""

import logging
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from abb_api import __version__
from abb_api.core.errors import AppError, ErrorCategory
from abb_api.db import check_database

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str


class ReadyResponse(HealthResponse):
    database: Literal["ok"]


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@router.get("/readyz", response_model=ReadyResponse)
async def readyz(request: Request) -> ReadyResponse:
    try:
        await check_database(request.app.state.engine)
    except Exception as exc:
        logger.warning("readiness check failed", extra={"error_type": type(exc).__name__})
        raise AppError(
            "DEPENDENCY_UNAVAILABLE",
            "Database is not reachable.",
            category=ErrorCategory.DEPENDENCY,
            status_code=503,
            retryable=True,
            details={"dependency": "postgresql"},
        ) from exc
    return ReadyResponse(status="ok", version=__version__, database="ok")

"""Liveness (no dependencies) and readiness (health/readiness.py); spec §106."""

from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from abb_api import __version__
from abb_api.health.readiness import Readiness

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str


class CheckOut(BaseModel):
    status: Literal["ok", "fail", "degraded"]
    reason: str | None = Field(
        description="A fixed reason code (for example `behind`, `breaker_open`), never error text."
    )


class ReadyResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    version: str
    checks: dict[str, CheckOut] = Field(
        description="`database`, `schema`, `artifacts` and `role` fail readiness; `worker` and "
        "`retention` are reported only."
    )


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@router.get(
    "/readyz",
    response_model=ReadyResponse,
    responses={503: {"description": "Not ready: see `checks`.", "model": ReadyResponse}},
)
async def readyz(request: Request, response: Response) -> ReadyResponse:
    readiness: Readiness = request.app.state.readiness
    report = await readiness.evaluate()
    if not report.ready:
        response.status_code = 503
        response.headers["Retry-After"] = "2"
    return ReadyResponse(
        status="ready" if report.ready else "not_ready",
        version=__version__,
        checks={
            name: CheckOut(status=result.status, reason=result.reason)
            for name, result in report.checks.items()
        },
    )

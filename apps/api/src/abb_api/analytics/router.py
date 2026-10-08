"""Analytics endpoints (spec §22-24, §61.2). Read-only, `runs:read`, tenant- and project-scoped."""

from datetime import datetime
from typing import Annotated, Any

from abb_event_schema.ids import IdKind, id_pattern
from fastapi import APIRouter, Depends, Query, Request

from abb_api.analytics.schemas import CostReport, PerformanceReport, ReliabilityReport, Summary
from abb_api.analytics.service import AnalyticsService
from abb_api.analytics.store import DEFAULT_TOP, MAX_TOP, AnalyticsScope
from abb_api.auth import scopes
from abb_api.auth.dependencies import require_principal
from abb_api.core.errors import ErrorEnvelope
from abb_api.tenancy import Principal

router = APIRouter(prefix="/v1/analytics", tags=["analytics"])
Reader = Annotated[Principal, Depends(require_principal(scopes.RUNS_READ))]

_ERROR_TEXT = {
    401: "Missing, malformed, unknown, revoked or expired API key.",
    403: "The key lacks `runs:read`.",
    404: "Project not found or not visible to this key.",
    422: "A parameter is invalid (for example a window over 92 days).",
    503: "The query timed out or a dependency is unavailable; retry.",
}
_ERRORS: dict[int | str, dict[str, Any]] = {
    code: {"description": text, "model": ErrorEnvelope} for code, text in _ERROR_TEXT.items()
}
_WINDOW = (
    "Runs are placed in the window by `started_at`. Windows are whole UTC days: `from` rounds "
    "down and `to` rounds up. Default: the last 7 days; at most 92. Figures come from daily "
    "rollups that lag changes by up to about a minute. Percentiles are approximate (about 10%). "
    "A project-bound key is confined to its project."
)

ProjectParam = Annotated[str | None, Query(pattern=id_pattern(IdKind.PROJECT))]
FromParam = Annotated[datetime | None, Query(alias="from")]
ToParam = Annotated[datetime | None, Query(alias="to")]
TopParam = Annotated[int, Query(ge=1, le=MAX_TOP, description="Groups listed before `other`.")]


def _service(request: Request) -> AnalyticsService:
    service: AnalyticsService = request.app.state.analytics
    return service


async def _scope(
    request: Request,
    principal: Principal,
    project_id: str | None,
    start: datetime | None,
    end: datetime | None,
) -> AnalyticsScope:
    return await _service(request).scope(principal, project_id=project_id, start=start, end=end)


@router.get(
    "/summary",
    response_model=Summary,
    responses=_ERRORS,
    summary="Headline figures for a window",
    description="Run counts and rates, cost, run latency, behaviour averages. " + _WINDOW,
)
async def summary(
    request: Request,
    principal: Reader,
    project_id: ProjectParam = None,
    start: FromParam = None,
    end: ToParam = None,
) -> Summary:
    scope = await _scope(request, principal, project_id, start, end)
    return await _service(request).summary(scope)


@router.get(
    "/cost",
    response_model=CostReport,
    responses=_ERRORS,
    summary="Where the money went",
    description=(
        "Cost per run, per successful run, by day, agent, model, project, the retry breakdown "
        "(spec §24) and the most expensive runs. " + _WINDOW
    ),
)
async def cost(
    request: Request,
    principal: Reader,
    project_id: ProjectParam = None,
    start: FromParam = None,
    end: ToParam = None,
    top: TopParam = DEFAULT_TOP,
) -> CostReport:
    scope = await _scope(request, principal, project_id, start, end)
    return await _service(request).cost(scope, top)


@router.get(
    "/reliability",
    response_model=ReliabilityReport,
    responses=_ERRORS,
    summary="Failure trend, tool success and retry-heavy runs",
    description=_WINDOW,
)
async def reliability(
    request: Request,
    principal: Reader,
    project_id: ProjectParam = None,
    start: FromParam = None,
    end: ToParam = None,
    top: TopParam = DEFAULT_TOP,
) -> ReliabilityReport:
    scope = await _scope(request, principal, project_id, start, end)
    return await _service(request).reliability(scope, top)


@router.get(
    "/performance",
    response_model=PerformanceReport,
    responses=_ERRORS,
    summary="Latency percentiles and the slowest operations",
    description=(
        "p50/p95 for runs, model calls and tools, and the slowest operations. Only tool and model "
        "spans are listed by name; other kinds are grouped by kind. " + _WINDOW
    ),
)
async def performance(
    request: Request,
    principal: Reader,
    project_id: ProjectParam = None,
    start: FromParam = None,
    end: ToParam = None,
    top: TopParam = DEFAULT_TOP,
) -> PerformanceReport:
    scope = await _scope(request, principal, project_id, start, end)
    return await _service(request).performance(scope, top)

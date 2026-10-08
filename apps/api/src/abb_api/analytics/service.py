"""Analytics use cases: turn a request and a principal into an authorised `AnalyticsScope`."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.analytics.schemas import CostReport, PerformanceReport, ReliabilityReport, Summary
from abb_api.analytics.store import (
    DEFAULT_WINDOW_DAYS,
    MAX_WINDOW_DAYS,
    AnalyticsScope,
    AnalyticsStore,
)
from abb_api.clock import Clock
from abb_api.core.errors import AppError, ErrorCategory
from abb_api.projects.access import authorise_project
from abb_api.tenancy import Principal


def _invalid(message: str) -> AppError:
    return AppError("INVALID_WINDOW", message, category=ErrorCategory.VALIDATION, status_code=422)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class AnalyticsService:
    def __init__(self, engine: AsyncEngine, store: AnalyticsStore, clock: Clock) -> None:
        self._engine = engine
        self._store = store
        self._clock = clock

    async def scope(
        self,
        principal: Principal,
        *,
        project_id: str | None,
        agent_id: str | None,
        start: datetime | None,
        end: datetime | None,
    ) -> AnalyticsScope:
        end_at = _utc(end) if end else self._clock()
        start_at = _utc(start) if start else end_at - timedelta(days=DEFAULT_WINDOW_DAYS)
        if start_at >= end_at:
            raise _invalid("`from` must be earlier than `to`.")
        if end_at - start_at > timedelta(days=MAX_WINDOW_DAYS):
            raise _invalid(f"The window may not exceed {MAX_WINDOW_DAYS} days.")
        async with self._engine.connect() as conn:
            project = await authorise_project(conn, principal, project_id)
        return AnalyticsScope(
            tenant=principal.tenant,
            start=start_at,
            end=end_at,
            project_id=project,
            agent_slug=agent_id,
        )

    async def summary(self, scope: AnalyticsScope) -> Summary:
        return await self._store.summary(scope)

    async def cost(self, scope: AnalyticsScope, top: int) -> CostReport:
        return await self._store.cost(scope, top=top)

    async def reliability(self, scope: AnalyticsScope, top: int) -> ReliabilityReport:
        return await self._store.reliability(scope, top=top)

    async def performance(self, scope: AnalyticsScope, top: int) -> PerformanceReport:
        return await self._store.performance(scope, top=top)

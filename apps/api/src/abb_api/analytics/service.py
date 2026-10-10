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
from abb_api.authz.principal import Principal
from abb_api.clock import Clock
from abb_api.core.errors import AppError, ErrorCategory
from abb_api.projects.access import authorise_project


def _invalid(message: str) -> AppError:
    return AppError("INVALID_WINDOW", message, category=ErrorCategory.VALIDATION, status_code=422)


MIN_YEAR, MAX_YEAR = 2000, 2100


def _utc(value: datetime) -> datetime:
    """UTC time within the supported years; anything else (or an overflow) is a 422, never a 500."""
    try:
        moment = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    except OverflowError:
        raise _invalid(f"Times must be between the years {MIN_YEAR} and {MAX_YEAR}.") from None
    if not MIN_YEAR <= moment.year <= MAX_YEAR:
        raise _invalid(f"Times must be between the years {MIN_YEAR} and {MAX_YEAR}.")
    return moment


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
        start: datetime | None,
        end: datetime | None,
    ) -> AnalyticsScope:
        """Whole UTC days: start rounds down, end rounds up, so a window never shrinks."""
        now = self._clock().astimezone(UTC)
        end_at = _utc(end) if end else now
        start_at = _utc(start) if start else end_at - timedelta(days=DEFAULT_WINDOW_DAYS - 1)
        if start_at >= end_at:
            raise _invalid("`from` must be earlier than `to`.")
        start_day = start_at.date()
        end_day = (end_at - timedelta(microseconds=1)).date() + timedelta(days=1)
        if (end_day - start_day).days > MAX_WINDOW_DAYS:
            raise _invalid(f"The window may not exceed {MAX_WINDOW_DAYS} days.")
        async with self._engine.connect() as conn:
            project = await authorise_project(conn, principal, project_id)
        return AnalyticsScope(
            tenant=principal.tenant,
            start_day=start_day,
            end_day=end_day,
            project_id=project,
        )

    async def summary(self, scope: AnalyticsScope) -> Summary:
        return await self._store.summary(scope)

    async def cost(self, scope: AnalyticsScope, top: int) -> CostReport:
        return await self._store.cost(scope, top=top)

    async def reliability(self, scope: AnalyticsScope, top: int) -> ReliabilityReport:
        return await self._store.reliability(scope, top=top)

    async def performance(self, scope: AnalyticsScope, top: int) -> PerformanceReport:
        return await self._store.performance(scope, top=top)

"""Spend per UTC day, read the way the cost rollups define it (`analytics/rollup.cost_select`):
the sum of `cost_calculations.total`, attributed to the day the run STARTED, optionally narrowed to
a project and an agent. Reading `cost_calculations` directly (not the 60-second-lagged rollup)
means a threshold fires as soon as the summary that priced the call has committed; the figure is
the one the cost page shows once its rollup has caught up."""

import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.analytics.rollup import day_start
from abb_api.db import tables as t
from abb_api.tenancy import TenantContext

_L = t.cost_calculations.c


class SpendReader:
    def __init__(self, conn: AsyncConnection, tenant: TenantContext) -> None:
        self._conn = conn
        self._ws = tenant.workspace_id

    def _where(
        self, start: date, end: date, project_id: uuid.UUID | None, agent_slug: str | None
    ) -> list[object]:
        conditions: list[object] = [
            _L.workspace_id == self._ws,
            _L.run_started_at >= day_start(start),
            _L.run_started_at < day_start(end),
        ]
        if project_id is not None:
            conditions.append(_L.project_id == project_id)
        if agent_slug is not None:
            conditions.append(_L.agent_slug == agent_slug)
        return conditions

    async def day_total(
        self, day: date, *, project_id: uuid.UUID | None, agent_slug: str | None
    ) -> Decimal:
        total = await self._conn.scalar(
            select(func.coalesce(func.sum(_L.total), 0)).where(
                *self._where(day, day + timedelta(days=1), project_id, agent_slug)  # type: ignore[arg-type]
            )
        )
        return Decimal(total or 0)

    async def daily_totals(
        self, start: date, end: date, *, project_id: uuid.UUID | None, agent_slug: str | None
    ) -> dict[date, Decimal]:
        """Totals per UTC day in [start, end), days with no spend omitted."""
        day = cast(func.timezone("UTC", _L.run_started_at), Date)
        rows = await self._conn.execute(
            select(day.label("day"), func.sum(_L.total).label("total"))
            .where(*self._where(start, end, project_id, agent_slug))  # type: ignore[arg-type]
            .group_by(day)
        )
        return {r.day: Decimal(r.total) for r in rows}

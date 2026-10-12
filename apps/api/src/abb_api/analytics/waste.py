"""The waste report (Phase 22 D12, ADR-113): what failed runs and retries cost.

Read live from `runs` and `cost_calculations`, the same derived rows the cost engine writes and
the cost report's rollups are built from (INV-2: nothing here is stored, so there is nothing to
rebuild). Every statement starts from the tenant's workspace id (INV-3) and runs inside the
store's read-only, time-limited transaction. "Wasted" never counts a dollar twice: all spend of a
failed, timed-out or blocked run, plus the retry spend of the runs that did not fail.
"""

from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, Numeric, cast, func, literal, literal_column, select
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.analytics.schemas import (
    FailedRunSpend,
    RetrySpend,
    WasteByAgent,
    WasteByModel,
    WasteOther,
    WasteReport,
    Window,
)
from abb_api.analytics.store import AnalyticsScope
from abb_api.db import tables as t
from abb_api.runs.summary import SUMMARY_VERSION

FAILED_STATUSES = ("FAILED", "TIMED_OUT", "BLOCKED")
# First summary version whose first-error facts are complete for every failed status: 5 introduced
# them but left a BLOCKED run without a first error, 6 fixed that (ADR-112).
FIRST_ERROR_VERSION = 6
R = t.runs.c
# A literal, not a bind parameter: the same expression sits in SELECT and GROUP BY (rollup.py).
EMPTY: ColumnElement[str] = literal_column("''")
L = t.cost_calculations.c


def _usd(value: Decimal | float | int | None) -> float:
    return round(float(value or 0), 9)


def _start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def _run_scope(scope: AnalyticsScope) -> list[Any]:
    conditions = [
        R.workspace_id == scope.tenant.workspace_id,
        R.started_at >= _start(scope.start_day),
        R.started_at < _start(scope.end_day),
    ]
    if scope.project_id is not None:
        conditions.append(R.project_id == scope.project_id)
    return conditions


def _other(group_count: int, shown: int, total: Decimal, shown_total: Decimal) -> WasteOther | None:
    """Everything beyond the top groups: response size never depends on how many names exist."""
    if group_count <= shown:
        return None
    return WasteOther(groups=group_count - shown, wasted_usd=_usd(total - shown_total))


async def waste_report(
    conn: AsyncConnection, scope: AnalyticsScope, top: int, window: Window
) -> WasteReport:
    failed = R.status.in_(FAILED_STATUSES)
    after = func.coalesce(cast(R.summary["cost_after_usd"].astext, Numeric(38, 9)), 0)
    known = R.summary_version >= FIRST_ERROR_VERSION

    # Run level: totals and the by-agent breakdown come from the run rows' own cost columns.
    wasted_by_agent = func.coalesce(func.sum(R.cost_usd).filter(failed), 0) + func.coalesce(
        func.sum(R.retry_cost_usd).filter(~failed), 0
    )
    agent = func.coalesce(R.agent_slug, EMPTY)
    agents = (
        await conn.execute(
            select(
                agent.label("agent"),
                func.count().filter(failed).label("failed_runs"),
                func.coalesce(func.sum(R.cost_usd).filter(failed), 0).label("failed_usd"),
                func.coalesce(func.sum(R.retry_cost_usd).filter(~failed), 0).label("retry_usd"),
            )
            .where(*_run_scope(scope))
            .group_by(agent)
            .having(wasted_by_agent > 0)
            .order_by(wasted_by_agent.desc(), agent)
            .limit(top)
        )
    ).all()
    totals = (
        await conn.execute(
            select(
                func.count().filter(failed).label("failed_runs"),
                func.coalesce(func.sum(R.cost_usd), 0).label("total_usd"),
                func.coalesce(func.sum(R.cost_usd).filter(failed), 0).label("failed_usd"),
                func.coalesce(func.sum(after).filter(failed & known), 0).label("after_usd"),
                func.count().filter(failed & ~known).label("after_unknown"),
                func.coalesce(func.sum(R.unpriced_calls).filter(failed), 0).label("unpriced"),
                func.coalesce(func.sum(R.retry_cost_usd), 0).label("retry_total"),
                func.coalesce(func.sum(R.retry_cost_usd).filter(failed), 0).label("retry_failed"),
                func.count()
                .filter((R.summary_version > 0) & (R.summary_version < SUMMARY_VERSION))
                .label("old"),
            ).where(*_run_scope(scope))
        )
    ).one()
    agent_groups = (
        await conn.execute(
            select(func.count()).select_from(
                select(agent)
                .where(*_run_scope(scope))
                .group_by(agent)
                .having(wasted_by_agent > 0)
                .subquery()
            )
        )
    ).scalar_one()

    # Call level: the by-model breakdown joins each cost line to its run's status.
    joined = (L.workspace_id == R.workspace_id) & (L.run_id == R.id)
    retried = L.is_retry & ~failed
    model_waste = func.coalesce(func.sum(L.total).filter(failed | retried), 0)
    models_where = [*_run_scope(scope), failed | L.is_retry]
    models = (
        await conn.execute(
            select(
                func.coalesce(L.provider, literal("")).label("provider"),
                func.coalesce(L.model, literal("")).label("model"),
                func.coalesce(func.sum(L.total).filter(failed), 0).label("failed_usd"),
                func.coalesce(func.sum(L.total).filter(retried), 0).label("retry_usd"),
                func.count().label("calls"),
                func.count().filter(L.source == "unpriced").label("unpriced"),
            )
            .select_from(t.cost_calculations.join(t.runs, joined))
            .where(*models_where)
            .group_by(L.provider, L.model)
            .order_by(model_waste.desc(), L.provider, L.model)
            .limit(top)
        )
    ).all()
    model_totals = (
        await conn.execute(
            select(
                func.coalesce(func.sum(L.total).filter(failed | retried), 0).label("waste"),
            )
            .select_from(t.cost_calculations.join(t.runs, joined))
            .where(*models_where)
        )
    ).one()
    model_groups = (
        await conn.execute(
            select(func.count()).select_from(
                select(L.provider, L.model)
                .select_from(t.cost_calculations.join(t.runs, joined))
                .where(*models_where)
                .group_by(L.provider, L.model)
                .subquery()
            )
        )
    ).scalar_one()

    failed_usd = Decimal(totals.failed_usd)
    retry_other = Decimal(totals.retry_total) - Decimal(totals.retry_failed)
    wasted = failed_usd + retry_other
    total = Decimal(totals.total_usd)

    def agent_row(r: Any) -> WasteByAgent:
        return WasteByAgent(
            agent=r.agent or "(unknown)",
            failed_runs=int(r.failed_runs),
            failed_usd=_usd(r.failed_usd),
            retry_usd=_usd(r.retry_usd),
            wasted_usd=_usd(Decimal(r.failed_usd) + Decimal(r.retry_usd)),
        )

    def model_row(r: Any) -> WasteByModel:
        return WasteByModel(
            provider=r.provider or None,
            model=r.model or None,
            failed_usd=_usd(r.failed_usd),
            retry_usd=_usd(r.retry_usd),
            wasted_usd=_usd(Decimal(r.failed_usd) + Decimal(r.retry_usd)),
            calls=int(r.calls),
            unpriced_calls=int(r.unpriced),
        )

    return WasteReport(
        window=window,
        wasted_usd=_usd(wasted),
        total_usd=_usd(total),
        wasted_share=round(float(wasted / total), 6) if total else None,
        failed=FailedRunSpend(
            runs=int(totals.failed_runs),
            spend_usd=_usd(failed_usd),
            after_first_error_usd=_usd(totals.after_usd),
            after_first_error_unknown_runs=int(totals.after_unknown),
            unpriced_calls=int(totals.unpriced),
        ),
        retries=RetrySpend(
            total_usd=_usd(totals.retry_total),
            in_failed_runs_usd=_usd(totals.retry_failed),
            in_other_runs_usd=_usd(retry_other),
        ),
        by_agent=[agent_row(r) for r in agents],
        by_agent_other=_other(
            int(agent_groups),
            len(agents),
            wasted,
            sum((Decimal(r.failed_usd) + Decimal(r.retry_usd) for r in agents), Decimal(0)),
        ),
        by_model=[model_row(r) for r in models],
        by_model_other=_other(
            int(model_groups),
            len(models),
            Decimal(model_totals.waste),
            sum((Decimal(r.failed_usd) + Decimal(r.retry_usd) for r in models), Decimal(0)),
        ),
        unrebuilt_runs=int(totals.old),
    )

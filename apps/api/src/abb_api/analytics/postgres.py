"""PostgreSQL implementation of `AnalyticsStore` over derived tables only (ADR-041).

Reads `runs`, `spans` and `cost_calculations`, never `events` (INV-7). Every statement is built
from `_runs_where` / `_lines_from` / `_spans_from`, which always start from the tenant's workspace
id, so a scope cannot be applied to one table and forgotten on another. Each call is one read-only
transaction with a statement timeout.
"""

import logging
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from abb_event_schema.ids import IdKind, from_uuid
from sqlalchemy import (
    ColumnElement,
    Numeric,
    Select,
    case,
    cast,
    func,
    literal,
    select,
    text,
)
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from abb_api.analytics.schemas import (
    Behaviour,
    CostByAgent,
    CostByDay,
    CostByModel,
    CostByProject,
    CostBySource,
    CostHeadline,
    CostReport,
    ExpensiveRun,
    FailureDay,
    OtherBucket,
    Percentiles,
    PerformanceReport,
    Rates,
    ReliabilityReport,
    RetryBreakdown,
    RetryHeavyRun,
    RunCounts,
    SlowOperation,
    SpanRate,
    Summary,
    ToolReliability,
    Window,
)
from abb_api.analytics.store import AnalyticsScope
from abb_api.core.errors import AppError, ErrorCategory
from abb_api.db import tables as t
from abb_api.runs.summary import SUMMARY_VERSION

logger = logging.getLogger(__name__)

ACTIVE = ("QUEUED", "RUNNING", "WAITING", "WAITING_FOR_APPROVAL")
FINISHED = (
    "SUCCESS",
    "FAILED",
    "TIMED_OUT",
    "BLOCKED",
)  # cancelled runs are neither success nor failure
NAMED_KINDS = (
    "tool",
    "llm",
)  # other span names (shell commands, paths) are unbounded and may be sensitive
SLOW_KINDS_EXCLUDED = ("agent",)
MIN_SLOW_CALLS = 1

R = t.runs.c
L = t.cost_calculations.c
S = t.spans.c


def analytics_timeout() -> AppError:
    return AppError(
        "ANALYTICS_TIMEOUT",
        "The analytics query took too long. Try a shorter time window or filter by project.",
        category=ErrorCategory.TIMEOUT,
        status_code=503,
        retryable=True,
    )


def _usd(value: Decimal | float | int | None) -> float:
    return round(float(value or 0), 9)


def _rate(part: int | None, whole: int | None) -> float | None:
    return round(part / whole, 6) if part is not None and whole else None


def _avg(value: Decimal | float | None) -> float | None:
    return None if value is None else round(float(value), 4)


def _num(key: str) -> ColumnElement[Any]:
    """A numeric field of the run summary, 0 when absent (older summaries lack newer fields)."""
    return func.coalesce(cast(R.summary[key].astext, Numeric), 0)


def _runs_where(scope: AnalyticsScope) -> list[ColumnElement[bool]]:
    where = [
        R.workspace_id == scope.tenant.workspace_id,
        R.started_at >= scope.start,
        R.started_at < scope.end,
    ]
    if scope.project_id is not None:
        where.append(R.project_id == scope.project_id)
    if scope.agent_slug is not None:
        where.append(R.agent_slug == scope.agent_slug)
    return where


def _lines_from(scope: AnalyticsScope, *columns: Any) -> Select[Any]:
    """Cost lines of the scope's runs; joins `runs` only when the agent filter needs it."""
    statement = select(*columns).where(
        L.workspace_id == scope.tenant.workspace_id,
        L.run_started_at >= scope.start,
        L.run_started_at < scope.end,
    )
    if scope.project_id is not None:
        statement = statement.where(L.project_id == scope.project_id)
    if scope.agent_slug is not None:
        statement = statement.join(
            t.runs, (t.runs.c.workspace_id == L.workspace_id) & (t.runs.c.id == L.run_id)
        ).where(R.workspace_id == scope.tenant.workspace_id, R.agent_slug == scope.agent_slug)
    return statement


def _spans_from(scope: AnalyticsScope, *columns: Any) -> Select[Any]:
    """Spans of the scope's runs (spans carry no project, so they always join their run)."""
    return (
        select(*columns)
        .select_from(
            t.spans.join(
                t.runs, (t.runs.c.workspace_id == S.workspace_id) & (t.runs.c.id == S.run_id)
            )
        )
        .where(S.workspace_id == scope.tenant.workspace_id, *_runs_where(scope))
    )


def _pct(column: ColumnElement[Any], q: float) -> ColumnElement[Any]:
    return func.percentile_cont(q).within_group(column)


def _percentiles(count: int | None, p50: float | None, p95: float | None) -> Percentiles:
    return Percentiles(
        count=count or 0,
        p50_ms=None if p50 is None else round(float(p50), 3),
        p95_ms=None if p95 is None else round(float(p95), 3),
    )


class PostgresAnalyticsStore:
    def __init__(self, engine: AsyncEngine, *, timeout_seconds: float = 10.0) -> None:
        self._engine = engine
        self._timeout_ms = int(timeout_seconds * 1000)

    # ------------------------------------------------------------------ plumbing

    async def _read(self, scope: AnalyticsScope, work: Any) -> Any:
        """Run `work(conn)` read-only; the database cancels it after the timeout."""
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SET TRANSACTION READ ONLY"))
                await conn.execute(text(f"SET LOCAL statement_timeout = {self._timeout_ms}"))
                result = await work(conn)
                await conn.rollback()  # nothing to commit; ends the transaction promptly
                return result
        except DBAPIError as exc:
            if "statement timeout" in str(exc.orig) or "canceling statement" in str(exc.orig):
                logger.warning(
                    "analytics query timed out",
                    extra={"workspace_id": str(scope.tenant.workspace_id)},
                )
                raise analytics_timeout() from exc
            raise

    @staticmethod
    def _window(scope: AnalyticsScope) -> Window:
        return Window(start=scope.start, end=scope.end)

    # ------------------------------------------------------------------ shared pieces

    @staticmethod
    async def _counts_and_cost(conn: AsyncConnection, scope: AnalyticsScope) -> Any:
        cost, retry = _num("estimated_cost_usd"), _num("retry_cost_usd")
        finished_duration = case((R.status.in_(FINISHED), R.duration_ms))
        statement = select(
            func.count().label("total"),
            func.count().filter(R.status.in_(ACTIVE)).label("active"),
            func.count().filter(R.status == "SUCCESS").label("success"),
            func.count().filter(R.status == "FAILED").label("failed"),
            func.count().filter(R.status == "TIMED_OUT").label("timed_out"),
            func.count().filter(R.status == "BLOCKED").label("blocked"),
            func.count().filter(R.status == "CANCELLED").label("cancelled"),
            func.sum(cost).label("cost"),
            func.sum(cost).filter(R.status == "SUCCESS").label("success_cost"),
            func.sum(retry).label("retry_cost"),
            func.count().filter(_num("retry_count") > 0).label("retried_runs"),
            func.count().filter(_num("retry_cost_usd") > 0).label("runs_with_retry_cost"),
            func.sum(_num("retries_unattributed")).label("unattributed"),
            func.avg(_num("llm_calls")).label("avg_llm"),
            func.avg(_num("tool_calls")).label("avg_tool"),
            func.avg(_num("retry_count")).label("avg_retries"),
            func.avg(_num("files_modified")).label("avg_files"),
            func.count(finished_duration).label("latency_count"),
            _pct(finished_duration, 0.5).label("p50"),
            _pct(finished_duration, 0.95).label("p95"),
            func.count()
            .filter((R.summary_version > 0) & (R.summary_version < SUMMARY_VERSION))
            .label("unrebuilt"),
        ).where(*_runs_where(scope))
        return (await conn.execute(statement)).one()

    @staticmethod
    def _run_counts(row: Any) -> RunCounts:
        finished = row.success + row.failed + row.timed_out + row.blocked
        return RunCounts(
            total=row.total,
            active=row.active,
            finished=finished,
            success=row.success,
            failed=row.failed,
            timed_out=row.timed_out,
            blocked=row.blocked,
            cancelled=row.cancelled,
        )

    @staticmethod
    def _rates(row: Any, counts: RunCounts) -> Rates:
        return Rates(
            success_rate=_rate(counts.success, counts.finished),
            failure_rate=_rate(counts.failed + counts.blocked, counts.finished),
            timeout_rate=_rate(counts.timed_out, counts.finished),
            retry_rate=_rate(row.retried_runs, counts.total),
        )

    @staticmethod
    async def _unpriced(conn: AsyncConnection, scope: AnalyticsScope) -> int:
        statement = _lines_from(scope, func.count()).where(L.source == "unpriced")
        return int((await conn.execute(statement)).scalar_one())

    def _headline(self, row: Any, counts: RunCounts, unpriced: int) -> CostHeadline:
        total = Decimal(row.cost or 0)
        retry = Decimal(row.retry_cost or 0)
        return CostHeadline(
            total_usd=_usd(total),
            per_run_usd=_usd(total / counts.total) if counts.total else None,
            per_successful_run_usd=(
                _usd(Decimal(row.success_cost or 0) / counts.success) if counts.success else None
            ),
            retry_usd=_usd(retry),
            retry_share=round(float(retry / total), 6) if total else None,
            unpriced_calls=unpriced,
            unrebuilt_runs=row.unrebuilt,
        )

    # ------------------------------------------------------------------ summary

    async def summary(self, scope: AnalyticsScope) -> Summary:
        async def work(conn: AsyncConnection) -> Summary:
            row = await self._counts_and_cost(conn, scope)
            counts = self._run_counts(row)
            unpriced = await self._unpriced(conn, scope)
            span_rows = (
                await conn.execute(
                    _spans_from(
                        scope,
                        S.kind,
                        func.count().filter(S.status.is_not(None)).label("calls"),
                        func.count().filter(S.status == "success").label("ok"),
                    )
                    .where(S.kind.in_(NAMED_KINDS))
                    .group_by(S.kind)
                )
            ).all()
            by_kind = {r.kind: r for r in span_rows}
            agents = await conn.execute(
                select(R.agent_slug)
                .where(R.workspace_id == scope.tenant.workspace_id, R.status.in_(ACTIVE))
                .where(*([R.project_id == scope.project_id] if scope.project_id else []))
                .where(R.agent_slug.is_not(None))
                .distinct()
                .order_by(R.agent_slug)
                .limit(10)
            )

            def span_rate(kind: str) -> SpanRate:
                found = by_kind.get(kind)
                return SpanRate(
                    calls=found.calls if found else 0,
                    success_rate=_rate(found.ok, found.calls) if found else None,
                )

            return Summary(
                window=self._window(scope),
                runs=counts,
                rates=self._rates(row, counts),
                cost=self._headline(row, counts, unpriced),
                run_latency=_percentiles(row.latency_count, row.p50, row.p95),
                behaviour=Behaviour(
                    avg_llm_calls=_avg(row.avg_llm) if counts.total else None,
                    avg_tool_calls=_avg(row.avg_tool) if counts.total else None,
                    avg_retries=_avg(row.avg_retries) if counts.total else None,
                    avg_files_modified=_avg(row.avg_files) if counts.total else None,
                ),
                tools=span_rate("tool"),
                llm=span_rate("llm"),
                active_agents=[a.agent_slug for a in agents],
            )

        result: Summary = await self._read(scope, work)
        return result

    # ------------------------------------------------------------------ cost

    async def cost(self, scope: AnalyticsScope, *, top: int) -> CostReport:
        async def work(conn: AsyncConnection) -> CostReport:
            row = await self._counts_and_cost(conn, scope)
            counts = self._run_counts(row)
            unpriced = await self._unpriced(conn, scope)
            headline = self._headline(row, counts, unpriced)
            total = Decimal(row.cost or 0)
            retry = Decimal(row.retry_cost or 0)

            day = func.date_trunc("day", func.timezone("UTC", R.started_at))
            by_day = [
                CostByDay(
                    day=r.day.date() if hasattr(r.day, "date") else r.day,
                    cost_usd=_usd(r.cost),
                    retry_usd=_usd(r.retry),
                    runs=r.runs,
                )
                for r in await conn.execute(
                    select(
                        day.label("day"),
                        func.sum(_num("estimated_cost_usd")).label("cost"),
                        func.sum(_num("retry_cost_usd")).label("retry"),
                        func.count().label("runs"),
                    )
                    .where(*_runs_where(scope))
                    .group_by(day)
                    .order_by(day)
                )
            ]

            total_cost = func.sum(L.total)
            agent_rows = (
                await conn.execute(
                    _lines_from(
                        scope,
                        L.agent_slug.label("agent"),
                        total_cost.label("cost"),
                        func.count().label("calls"),
                        func.count().over().label("groups"),
                    )
                    .group_by(L.agent_slug)
                    .order_by(total_cost.desc(), L.agent_slug)
                    .limit(top)
                )
            ).all()
            model_rows = (
                await conn.execute(
                    _lines_from(
                        scope,
                        L.provider,
                        L.model,
                        total_cost.label("cost"),
                        func.count().label("calls"),
                        func.sum(L.input_tokens).label("tin"),
                        func.sum(L.output_tokens).label("tout"),
                        func.count().filter(L.source == "unpriced").label("unpriced"),
                    )
                    .group_by(L.provider, L.model)
                    .order_by(total_cost.desc(), L.model, L.provider)
                    .limit(top)
                )
            ).all()
            grand = (
                await conn.execute(
                    _lines_from(
                        scope,
                        total_cost.label("cost"),
                        func.count().label("calls"),
                        func.count(func.distinct(L.agent_slug)).label("agents"),
                    )
                )
            ).one()
            model_groups = (
                await conn.execute(
                    select(func.count()).select_from(
                        _lines_from(scope, L.provider, L.model)
                        .group_by(L.provider, L.model)
                        .subquery()
                    )
                )
            ).scalar_one()
            sources = [
                CostBySource(source=r.source, cost_usd=_usd(r.cost), calls=r.calls)
                for r in await conn.execute(
                    _lines_from(
                        scope, L.source, total_cost.label("cost"), func.count().label("calls")
                    )
                    .group_by(L.source)
                    .order_by(total_cost.desc(), L.source)
                )
            ]

            projects: list[CostByProject] | None = None
            if scope.project_id is None:
                projects = [
                    CostByProject(
                        project_id=from_uuid(IdKind.PROJECT, r.project_id),
                        cost_usd=_usd(r.cost),
                        runs=r.runs,
                    )
                    for r in await conn.execute(
                        select(
                            R.project_id,
                            func.sum(_num("estimated_cost_usd")).label("cost"),
                            func.count().label("runs"),
                        )
                        .where(*_runs_where(scope))
                        .group_by(R.project_id)
                        .order_by(func.sum(_num("estimated_cost_usd")).desc(), R.project_id)
                        .limit(top)
                    )
                ]

            expensive = [
                _expensive(r)
                for r in await conn.execute(
                    select(
                        R.id,
                        R.project_id,
                        R.name,
                        R.agent_slug,
                        R.status,
                        R.started_at,
                        _num("estimated_cost_usd").label("cost"),
                        _num("retry_cost_usd").label("retry"),
                        _num("retry_count").label("retries"),
                    )
                    .where(*_runs_where(scope), _num("estimated_cost_usd") > 0)
                    .order_by(_num("estimated_cost_usd").desc(), R.id)
                    .limit(top)
                )
            ]

            return CostReport(
                window=self._window(scope),
                headline=headline,
                retries=RetryBreakdown(
                    total_usd=_usd(total),
                    initial_usd=_usd(total - retry),
                    retry_usd=_usd(retry),
                    retry_share=headline.retry_share,
                    runs_with_retry_cost=row.runs_with_retry_cost,
                    retries_unattributed=int(row.unattributed or 0),
                ),
                by_day=by_day,
                by_agent=[
                    CostByAgent(agent=r.agent, cost_usd=_usd(r.cost), calls=r.calls)
                    for r in agent_rows
                ],
                by_agent_other=_other(agent_rows, grand.agents, grand.cost, grand.calls),
                by_model=[
                    CostByModel(
                        provider=r.provider,
                        model=r.model,
                        cost_usd=_usd(r.cost),
                        calls=r.calls,
                        input_tokens=int(r.tin or 0),
                        output_tokens=int(r.tout or 0),
                        unpriced_calls=r.unpriced,
                    )
                    for r in model_rows
                ],
                by_model_other=_other(model_rows, model_groups, grand.cost, grand.calls),
                by_source=sources,
                by_project=projects,
                expensive_runs=expensive,
            )

        result: CostReport = await self._read(scope, work)
        return result

    # ------------------------------------------------------------------ reliability

    async def reliability(self, scope: AnalyticsScope, *, top: int) -> ReliabilityReport:
        async def work(conn: AsyncConnection) -> ReliabilityReport:
            row = await self._counts_and_cost(conn, scope)
            counts = self._run_counts(row)

            day = func.date_trunc("day", func.timezone("UTC", R.started_at))
            trend = []
            for r in await conn.execute(
                select(
                    day.label("day"),
                    func.count().filter(R.status == "SUCCESS").label("success"),
                    func.count().filter(R.status == "FAILED").label("failed"),
                    func.count().filter(R.status == "TIMED_OUT").label("timed_out"),
                    func.count().filter(R.status == "BLOCKED").label("blocked"),
                )
                .where(*_runs_where(scope))
                .group_by(day)
                .order_by(day)
            ):
                finished = r.success + r.failed + r.timed_out + r.blocked
                trend.append(
                    FailureDay(
                        day=_as_date(r.day),
                        finished=finished,
                        success=r.success,
                        failed=r.failed,
                        timed_out=r.timed_out,
                        blocked=r.blocked,
                        failure_rate=_rate(r.failed + r.blocked + r.timed_out, finished),
                    )
                )

            calls = func.count().filter(S.status.is_not(None))
            tool_rows = (
                await conn.execute(
                    _spans_from(
                        scope,
                        S.name,
                        calls.label("calls"),
                        func.count().filter(S.status == "success").label("ok"),
                        _pct(S.duration_ms, 0.95).label("p95"),
                        func.count().over().label("groups"),
                    )
                    .where(S.kind == "tool")
                    .group_by(S.name)
                    .order_by(calls.desc(), S.name)
                    .limit(top)
                )
            ).all()
            tool_total = (
                await conn.execute(
                    _spans_from(
                        scope,
                        calls.label("calls"),
                        func.count(func.distinct(S.name)).label("names"),
                    ).where(S.kind == "tool")
                )
            ).one()

            heavy = [
                RetryHeavyRun(
                    run_id=from_uuid(IdKind.RUN, r.id),
                    project_id=from_uuid(IdKind.PROJECT, r.project_id),
                    name=r.name,
                    agent=r.agent_slug,
                    status=r.status,
                    started_at=r.started_at,
                    retry_count=int(r.retries),
                    retry_usd=_usd(r.retry),
                    cost_usd=_usd(r.cost),
                )
                for r in await conn.execute(
                    select(
                        R.id,
                        R.project_id,
                        R.name,
                        R.agent_slug,
                        R.status,
                        R.started_at,
                        _num("retry_count").label("retries"),
                        _num("retry_cost_usd").label("retry"),
                        _num("estimated_cost_usd").label("cost"),
                    )
                    .where(*_runs_where(scope), _num("retry_count") > 0)
                    .order_by(_num("retry_count").desc(), R.started_at.desc(), R.id)
                    .limit(top)
                )
            ]
            shown = sum(r.calls for r in tool_rows)
            return ReliabilityReport(
                window=self._window(scope),
                runs=counts,
                rates=self._rates(row, counts),
                failure_trend=trend,
                tools=[
                    ToolReliability(
                        name=r.name or "(unnamed)",
                        calls=r.calls,
                        success_rate=_rate(r.ok, r.calls),
                        p95_ms=None if r.p95 is None else round(float(r.p95), 3),
                    )
                    for r in tool_rows
                ],
                tools_other=(
                    OtherBucket(
                        groups=int(tool_total.names) - len(tool_rows),
                        calls=int(tool_total.calls) - shown,
                    )
                    if int(tool_total.names) > len(tool_rows)
                    else None
                ),
                retry_heavy_runs=heavy,
            )

        result: ReliabilityReport = await self._read(scope, work)
        return result

    # ------------------------------------------------------------------ performance

    async def performance(self, scope: AnalyticsScope, *, top: int) -> PerformanceReport:
        async def work(conn: AsyncConnection) -> PerformanceReport:
            row = await self._counts_and_cost(conn, scope)

            def kind_stats(kind: str) -> Select[Any]:
                return _spans_from(
                    scope,
                    func.count(S.duration_ms).label("n"),
                    _pct(S.duration_ms, 0.5).label("p50"),
                    _pct(S.duration_ms, 0.95).label("p95"),
                ).where(S.kind == kind)

            llm = (await conn.execute(kind_stats("llm"))).one()
            tool = (await conn.execute(kind_stats("tool"))).one()

            name = case((S.kind.in_(NAMED_KINDS), S.name), else_=literal(None))
            p95 = _pct(S.duration_ms, 0.95)
            slow = [
                SlowOperation(
                    kind=r.kind,
                    name=r.name,
                    calls=r.calls,
                    p50_ms=None if r.p50 is None else round(float(r.p50), 3),
                    p95_ms=None if r.p95 is None else round(float(r.p95), 3),
                    max_ms=None if r.max is None else round(float(r.max), 3),
                )
                for r in await conn.execute(
                    _spans_from(
                        scope,
                        S.kind,
                        name.label("name"),
                        func.count(S.duration_ms).label("calls"),
                        _pct(S.duration_ms, 0.5).label("p50"),
                        p95.label("p95"),
                        func.max(S.duration_ms).label("max"),
                    )
                    .where(S.duration_ms.is_not(None), S.kind.not_in(SLOW_KINDS_EXCLUDED))
                    .group_by(S.kind, name)
                    .order_by(p95.desc(), S.kind, name)
                    .limit(top)
                )
            ]
            return PerformanceReport(
                window=self._window(scope),
                run=_percentiles(row.latency_count, row.p50, row.p95),
                llm=_percentiles(llm.n, llm.p50, llm.p95),
                tool=_percentiles(tool.n, tool.p50, tool.p95),
                slow_operations=slow,
            )

        result: PerformanceReport = await self._read(scope, work)
        return result


def _as_date(value: Any) -> date:
    result: date = value.date() if hasattr(value, "date") else value
    return result


def _expensive(r: Any) -> ExpensiveRun:
    return ExpensiveRun(
        run_id=from_uuid(IdKind.RUN, r.id),
        project_id=from_uuid(IdKind.PROJECT, r.project_id),
        name=r.name,
        agent=r.agent_slug,
        status=r.status,
        started_at=r.started_at,
        cost_usd=_usd(r.cost),
        retry_usd=_usd(r.retry),
        retry_count=int(r.retries),
    )


def _other(
    shown: Sequence[Any], groups: int, total_cost: Decimal | None, total_calls: int
) -> OtherBucket | None:
    if groups <= len(shown):
        return None
    return OtherBucket(
        groups=groups - len(shown),
        calls=int(total_calls) - sum(r.calls for r in shown),
        cost_usd=_usd(
            Decimal(total_cost or 0) - sum((Decimal(r.cost or 0) for r in shown), Decimal(0))
        ),
    )

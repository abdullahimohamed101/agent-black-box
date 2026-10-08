"""PostgreSQL implementation of `AnalyticsStore` (ADR-041, ADR-043).

Reads only derived tables (INV-7): the daily rollups, which the `refresh_analytics_day` job rewrites
from `runs`, `spans` and `cost_calculations`. Never `events`. Every
statement starts from the tenant's workspace id through `_source`, so a scope cannot be applied to
one table and forgotten on another. Each call is one read-only transaction with a statement timeout.
"""

import logging
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from abb_event_schema.ids import IdKind, from_uuid
from sqlalchemy import func, select, text, tuple_
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from abb_api.analytics.percentiles import percentile
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

logger = logging.getLogger(__name__)

ACTIVE = ("QUEUED", "RUNNING", "WAITING", "WAITING_FOR_APPROVAL")
R = t.runs.c
MAX_SLOW_CANDIDATES = 200  # operations whose histograms are read to rank "slowest"

RUNS_COLS = (
    "project_id", "day", "agent_slug", "status", "runs", "cost_usd", "retry_cost_usd",
    "retried_runs", "runs_with_retry_cost", "llm_calls", "tool_calls", "retry_count",
    "retries_unattributed", "files_modified", "unpriced_calls", "tool_spans_finished",
    "tool_spans_ok", "llm_spans_finished", "llm_spans_ok", "unrebuilt_runs",
)  # fmt: skip
COST_COLS = (
    "project_id", "day", "agent_slug", "provider", "model", "source", "calls", "total_usd",
    "input_tokens", "output_tokens",
)  # fmt: skip
RUN_SUM_FIELDS = (
    "retried_runs", "runs_with_retry_cost", "llm_calls", "tool_calls", "retry_count",
    "retries_unattributed", "files_modified", "unpriced_calls", "tool_spans_finished",
    "tool_spans_ok", "llm_spans_finished", "llm_spans_ok", "unrebuilt_runs",
)  # fmt: skip
SPANS_COLS = ("project_id", "day", "kind", "name", "finished", "ok")
LATENCY_COLS = ("project_id", "day", "series", "name", "bucket", "n")
TOP_COLS = (
    "project_id",
    "day",
    "kind",
    "rank",
    "run_id",
    "cost_usd",
    "retry_cost_usd",
    "retry_count",
)


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


def _rate(part: int | float | None, whole: int | float | None) -> float | None:
    return round(float(part) / float(whole), 6) if part is not None and whole else None


def _avg(total: Decimal | float | int | None, count: int) -> float | None:
    return round(float(total or 0) / count, 4) if count else None


def _ms(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


def _source(table: Any, columns: Sequence[str], scope: AnalyticsScope) -> Any:
    """The stored rollup rows of one kind for the scope (tenant, days, optional project)."""
    statement = select(*(table.c[c] for c in columns)).where(
        table.c.workspace_id == scope.tenant.workspace_id,
        table.c.day >= scope.start_day,
        table.c.day < scope.end_day,
    )
    if scope.project_id is not None:
        statement = statement.where(table.c.project_id == scope.project_id)
    return statement.subquery()


class _Sources:
    """The rollup kinds for one scope."""

    def __init__(self, scope: AnalyticsScope) -> None:
        self._scope = scope

    def runs(self) -> Any:
        return _source(t.analytics_runs_daily, RUNS_COLS, self._scope)

    def cost(self) -> Any:
        return _source(t.analytics_cost_daily, COST_COLS, self._scope)

    def spans(self) -> Any:
        return _source(t.analytics_spans_daily, SPANS_COLS, self._scope)

    def latency(self) -> Any:
        return _source(t.analytics_latency_daily, LATENCY_COLS, self._scope)

    def top(self) -> Any:
        return _source(t.analytics_top_runs, TOP_COLS, self._scope)


class _RunTotals:
    """Run-level totals folded from per-status rollup rows."""

    def __init__(self, rows: Sequence[Any]) -> None:
        self.by_status: dict[str, int] = defaultdict(int)
        self.cost = Decimal(0)
        self.success_cost = Decimal(0)
        self.retry_cost = Decimal(0)
        self.fields: dict[str, int] = defaultdict(int)
        for r in rows:
            self.by_status[r.status] += int(r.runs)
            self.cost += Decimal(r.cost_usd or 0)
            self.retry_cost += Decimal(r.retry_cost_usd or 0)
            if r.status == "SUCCESS":
                self.success_cost += Decimal(r.cost_usd or 0)
            for field in RUN_SUM_FIELDS:
                self.fields[field] += int(getattr(r, field) or 0)

    @property
    def total(self) -> int:
        return sum(self.by_status.values())

    @property
    def counts(self) -> RunCounts:
        s = self.by_status
        finished = s["SUCCESS"] + s["FAILED"] + s["TIMED_OUT"] + s["BLOCKED"]
        return RunCounts(
            total=self.total,
            active=sum(s[a] for a in ACTIVE),
            finished=finished,
            success=s["SUCCESS"],
            failed=s["FAILED"],
            timed_out=s["TIMED_OUT"],
            blocked=s["BLOCKED"],
            cancelled=s["CANCELLED"],
        )

    @property
    def rates(self) -> Rates:
        c = self.counts
        return Rates(
            success_rate=_rate(c.success, c.finished),
            failure_rate=_rate(c.failed + c.blocked, c.finished),
            timeout_rate=_rate(c.timed_out, c.finished),
            retry_rate=_rate(self.fields["retried_runs"], c.total),
        )

    @property
    def headline(self) -> CostHeadline:
        c = self.counts
        return CostHeadline(
            total_usd=_usd(self.cost),
            per_run_usd=_usd(self.cost / c.total) if c.total else None,
            per_successful_run_usd=(_usd(self.success_cost / c.success) if c.success else None),
            retry_usd=_usd(self.retry_cost),
            retry_share=round(float(self.retry_cost / self.cost), 6) if self.cost else None,
            unpriced_calls=self.fields["unpriced_calls"],
            unrebuilt_runs=self.fields["unrebuilt_runs"],
        )


def _histograms(rows: Sequence[Any], key: Callable[[Any], Any]) -> dict[Any, dict[int, int]]:
    merged: dict[Any, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        merged[key(r)][int(r.bucket)] += int(r.n)
    return merged


def _percentiles(counts: Mapping[int, int] | None) -> Percentiles:
    counts = counts or {}
    return Percentiles(
        count=sum(counts.values()),
        p50_ms=_ms(percentile(counts, 0.5)),
        p95_ms=_ms(percentile(counts, 0.95)),
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
        return Window(
            start=datetime.combine(scope.start_day, time.min, tzinfo=UTC),
            end=datetime.combine(scope.end_day, time.min, tzinfo=UTC),
        )

    @staticmethod
    async def _totals(conn: AsyncConnection, sources: _Sources) -> _RunTotals:
        u = sources.runs()
        statement = select(
            u.c.status,
            func.sum(u.c.runs).label("runs"),
            func.sum(u.c.cost_usd).label("cost_usd"),
            func.sum(u.c.retry_cost_usd).label("retry_cost_usd"),
            *(func.sum(u.c[f]).label(f) for f in RUN_SUM_FIELDS),
        ).group_by(u.c.status)  # fmt: skip
        return _RunTotals((await conn.execute(statement)).all())

    @staticmethod
    async def _latency(
        conn: AsyncConnection,
        sources: _Sources,
        *series: str,
        names: Sequence[str] | None = None,
    ) -> Sequence[Any]:
        """Duration histogram rows of some series, optionally only for the given names."""
        u = sources.latency()
        statement = (
            select(u.c.series, u.c.name, u.c.bucket, func.sum(u.c.n).label("n"))
            .where(u.c.series.in_(series))
            .group_by(u.c.series, u.c.name, u.c.bucket)
        )
        if names is not None:
            statement = statement.where(u.c.name.in_(list(names)))
        return (await conn.execute(statement)).all()

    # ------------------------------------------------------------------ summary

    async def summary(self, scope: AnalyticsScope) -> Summary:
        async def work(conn: AsyncConnection) -> Summary:
            sources = _Sources(scope)
            totals = await self._totals(conn, sources)
            run_hist = _histograms(await self._latency(conn, sources, "run"), lambda r: "run")
            agents = await conn.execute(
                select(R.agent_slug)
                .where(R.workspace_id == scope.tenant.workspace_id, R.status.in_(ACTIVE))
                .where(*([R.project_id == scope.project_id] if scope.project_id else []))
                .where(R.agent_slug.is_not(None))
                .distinct()
                .order_by(R.agent_slug)
                .limit(10)
            )
            f, n = totals.fields, totals.total
            return Summary(
                window=self._window(scope),
                runs=totals.counts,
                rates=totals.rates,
                cost=totals.headline,
                run_latency=_percentiles(run_hist.get("run")),
                behaviour=Behaviour(
                    avg_llm_calls=_avg(f["llm_calls"], n),
                    avg_tool_calls=_avg(f["tool_calls"], n),
                    avg_retries=_avg(f["retry_count"], n),
                    avg_files_modified=_avg(f["files_modified"], n),
                ),
                tools=SpanRate(
                    calls=f["tool_spans_finished"],
                    success_rate=_rate(f["tool_spans_ok"], f["tool_spans_finished"]),
                ),
                llm=SpanRate(
                    calls=f["llm_spans_finished"],
                    success_rate=_rate(f["llm_spans_ok"], f["llm_spans_finished"]),
                ),
                active_agents=[a.agent_slug for a in agents],
            )

        result: Summary = await self._read(scope, work)
        return result

    # ------------------------------------------------------------------ cost

    async def cost(self, scope: AnalyticsScope, *, top: int) -> CostReport:
        async def work(conn: AsyncConnection) -> CostReport:
            sources = _Sources(scope)
            totals = await self._totals(conn, sources)
            headline = totals.headline

            runs_u = sources.runs()
            by_day = [
                CostByDay(
                    day=r.day, cost_usd=_usd(r.cost), retry_usd=_usd(r.retry), runs=int(r.runs)
                )
                for r in await conn.execute(
                    select(
                        runs_u.c.day,
                        func.sum(runs_u.c.cost_usd).label("cost"),
                        func.sum(runs_u.c.retry_cost_usd).label("retry"),
                        func.sum(runs_u.c.runs).label("runs"),
                    )
                    .group_by(runs_u.c.day)
                    .order_by(runs_u.c.day)
                )
            ]
            by_project: list[CostByProject] | None = None
            if scope.project_id is None:
                by_project = [
                    CostByProject(
                        project_id=from_uuid(IdKind.PROJECT, r.project_id),
                        cost_usd=_usd(r.cost),
                        runs=int(r.runs),
                    )
                    for r in await conn.execute(
                        select(
                            runs_u.c.project_id,
                            func.sum(runs_u.c.cost_usd).label("cost"),
                            func.sum(runs_u.c.runs).label("runs"),
                        )
                        .group_by(runs_u.c.project_id)
                        .order_by(func.sum(runs_u.c.cost_usd).desc(), runs_u.c.project_id)
                        .limit(top)
                    )
                ]

            cost_u = sources.cost()
            total_usd = func.sum(cost_u.c.total_usd)
            calls = func.sum(cost_u.c.calls)

            async def top_groups(*columns: Any) -> tuple[list[Any], OtherBucket | None]:
                """The `top` costliest groups (LIMIT in SQL) and one bucket for all the rest."""
                shown = list(
                    (
                        await conn.execute(
                            select(
                                *columns,
                                total_usd.label("cost"),
                                calls.label("calls"),
                                func.sum(cost_u.c.input_tokens).label("tin"),
                                func.sum(cost_u.c.output_tokens).label("tout"),
                            )
                            .group_by(*columns)
                            .order_by(total_usd.desc(), *columns)
                            .limit(top)
                        )
                    ).all()
                )
                grouped = select(*columns).group_by(*columns).subquery()
                group_count = (
                    await conn.execute(select(func.count()).select_from(grouped))
                ).scalar_one()
                if group_count <= len(shown):
                    return shown, None
                everything = (
                    await conn.execute(select(total_usd.label("cost"), calls.label("calls")))
                ).one()
                rest = OtherBucket(
                    groups=group_count - len(shown),
                    calls=int(everything.calls or 0) - sum(int(r.calls) for r in shown),
                    cost_usd=_usd(
                        Decimal(everything.cost or 0)
                        - sum((Decimal(r.cost or 0) for r in shown), Decimal(0))
                    ),
                )
                return shown, rest

            agent_rows, agent_other = await top_groups(cost_u.c.agent_slug)
            model_rows, model_other = await top_groups(cost_u.c.provider, cost_u.c.model)
            source_rows = (
                await conn.execute(
                    select(cost_u.c.source, total_usd.label("cost"), calls.label("calls"))
                    .group_by(cost_u.c.source)
                    .order_by(total_usd.desc(), cost_u.c.source)
                )
            ).all()
            unpriced_by_model: dict[tuple[str, str], int] = {
                (r.provider, r.model): int(r.calls)
                for r in (
                    await conn.execute(
                        select(cost_u.c.provider, cost_u.c.model, calls.label("calls"))
                        .where(cost_u.c.source == "unpriced")
                        .group_by(cost_u.c.provider, cost_u.c.model)
                        .order_by(calls.desc())
                        .limit(top)
                    )
                ).all()
            }

            top_u = sources.top()
            expensive = await self._runs_from_top(conn, scope, top_u, "cost", top)
            return CostReport(
                window=self._window(scope),
                headline=headline,
                retries=RetryBreakdown(
                    total_usd=headline.total_usd,
                    initial_usd=_usd(totals.cost - totals.retry_cost),
                    retry_usd=headline.retry_usd,
                    retry_share=headline.retry_share,
                    runs_with_retry_cost=totals.fields["runs_with_retry_cost"],
                    retries_unattributed=totals.fields["retries_unattributed"],
                ),
                by_day=by_day,
                by_agent=[
                    CostByAgent(
                        agent=r.agent_slug or "(unknown)", cost_usd=_usd(r.cost), calls=int(r.calls)
                    )
                    for r in agent_rows
                ],
                by_agent_other=agent_other,
                by_model=[
                    CostByModel(
                        provider=r.provider or None,
                        model=r.model or None,
                        cost_usd=_usd(r.cost),
                        calls=int(r.calls),
                        input_tokens=int(r.tin or 0),
                        output_tokens=int(r.tout or 0),
                        unpriced_calls=unpriced_by_model.get((r.provider, r.model), 0),
                    )
                    for r in model_rows
                ],
                by_model_other=model_other,
                by_source=[
                    CostBySource(source=r.source, cost_usd=_usd(r.cost), calls=int(r.calls))
                    for r in source_rows
                ],
                by_project=by_project,
                expensive_runs=[
                    ExpensiveRun(
                        **row,
                        cost_usd=_usd(c),
                        retry_usd=_usd(rc),
                        retry_count=int(n),
                    )
                    for row, c, rc, n in expensive
                ],
            )

        result: CostReport = await self._read(scope, work)
        return result

    @staticmethod
    async def _runs_from_top(
        conn: AsyncConnection, scope: AnalyticsScope, top_u: Any, kind: str, limit: int
    ) -> list[tuple[dict[str, Any], Any, Any, Any]]:
        """The top `limit` stored/live ranked runs, joined to their run rows (tenant-scoped)."""
        metric = top_u.c.cost_usd if kind == "cost" else top_u.c.retry_count
        ranked = (
            await conn.execute(
                select(
                    top_u.c.run_id, top_u.c.cost_usd, top_u.c.retry_cost_usd, top_u.c.retry_count
                )
                .where(top_u.c.kind == kind)
                .order_by(metric.desc(), top_u.c.run_id)
                .limit(limit)
            )
        ).all()
        if not ranked:
            return []
        details = {
            r.id: r
            for r in await conn.execute(
                select(R.id, R.project_id, R.name, R.agent_slug, R.status, R.started_at).where(
                    R.workspace_id == scope.tenant.workspace_id,
                    R.id.in_([r.run_id for r in ranked]),
                )
            )
        }
        out = []
        for r in ranked:
            d = details.get(r.run_id)
            if d is None:  # a run removed since the rollup was written
                continue
            row = {
                "run_id": from_uuid(IdKind.RUN, d.id),
                "project_id": from_uuid(IdKind.PROJECT, d.project_id),
                "name": d.name,
                "agent": d.agent_slug,
                "status": d.status,
                "started_at": d.started_at,
            }
            out.append((row, r.cost_usd, r.retry_cost_usd, r.retry_count))
        return out

    # ------------------------------------------------------------------ reliability

    async def reliability(self, scope: AnalyticsScope, *, top: int) -> ReliabilityReport:
        async def work(conn: AsyncConnection) -> ReliabilityReport:
            sources = _Sources(scope)
            totals = await self._totals(conn, sources)

            runs_u = sources.runs()
            per_day: dict[date, dict[str, int]] = defaultdict(lambda: defaultdict(int))
            for r in await conn.execute(
                select(
                    runs_u.c.day, runs_u.c.status, func.sum(runs_u.c.runs).label("runs")
                ).group_by(runs_u.c.day, runs_u.c.status)
            ):
                per_day[r.day][r.status] += int(r.runs)
            trend = []
            for day in sorted(per_day):
                s = per_day[day]
                finished = s["SUCCESS"] + s["FAILED"] + s["TIMED_OUT"] + s["BLOCKED"]
                trend.append(
                    FailureDay(
                        day=day,
                        finished=finished,
                        success=s["SUCCESS"],
                        failed=s["FAILED"],
                        timed_out=s["TIMED_OUT"],
                        blocked=s["BLOCKED"],
                        failure_rate=_rate(s["FAILED"] + s["BLOCKED"] + s["TIMED_OUT"], finished),
                    )
                )

            spans_u = sources.spans()
            finished_calls: Any = func.sum(spans_u.c.finished)
            tool_rows = (
                await conn.execute(
                    select(
                        spans_u.c.name,
                        finished_calls.label("calls"),
                        func.sum(spans_u.c.ok).label("ok"),
                    )
                    .where(spans_u.c.kind == "tool")
                    .group_by(spans_u.c.name)
                    .order_by(finished_calls.desc(), spans_u.c.name)
                    .limit(top)
                )
            ).all()
            tool_totals = (
                await conn.execute(
                    select(
                        func.count(func.distinct(spans_u.c.name)).label("names"),
                        finished_calls.label("calls"),
                    ).where(spans_u.c.kind == "tool")
                )
            ).one()
            tool_hist = _histograms(
                await self._latency(conn, sources, "tool", names=[r.name for r in tool_rows]),
                lambda r: r.name,
            )
            tools = [
                ToolReliability(
                    name=r.name or "(unnamed)",
                    calls=int(r.calls),
                    success_rate=_rate(r.ok, r.calls),
                    p95_ms=_ms(percentile(tool_hist.get(r.name, {}), 0.95)),
                )
                for r in tool_rows
            ]
            heavy = [
                RetryHeavyRun(
                    run_id=row["run_id"],
                    project_id=row["project_id"],
                    name=row["name"],
                    agent=row["agent"],
                    status=row["status"],
                    started_at=row["started_at"],
                    retry_count=int(n),
                    retry_usd=_usd(rc),
                    cost_usd=_usd(c),
                )
                for row, c, rc, n in await self._runs_from_top(
                    conn, scope, sources.top(), "retries", top
                )
            ]
            hidden = int(tool_totals.names) - len(tool_rows)
            return ReliabilityReport(
                window=self._window(scope),
                runs=totals.counts,
                rates=totals.rates,
                failure_trend=trend,
                tools=tools,
                tools_other=(
                    OtherBucket(
                        groups=hidden,
                        calls=int(tool_totals.calls or 0) - sum(int(r.calls) for r in tool_rows),
                    )
                    if hidden > 0
                    else None
                ),
                retry_heavy_runs=heavy,
            )

        result: ReliabilityReport = await self._read(scope, work)
        return result

    # ------------------------------------------------------------------ performance

    async def performance(self, scope: AnalyticsScope, *, top: int) -> PerformanceReport:
        async def work(conn: AsyncConnection) -> PerformanceReport:
            sources = _Sources(scope)
            u = sources.latency()
            by_series = _histograms(
                (
                    await conn.execute(
                        select(u.c.series, u.c.bucket, func.sum(u.c.n).label("n")).group_by(
                            u.c.series, u.c.bucket
                        )
                    )
                ).all(),
                lambda r: r.series,
            )
            # Candidates: the busiest operations (bounded), then their histograms, ranked by p95.
            volume: Any = func.sum(u.c.n)
            busiest = (
                await conn.execute(
                    select(u.c.series, u.c.name)
                    .where(u.c.series != "run")
                    .group_by(u.c.series, u.c.name)
                    .order_by(volume.desc(), u.c.series, u.c.name)
                    .limit(MAX_SLOW_CANDIDATES)
                )
            ).all()
            by_operation = _histograms(
                (
                    await conn.execute(
                        select(u.c.series, u.c.name, u.c.bucket, volume.label("n"))
                        .where(
                            tuple_(u.c.series, u.c.name).in_([(r.series, r.name) for r in busiest])
                        )
                        .group_by(u.c.series, u.c.name, u.c.bucket)
                    )
                ).all()
                if busiest
                else [],
                lambda r: (r.series, r.name),
            )
            slow = [
                SlowOperation(
                    kind=series,
                    name=name or None,
                    calls=sum(counts.values()),
                    p50_ms=_ms(percentile(counts, 0.5)),
                    p95_ms=_ms(percentile(counts, 0.95)),
                )
                for (series, name), counts in by_operation.items()
            ]
            slow.sort(key=lambda o: (-(o.p95_ms or 0), o.kind, o.name or ""))
            return PerformanceReport(
                window=self._window(scope),
                run=_percentiles(by_series.get("run")),
                llm=_percentiles(by_series.get("llm")),
                tool=_percentiles(by_series.get("tool")),
                slow_operations=slow[:top],
            )

        result: PerformanceReport = await self._read(scope, work)
        return result

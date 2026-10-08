"""Daily rollups: SQL that aggregates the derived tables per (project, UTC day) (ADR-043).

`refresh_day` stores the aggregates of one workspace-day (delete + insert, so it is idempotent
and a rebuild reproduces it, INV-2). Every select returns columns named like the rollup table it
feeds. Client-controlled names (agent slugs, models, tool names) are capped per day.
"""

import logging
import uuid
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Date,
    Select,
    case,
    cast,
    delete,
    func,
    insert,
    literal,
    literal_column,
    select,
)
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.analytics.percentiles import bucket_sql
from abb_api.db import tables as t
from abb_api.runs.summary import SUMMARY_VERSION
from abb_api.tenancy import TenantContext

logger = logging.getLogger(__name__)

R = t.runs.c
RUN_SUMS = (
    "runs", "cost_usd", "retry_cost_usd", "retried_runs", "runs_with_retry_cost", "llm_calls",
    "tool_calls", "retry_count", "retries_unattributed", "files_modified", "unpriced_calls",
    "tool_spans_finished", "tool_spans_ok", "llm_spans_finished", "llm_spans_ok", "unrebuilt_runs",
)  # fmt: skip
L = t.cost_calculations.c
S = t.spans.c

FINISHED = ("SUCCESS", "FAILED", "TIMED_OUT", "BLOCKED")
NAMED_KINDS = ("tool", "llm")
TOP_RUNS = 50  # stored per project and day, per ranking
MAX_NAMES_PER_DAY = 200  # distinct names kept per project and day; the rest fold into OTHER
MAX_ROWS_PER_DAY = 2000  # rows kept per project and day in one rollup table
NAME_MAX = 128  # characters of any client-controlled key stored in a rollup
OTHER = "(other)"
# Constants inside grouped expressions must be literals, not bind parameters: PostgreSQL only
# accepts a SELECT expression in GROUP BY if it is the same expression, and bound values get
# different numbers.
EMPTY: ColumnElement[str] = literal_column("''")


def day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def _day(column: Any) -> Any:
    return cast(func.timezone("UTC", column), Date)


def _window(column: Any, workspace_id: uuid.UUID, start: date, end: date) -> list[Any]:
    return [column >= day_start(start), column < day_start(end)]


def _project(column: Any, project_id: uuid.UUID | None) -> list[Any]:
    return [column == project_id] if project_id is not None else []


# ------------------------------------------------------------------ the aggregate selects


def runs_select(ws: uuid.UUID, start: date, end: date, project: uuid.UUID | None) -> Select[Any]:
    day = _day(R.started_at)
    agent = func.coalesce(R.agent_slug, EMPTY)
    return (
        select(
            R.project_id,
            day.label("day"),
            agent.label("agent_slug"),
            R.status,
            func.count().label("runs"),
            func.sum(R.cost_usd).label("cost_usd"),
            func.sum(R.retry_cost_usd).label("retry_cost_usd"),
            func.count().filter(R.retry_count > 0).label("retried_runs"),
            func.count().filter(R.retry_cost_usd > 0).label("runs_with_retry_cost"),
            func.sum(R.llm_calls).label("llm_calls"),
            func.sum(R.tool_calls).label("tool_calls"),
            func.sum(R.retry_count).label("retry_count"),
            func.sum(R.retries_unattributed).label("retries_unattributed"),
            func.sum(R.files_modified).label("files_modified"),
            func.sum(R.unpriced_calls).label("unpriced_calls"),
            func.sum(R.tool_spans_finished).label("tool_spans_finished"),
            func.sum(R.tool_spans_ok).label("tool_spans_ok"),
            func.sum(R.llm_spans_finished).label("llm_spans_finished"),
            func.sum(R.llm_spans_ok).label("llm_spans_ok"),
            func.count()
            .filter((R.summary_version > 0) & (R.summary_version < SUMMARY_VERSION))
            .label("unrebuilt_runs"),
        )
        .where(
            R.workspace_id == ws,
            *_window(R.started_at, ws, start, end),
            *_project(R.project_id, project),
        )
        .group_by(R.project_id, day, agent, R.status)
    )


def cost_select(ws: uuid.UUID, start: date, end: date, project: uuid.UUID | None) -> Select[Any]:
    day = _day(L.run_started_at)
    provider = func.coalesce(L.provider, EMPTY)
    model = func.coalesce(L.model, EMPTY)
    return (
        select(
            L.project_id,
            day.label("day"),
            L.agent_slug,
            provider.label("provider"),
            model.label("model"),
            L.source,
            func.count().label("calls"),
            func.sum(L.total).label("total_usd"),
            func.sum(L.input_tokens).label("input_tokens"),
            func.sum(L.output_tokens).label("output_tokens"),
        )
        .where(
            L.workspace_id == ws,
            *_window(L.run_started_at, ws, start, end),
            *_project(L.project_id, project),
        )
        .group_by(L.project_id, day, L.agent_slug, provider, model, L.source)
    )


def _span_name() -> Any:
    return case((S.kind.in_(NAMED_KINDS), func.coalesce(S.name, EMPTY)), else_=EMPTY)


def spans_select(ws: uuid.UUID, start: date, end: date, project: uuid.UUID | None) -> Select[Any]:
    day = _day(S.run_started_at)
    name = _span_name()
    return (
        select(
            S.project_id,
            day.label("day"),
            S.kind,
            name.label("name"),
            func.count().filter(S.status.is_not(None)).label("finished"),
            func.count().filter(S.status == "success").label("ok"),
        )
        .where(
            S.workspace_id == ws,
            *_window(S.run_started_at, ws, start, end),
            *_project(S.project_id, project),
            S.kind.is_not(None),
            S.kind != "agent",
        )
        .group_by(S.project_id, day, S.kind, name)
    )


def latency_select(ws: uuid.UUID, start: date, end: date, project: uuid.UUID | None) -> Any:
    """Span durations by bucket, plus run durations (series 'run')."""
    day = _day(S.run_started_at)
    name = _span_name()
    bucket = bucket_sql(S.duration_ms)
    spans = (
        select(
            S.project_id,
            day.label("day"),
            S.kind.label("series"),
            name.label("name"),
            bucket.label("bucket"),
            func.count().label("n"),
        )
        .where(
            S.workspace_id == ws,
            *_window(S.run_started_at, ws, start, end),
            *_project(S.project_id, project),
            S.kind.is_not(None),
            S.kind != "agent",
            S.duration_ms.is_not(None),
        )
        .group_by(S.project_id, day, S.kind, name, bucket)
    )
    run_day = _day(R.started_at)
    run_bucket = bucket_sql(R.duration_ms)
    runs = (
        select(
            R.project_id,
            run_day.label("day"),
            literal_column("'run'").label("series"),
            EMPTY.label("name"),
            run_bucket.label("bucket"),
            func.count().label("n"),
        )
        .where(
            R.workspace_id == ws,
            *_window(R.started_at, ws, start, end),
            *_project(R.project_id, project),
            R.status.in_(FINISHED),
            R.duration_ms.is_not(None),
        )
        .group_by(R.project_id, run_day, run_bucket)
    )
    return spans.union_all(runs)


def top_runs_select(ws: uuid.UUID, start: date, end: date, project: uuid.UUID | None) -> Any:
    """The `TOP_RUNS` costliest and most-retried runs per project and day."""
    day = _day(R.started_at)
    parts = []
    for kind, metric, positive in (
        ("cost", R.cost_usd, R.cost_usd > 0),
        ("retries", R.retry_count, R.retry_count > 0),
    ):
        ranked = (
            select(
                R.project_id.label("project_id"),
                day.label("day"),
                literal(kind).label("kind"),
                func.row_number()
                .over(partition_by=(R.project_id, day), order_by=(metric.desc(), R.id))
                .label("rank"),
                R.id.label("run_id"),
                R.cost_usd,
                R.retry_cost_usd,
                R.retry_count,
            )
            .where(
                R.workspace_id == ws,
                *_window(R.started_at, ws, start, end),
                *_project(R.project_id, project),
                positive,
            )
            .subquery()
        )
        parts.append(select(ranked).where(ranked.c.rank <= TOP_RUNS))
    return parts[0].union_all(parts[1])


# ------------------------------------------------------------------ refresh


def _short(value: Any) -> Any:
    """Client-controlled text as stored in a rollup key: bounded, so a 4,000-character model or
    tool name can never exceed the index row limit (PostgreSQL btree keys are about 2.7 KB)."""
    return value[:NAME_MAX] if isinstance(value, str) else value


def _fold(
    rows: Sequence[Any],
    key: tuple[str, ...],
    names: tuple[str, ...],
    sums: tuple[str, ...],
    partition: str | None = None,
    max_rows: int | None = None,
) -> list[dict[str, Any]]:
    """Bound a day's rollup rows whatever a client sends (KI-018, KI-055).

    Every text key is truncated to NAME_MAX characters. In each of the `names` fields, names beyond
    MAX_NAMES_PER_DAY (per project, day and `partition`, such as a span kind) fold into OTHER. Rows
    that collide after folding merge their `sums`; if more than `max_rows` rows remain for a
    project-day the smallest fold into a single OTHER row. Totals are preserved at every step.
    """

    def group_of(row: Any) -> tuple[Any, ...]:
        return (row.project_id, row.day, getattr(row, partition) if partition else None)

    keep: dict[tuple[str, tuple[Any, ...]], set[str]] = {}
    for field in names:
        totals: dict[tuple[Any, ...], dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for row in rows:
            totals[group_of(row)][_short(getattr(row, field))] += int(getattr(row, sums[0]) or 0)
        for group, counts in totals.items():
            ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
            keep[(field, group)] = {name for name, _ in ranked[:MAX_NAMES_PER_DAY]}
    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        values = {k: _short(v) for k, v in row._mapping.items()}
        for field in names:
            if values[field] not in keep[(field, group_of(row))]:
                values[field] = OTHER
        ident = tuple(values[k] for k in ("project_id", "day", *key))
        if ident in merged:
            for field in sums:
                merged[ident][field] = (merged[ident][field] or 0) + (values[field] or 0)
        else:
            merged[ident] = values
    if max_rows is None:
        return list(merged.values())
    by_day: dict[tuple[Any, Any], list[dict[str, Any]]] = defaultdict(list)
    for values in merged.values():
        by_day[(values["project_id"], values["day"])].append(values)
    result: list[dict[str, Any]] = []
    for day_rows in by_day.values():
        day_rows.sort(key=lambda v: -int(v[sums[0]] or 0))
        result.extend(day_rows[:max_rows])
        tail = day_rows[max_rows:]
        if tail:
            other = dict(tail[0])
            for field in names:
                other[field] = OTHER
            for field in sums:
                other[field] = sum((v[field] or 0 for v in tail), 0)
            result.append(other)
    return result


async def refresh_day(conn: AsyncConnection, tenant: TenantContext, day: date) -> int:
    """Rewrite the rollup rows of one workspace and UTC day from the derived tables.

    Idempotent, and serialised per workspace-day by an advisory lock so two refreshes of the same
    day cannot interleave. A row the database refuses is skipped, counted and logged (value-free):
    one hostile row must never keep a whole day from rendering. Returns how many rows were skipped.
    """
    ws = tenant.workspace_id
    end = day + timedelta(days=1)
    await conn.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(f"analytics:{ws}:{day}", 0)))
    )
    for table in (
        t.analytics_runs_daily,
        t.analytics_cost_daily,
        t.analytics_spans_daily,
        t.analytics_latency_daily,
        t.analytics_top_runs,
    ):
        await conn.execute(delete(table).where(table.c.workspace_id == ws, table.c.day == day))

    def with_ws(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{"workspace_id": ws, **row} for row in rows]

    skipped = 0
    runs = (await conn.execute(runs_select(ws, day, end, None))).all()
    folded = _fold(
        runs, ("agent_slug", "status"), ("agent_slug",), RUN_SUMS, max_rows=MAX_ROWS_PER_DAY
    )
    skipped += await _insert(conn, t.analytics_runs_daily, with_ws(folded))

    cost = (await conn.execute(cost_select(ws, day, end, None))).all()
    folded = _fold(
        cost,
        ("agent_slug", "provider", "model", "source"),
        ("agent_slug", "provider", "model"),
        ("calls", "total_usd", "input_tokens", "output_tokens"),
        max_rows=MAX_ROWS_PER_DAY,
    )
    skipped += await _insert(conn, t.analytics_cost_daily, with_ws(folded))

    spans = (await conn.execute(spans_select(ws, day, end, None))).all()
    folded = _fold(spans, ("kind", "name"), ("name",), ("finished", "ok"), partition="kind")
    skipped += await _insert(conn, t.analytics_spans_daily, with_ws(folded))

    latency = (await conn.execute(latency_select(ws, day, end, None))).all()
    folded = _fold(latency, ("series", "name", "bucket"), ("name",), ("n",), partition="series")
    skipped += await _insert(conn, t.analytics_latency_daily, with_ws(folded))

    top = (await conn.execute(top_runs_select(ws, day, end, None))).all()
    skipped += await _insert(conn, t.analytics_top_runs, with_ws([dict(r._mapping) for r in top]))
    if skipped:
        logger.warning(
            "analytics rollup skipped rows the database refused",
            extra={"workspace_id": str(ws), "day": day.isoformat(), "skipped": skipped},
        )
    return skipped


async def _insert(conn: AsyncConnection, table: Any, rows: list[dict[str, Any]]) -> int:
    """Insert rows; a chunk the database refuses is retried row by row and bad rows are skipped."""
    columns = {c.name for c in table.columns}
    chunk = max(1, 30000 // max(len(columns), 1))
    skipped = 0
    for start in range(0, len(rows), chunk):
        batch = [{k: v for k, v in r.items() if k in columns} for r in rows[start : start + chunk]]
        try:
            async with conn.begin_nested():
                await conn.execute(insert(table).values(batch))
        except DBAPIError:
            for row in batch:
                try:
                    async with conn.begin_nested():
                        await conn.execute(insert(table).values([row]))
                except DBAPIError:
                    skipped += 1
    return skipped

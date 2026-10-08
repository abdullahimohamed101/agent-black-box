"""Rollups (ADR-043): percentiles, stored == live, rebuildability, caps, refresh triggers."""

import math
import random
from datetime import date, timedelta
from typing import Any

import pytest
from abb_event_schema.ids import IdKind, from_uuid
from sqlalchemy import literal_column, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.analytics import percentiles, rollup
from abb_api.analytics.percentiles import bucket_of, percentile
from abb_api.analytics.postgres import PostgresAnalyticsStore
from abb_api.analytics.rollup import refresh_day
from abb_api.analytics.store import AnalyticsScope
from abb_api.db import tables as t
from abb_api.tenancy import TenantContext
from tests.analytics_fixtures import D6, D7, Seeder
from tests.api_fixtures import Api
from tests.ingest_helpers import wire_event

TABLES = (
    t.analytics_runs_daily,
    t.analytics_cost_daily,
    t.analytics_spans_daily,
    t.analytics_latency_daily,
    t.analytics_top_runs,
)


# ------------------------------------------------------------------ histogram percentiles


def histogram(values: list[float]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for v in values:
        counts[bucket_of(v)] = counts.get(bucket_of(v), 0) + 1
    return counts


def exact(values: list[float], q: float) -> float:
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_percentiles_are_within_a_few_percent_of_exact(seed: int) -> None:
    rng = random.Random(seed)
    values = [rng.lognormvariate(6, 1.5) for _ in range(5000)]
    counts = histogram(values)
    for q in (0.5, 0.9, 0.95, 0.99):
        got, want = percentile(counts, q), exact(values, q)
        assert got is not None and abs(got - want) <= want * 0.06, (q, got, want)


def test_small_samples_and_edges() -> None:
    assert percentile({}, 0.5) is None
    four = histogram([10_000, 20_000, 30_000, 40_000])
    assert percentile(four, 0.5) == pytest.approx(25_000, rel=0.06)
    assert percentile(four, 0.95) == pytest.approx(38_500, rel=0.06)
    assert percentile(histogram([123.0]), 0.95) == pytest.approx(123.0, rel=0.06)
    assert percentile(histogram([0.2, 0.4]), 0.5) is not None  # sub-millisecond bucket
    assert percentile(histogram([10**9]), 0.5) is not None  # beyond the last bucket


async def test_the_python_bucket_function_matches_the_sql_one(engine: AsyncEngine) -> None:
    values = [
        0.0,
        0.3,
        0.5,
        1.0,
        1.5,
        9.99,
        10.0,
        123.456,
        1000.0,
        59_999.0,
        3_599_999.0,
        3_600_000.0,
        10**8,
    ]
    async with engine.connect() as conn:
        for v in values:
            sql = (
                await conn.execute(
                    select(
                        percentiles.bucket_sql(literal_column(f"CAST({v!r} AS double precision)"))
                    )
                )
            ).scalar_one()
            assert sql == bucket_of(v), v


# ------------------------------------------------------------------ stored == live


def scope(
    api: Api, today: date, start: date | None = None, end: date | None = None
) -> AnalyticsScope:
    start, end = start or D6.date(), end or D7.date()
    ctx = TenantContext(api.tenant.context.workspace_id)
    return AnalyticsScope(ctx, start, end, today)


async def test_a_finished_day_reads_the_same_from_rollups_and_live(seeded: Api) -> None:
    """Yesterday as stored rollup rows (today = D7) equals yesterday computed live (today = D6)."""
    store = PostgresAnalyticsStore(seeded.engine)
    stored, live = scope(seeded, D7.date()), scope(seeded, D6.date())
    for method in ("summary", "cost", "reliability", "performance"):
        a = await getattr(store, method)(stored, **({} if method == "summary" else {"top": 10}))
        b = await getattr(store, method)(live, **({} if method == "summary" else {"top": 10}))
        assert a.model_dump() == b.model_dump(), method
    # and the day really was read from the stored rows
    async with seeded.engine.connect() as conn:
        n = (
            await conn.execute(select(text("count(*)")).select_from(t.analytics_runs_daily))
        ).scalar_one()
    assert n > 0


async def test_today_is_never_read_from_stored_rows(seeded: Api) -> None:
    store = PostgresAnalyticsStore(seeded.engine)
    async with seeded.engine.begin() as conn:
        for table in TABLES:  # wipe every rollup row: stored days vanish, today is unaffected
            await conn.execute(table.delete())
    today_only = await store.summary(
        scope(seeded, D7.date(), D7.date(), D7.date() + timedelta(days=1))
    )
    assert today_only.runs.total == 6  # alpha 5 + beta 1, workspace-wide
    assert today_only.cost.total_usd == pytest.approx(9.25)
    yesterday = await store.summary(scope(seeded, D7.date()))
    assert (
        yesterday.runs.total == 0
    )  # stored rows are gone, proving that yesterday is read from them


# ------------------------------------------------------------------ rebuildable


async def snapshot(engine: AsyncEngine) -> dict[str, list[tuple[Any, ...]]]:
    out = {}
    async with engine.connect() as conn:
        for table in TABLES:
            rows = (await conn.execute(select(table))).all()
            out[table.name] = sorted(tuple(map(str, r)) for r in rows)
    return out


async def test_rollups_are_rebuilt_exactly_from_the_derived_tables(seeded: Api) -> None:
    before = await snapshot(seeded.engine)
    assert all(before[name] for name in before)  # every kind has rows
    async with seeded.engine.begin() as conn:
        for table in TABLES:
            await conn.execute(table.delete())
    assert not any((await snapshot(seeded.engine)).values())
    async with seeded.engine.begin() as conn:
        for day in (D6.date(), D7.date()):
            await refresh_day(conn, TenantContext(seeded.tenant.context.workspace_id), day)
        for day in (D7.date(),):  # the other tenant's day too: rows are per workspace
            await refresh_day(conn, TenantContext(seeded.other.context.workspace_id), day)
    after = await snapshot(seeded.engine)
    for name in before:
        assert sorted(after[name]) == sorted(before[name]), name


async def test_refresh_is_idempotent(seeded: Api) -> None:
    before = await snapshot(seeded.engine)
    async with seeded.engine.begin() as conn:
        for _ in range(2):
            await refresh_day(conn, TenantContext(seeded.tenant.context.workspace_id), D7.date())
    assert await snapshot(seeded.engine) == before


async def test_rollup_rows_never_cross_workspaces(seeded: Api) -> None:
    async with seeded.engine.connect() as conn:
        for table in TABLES:
            mine = await conn.execute(
                select(table.c.project_id).where(
                    table.c.workspace_id == seeded.tenant.context.workspace_id
                )
            )
            projects = {r.project_id for r in mine}
            assert projects <= set(seeded.tenant.project_uuids.values()), table.name


# ------------------------------------------------------------------ caps and triggers


async def test_client_controlled_names_are_capped_per_day(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rollup, "MAX_NAMES_PER_DAY", 3)
    llm = [
        {
            "llm.provider": "p",
            "llm.model": f"m{i}",
            "llm.input_tokens": 1,
            "cost.estimated_usd": 0.1,
        }
        for i in range(8)
    ]
    # `llm` calls are listed in order; the first model gets two calls so ranking is deterministic
    await Seeder(api, "writer").run(
        D6, llm=[*llm, llm[0]], tools=[(f"tool{i}", "success", 5) for i in range(8)]
    )
    await api.drain()
    async with api.engine.connect() as conn:
        models = {r.model for r in await conn.execute(select(t.analytics_cost_daily.c.model))}
        tools = {
            r.name
            for r in await conn.execute(
                select(t.analytics_spans_daily.c.name).where(
                    t.analytics_spans_daily.c.kind == "tool"
                )
            )
        }
        total = (
            await conn.execute(select(text("sum(total_usd)")).select_from(t.analytics_cost_daily))
        ).scalar_one()
    assert len(models) == 4 and rollup.OTHER in models  # 3 kept + other
    assert len(tools) == 4 and rollup.OTHER in tools
    assert float(total) == pytest.approx(0.9)  # folding never loses cost


async def runs_by_day(engine: AsyncEngine) -> list[tuple[date, int]]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            select(t.analytics_runs_daily.c.day, t.analytics_runs_daily.c.runs).order_by(
                t.analytics_runs_daily.c.day
            )
        )
        return [(r.day, r.runs) for r in rows]


async def test_a_late_event_for_an_earlier_day_refreshes_both_days(api: Api) -> None:
    first = {
        "llm.provider": "p",
        "llm.model": "m",
        "llm.input_tokens": 1,
        "cost.estimated_usd": 1.0,
    }
    run_id = await Seeder(api, "writer").run(D7, llm=[first])
    await api.drain()
    assert await runs_by_day(api.engine) == [(D7.date(), 1)]
    # a straggler event from the previous day moves the run's start (earliest event) one day back
    async with api.engine.connect() as conn:
        trace = (await conn.execute(select(t.runs.c.trace_id))).scalar_one()
    late = wire_event(
        {"run_id": run_id, "trace_id": from_uuid(IdKind.TRACE, trace)}, 99,
        event_type="tool.call.completed", attributes={"tool.name": "t"},
        occurred_at=D6.isoformat().replace("+00:00", "Z"),
    )  # fmt: skip
    assert (await api.post_batch([late])).status_code == 202
    await api.drain()
    # the old day's row is gone and the new day's row exists
    assert await runs_by_day(api.engine) == [(D6.date(), 1)]


async def test_the_cli_rebuilds_rollups_after_they_are_lost(seeded: Api, database_url: str) -> None:
    from tests.conftest import make_settings
    from tests.test_cli import invoke

    before = await snapshot(seeded.engine)
    async with seeded.engine.begin() as conn:
        for table in TABLES:
            await conn.execute(table.delete())
    code, _, err = await invoke(
        make_settings(database_url), "refresh-analytics", "--workspace", "acme"
    )
    assert code == 0 and "2 day(s)" in err
    after = await snapshot(seeded.engine)
    acme = seeded.tenant.context.workspace_id
    async with seeded.engine.connect() as conn:  # only acme was rebuilt: globex's rows stay gone
        other = (
            await conn.execute(
                select(text("count(*)"))
                .select_from(t.analytics_runs_daily)
                .where(t.analytics_runs_daily.c.workspace_id == seeded.other.context.workspace_id)
            )
        ).scalar_one()
    assert other == 0 and acme != seeded.other.context.workspace_id
    assert {k: len(v) for k, v in after.items()} != {} and all(after[k] for k in after)
    assert set(before) == set(after)

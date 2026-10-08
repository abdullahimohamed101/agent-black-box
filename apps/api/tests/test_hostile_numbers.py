"""Hostile numeric values must never poison a run or a workspace-day of analytics."""

import json
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select

from abb_api.analytics.rollup import refresh_day
from abb_api.cost.engine import MAX_LINE_USD, CostEngine
from abb_api.cost.pricing import PriceBook
from abb_api.db import tables as t
from tests.analytics_fixtures import D7, Seeder
from tests.api_fixtures import Api
from tests.ingest_helpers import make_run_ids, wire_event
from tests.test_cost_engine import llm


def call(**attrs: Any) -> dict[str, Any]:
    base = {"llm.provider": "example-provider", "llm.model": "model-x", "llm.input_tokens": 1}
    return {**base, **attrs}


@pytest.mark.parametrize("value", [5e10, 1e11, 1e12, 1e15, 9e15])
async def test_huge_reported_costs_never_block_the_run_or_the_day(api: Api, value: float) -> None:
    """Two runs of 5e10 overflowed numeric(20,9) in the day's rollup: the whole day showed $0."""
    seeder = Seeder(api, "writer")
    for _ in range(3):  # several runs in one day: their sum must not overflow either
        await seeder.run(D7, llm=[call(**{"cost.provider_usd": value})])
    await seeder.run(D7, llm=[call(**{"llm.input_tokens": 1_000_000})])  # a normal $3 run
    await api.drain()
    async with api.engine.connect() as conn:
        versions = [r.summary_version for r in await conn.execute(select(t.runs.c.summary_version))]
        unfinished = (
            await conn.execute(
                select(t.outbox_jobs.c.status).where(
                    t.outbox_jobs.c.status.in_(("pending", "running", "dead_letter"))
                )
            )
        ).all()
        largest = (await conn.execute(select(func.max(t.cost_calculations.c.total)))).scalar_one()
    assert all(v > 0 for v in versions), "a hostile run stayed unsummarized"
    assert not unfinished, "a job keeps failing and retrying"
    assert largest <= MAX_LINE_USD
    body = (await api.get("/v1/analytics/cost")).json()
    assert body["headline"]["total_usd"] >= 3.0  # the day renders, normal run included
    assert (await api.get("/v1/analytics/summary")).json()["runs"]["total"] == 4


@pytest.mark.parametrize("value", [1e16, 1e19, 1e20, 1e30, 1e300, -1.0, -1e30, float("inf")])
async def test_out_of_range_numbers_are_rejected_at_ingestion(api: Api, value: float) -> None:
    """Poison-run pattern: 1e19 was accepted, stored as a JSONB integer, then failed to reload."""
    event = wire_event(
        make_run_ids(), 1, event_type="llm.request.completed",
        attributes=call(**{"cost.provider_usd": value}),
    )  # fmt: skip
    response = await api.client.post(
        "/v1/events/batch",
        content=json.dumps({"events": [event]}).encode(),
        headers=api.headers("writer"),
    )
    assert response.status_code in (202, 207, 422)
    assert response.json().get("accepted", 0) == 0
    async with api.engine.connect() as conn:
        assert (await conn.execute(select(t.events.c.event_id))).first() is None


@pytest.mark.parametrize("value", [1e7, 5e10, 9e15, float(2**52)])
def test_the_engine_clamps_any_single_figure_and_flags_it(value: float) -> None:
    engine = CostEngine(PriceBook([]))
    line = engine.calculate(llm({"cost.provider_usd": value}, raw=True))
    assert line.total == MAX_LINE_USD and line.clamped
    estimate = engine.calculate(llm({"cost.estimated_usd": value}, raw=True))
    assert estimate.total == MAX_LINE_USD and estimate.clamped


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), -1.0, -1e30, True])
def test_the_engine_ignores_nan_inf_negative_and_boolean_costs(value: Any) -> None:
    line = CostEngine(PriceBook([])).calculate(llm({"cost.provider_usd": value}, raw=True))
    assert line.source == "unpriced" and line.total == 0 and not line.clamped


async def test_a_refresh_survives_values_the_old_column_could_not_hold(api: Api) -> None:
    seeder = Seeder(api, "writer")
    await seeder.run(D7, llm=[call(**{"llm.input_tokens": 1_000_000})])
    await api.drain()
    async with api.engine.begin() as conn:
        await conn.execute(t.cost_calculations.update().values(total=Decimal("1" + "0" * 20)))
        await conn.execute(t.runs.update().values(cost_usd=Decimal("1" + "0" * 20)))
    async with api.engine.begin() as conn:
        await refresh_day(conn, api.tenant.context, D7.date())
    assert (await api.get("/v1/analytics/summary")).json()["runs"]["total"] == 1

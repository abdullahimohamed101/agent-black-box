"""Cost lines through the worker: events -> summarizer -> `cost_calculations` (INV-2)."""

import random
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from abb_event_schema.event import Event
from abb_event_schema.ids import IdKind, new_id, to_uuid
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.cost.repository import CostRepository
from abb_api.db import tables as t
from abb_api.jobs.outbox import OutboxRepository
from abb_api.runs.summary import derive_run
from tests.ingest_helpers import Tenant, build_event, make_run_ids, make_tenant
from tests.test_summarizer import drain, ingest, run_row, worker_for

D = Decimal
PRICED = {"llm.provider": "example-provider", "llm.model": "model-x"}


def scenario(tenant: Tenant, run: dict[str, str]) -> list[Event]:
    """Initial call, a retry of the same operation, a second priced call, and an unpriced model."""
    scope, inside, other = (new_id(IdKind.SPAN) for _ in range(3))

    def ev(n: int, kind: str, attrs: dict[str, Any] | None = None, **kw: Any) -> Event:
        return build_event(tenant, run, n=n, event_type=kind, attributes=attrs or {}, **kw)

    first = {**PRICED, "llm.input_tokens": 1_000_000, "llm.output_tokens": 100_000}
    again = {**PRICED, "llm.input_tokens": 500_000, "cost.provider_usd": 1.25}
    odd = {
        "llm.provider": "x",
        "llm.model": "mystery",
        "llm.input_tokens": 9,
        "cost.estimated_usd": 0.5,
    }
    return [
        ev(1, "run.started", span_id=...),
        ev(2, "tool.call.started", {"tool.name": "fix"}, span_id=scope),
        ev(3, "llm.request.completed", first, span_id=inside, parent_span_id=scope),
        ev(4, "retry.attempted", {"retry.attempt": 1}, span_id=scope),
        ev(5, "llm.request.completed", again, span_id=new_id(IdKind.SPAN), parent_span_id=scope),
        ev(6, "llm.request.completed", odd, span_id=other),
        ev(7, "tool.call.completed", {"tool.name": "fix"}, span_id=scope, status="success"),
        ev(8, "run.completed", span_id=...),
    ]


async def lines(engine: AsyncEngine, run_id: str) -> dict[uuid.UUID, Any]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            select(t.cost_calculations).where(t.cost_calculations.c.run_id == to_uuid(run_id))
        )
        return {r.event_id: r for r in rows}


async def test_a_run_gets_priced_lines_with_sources_versions_and_retry_flags(
    engine: AsyncEngine, database_url: str
) -> None:
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    events = scenario(tenant, run)
    await ingest(engine, tenant, events)
    await drain(worker_for(engine, database_url))

    stored = await lines(engine, run["run_id"])
    first, again, odd = (stored[to_uuid(events[i].event_id)] for i in (2, 4, 5))
    assert (first.source, first.total, first.pricing_version) == (
        "estimated",
        D("4.5"),
        "2026-10-01",
    )
    assert first.is_retry is False and first.input_cost == D("3.0")
    assert (again.source, again.total, again.is_retry) == ("provider_reported", D("1.25"), True)
    assert again.estimated_total == D("1.5") and again.reported_total == D("1.25")
    assert (odd.source, odd.total, odd.is_retry) == ("client_estimate", D("0.5"), False)

    row = await run_row(engine, run["run_id"])
    assert row.summary["estimated_cost_usd"] == 6.25
    assert row.summary["retry_cost_usd"] == 1.25 and row.summary["initial_cost_usd"] == 5.0
    assert row.summary["cost_by_source_usd"] == {
        "provider_reported": 1.25,
        "estimated": 4.5,
        "client_estimate": 0.5,
    }
    assert sum(r.total for r in stored.values()) == D("6.25")
    assert all(r.run_started_at == row.started_at for r in stored.values())


async def test_cost_lines_are_a_pure_function_of_the_events(
    engine: AsyncEngine, database_url: str
) -> None:
    """INV-2: any batching or arrival order, and a delete + rebuild, converge on the same lines."""
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    events = scenario(tenant, run)
    expected = {ln.event_id: ln for ln in derive_run(events).cost_lines}
    shuffled = events[:]
    random.Random(7).shuffle(shuffled)
    for start in range(0, len(shuffled), 3):  # several batches, each with its own job
        await ingest(engine, tenant, shuffled[start : start + 3])
    worker = worker_for(engine, database_url)
    await drain(worker)

    def snapshot(rows: dict[Any, Any]) -> dict[str, tuple[Any, ...]]:
        return {
            str(k): (r.source, r.total, r.pricing_version, r.is_retry, r.estimated_total)
            for k, r in rows.items()
        }

    first = await lines(engine, run["run_id"])
    assert len(first) == len(expected) == 3
    for ln in expected.values():
        r = first[to_uuid(ln.event_id)]
        assert (r.source, r.total, r.is_retry) == (ln.source, ln.total, ln.is_retry)

    async with engine.begin() as conn:  # lose the derived rows, then rebuild from events alone
        await conn.execute(delete(t.cost_calculations))
    async with engine.begin() as conn:
        await OutboxRepository(conn, tenant.context).enqueue_summarize([to_uuid(run["run_id"])])
    await drain(worker)
    assert snapshot(await lines(engine, run["run_id"])) == snapshot(first)


async def test_a_workspace_override_reprices_on_rebuild_and_is_versioned(
    engine: AsyncEngine, database_url: str
) -> None:
    tenant, other = await make_tenant(engine, "acme"), await make_tenant(engine, "globex")
    run = make_run_ids()
    events = scenario(tenant, run)
    await ingest(engine, tenant, events)
    async with engine.begin() as conn:
        # globex's override must never price acme's calls
        await CostRepository(conn, other.context).add_override(
            project_id=None, provider=None, model_pattern="model-x", input_per_million=D(100),
            output_per_million=D(100), cached_input_per_million=None, request_price=D(0),
            valid_from=datetime(2020, 1, 1, tzinfo=UTC), note="other tenant",
        )  # fmt: skip
        mine = await CostRepository(conn, tenant.context).add_override(
            project_id=None, provider="example-provider", model_pattern="model-x",
            input_per_million=D(1), output_per_million=D(2), cached_input_per_million=None,
            request_price=D(0), valid_from=datetime(2020, 1, 1, tzinfo=UTC), note="negotiated",
        )  # fmt: skip
    await drain(worker_for(engine, database_url))
    first = (await lines(engine, run["run_id"]))[to_uuid(events[2].event_id)]
    assert first.total == D("1.2") and first.pricing_version == f"override:{mine}"
    assert first.pricing_origin == "override"


async def test_a_project_override_only_applies_to_its_project(
    engine: AsyncEngine, database_url: str
) -> None:
    tenant = await make_tenant(engine, "acme", projects=("a", "b"))
    async with engine.begin() as conn:
        await CostRepository(conn, tenant.context).add_override(
            project_id=tenant.project_uuids["a"], provider=None, model_pattern="model-x",
            input_per_million=D(1), output_per_million=D(1), cached_input_per_million=None,
            request_price=D(0), valid_from=datetime(2020, 1, 1, tzinfo=UTC), note=None,
        )  # fmt: skip
    in_a, in_b = make_run_ids(), make_run_ids()
    for run, project in ((in_a, "a"), (in_b, "b")):
        call = {**PRICED, "llm.input_tokens": 1_000_000}
        await ingest(
            engine,
            tenant,
            [
                build_event(
                    tenant,
                    run,
                    n=1,
                    event_type="llm.request.completed",
                    attributes=call,
                    project=project,
                )
            ],
        )
    await drain(worker_for(engine, database_url))
    (a,) = (await lines(engine, in_a["run_id"])).values()
    (b,) = (await lines(engine, in_b["run_id"])).values()
    assert (a.total, b.total) == (D("1.0"), D("3.0"))

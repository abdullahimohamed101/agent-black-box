"""Per-event priced cost on the events read path (Phase 12, ADR-121): replay parity.

The cost engine prices every completed model call into `cost_calculations`; the events list and
detail now carry that settled figure so a client can add it up and get the run summary's
`estimated_cost_usd` exactly, whatever the caller did or did not put in the event's attributes.
"""

from decimal import Decimal
from typing import Any

from abb_event_schema.ids import to_uuid
from sqlalchemy import text

from abb_api.cost.repository import CostRepository
from tests.api_fixtures import Api
from tests.ingest_helpers import make_run_ids, wire_event

KNOWN = {"llm.provider": "example-provider", "llm.model": "model-x"}  # in the built-in price book
MYSTERY = {"llm.provider": "x", "llm.model": "mystery", "llm.input_tokens": 9}


def run_events(ids: dict[str, str]) -> list[dict[str, Any]]:
    def call(n: int, attrs: dict[str, Any]) -> dict[str, Any]:
        return wire_event(
            ids, n, event_type="llm.request.completed", agent_id="ab", status="success",
            attributes=attrs,
        )  # fmt: skip

    return [
        wire_event(ids, 1, event_type="run.started", agent_id="ab", span_id=..., attributes={}),
        # a plain SDK call: tokens and a model, no cost attribute at all (the price book prices it)
        call(2, {**KNOWN, "llm.input_tokens": 1_000_000, "llm.output_tokens": 100_000}),
        call(3, {**KNOWN, "llm.input_tokens": 500_000, "cost.provider_usd": 1.25}),
        call(4, {**MYSTERY, "cost.estimated_usd": 0.5}),
        call(5, MYSTERY),  # nothing can price it
        wire_event(
            ids, 6, event_type="tool.call.completed", agent_id="ab", attributes={"tool.name": "t"}
        ),
        wire_event(ids, 7, event_type="run.completed", agent_id="ab", span_id=..., attributes={}),
    ]


async def listed(api: Api, run_id: str, token: str = "reader") -> list[dict[str, Any]]:
    response = await api.get(f"/v1/runs/{run_id}/events", token=token, limit=100)
    assert response.status_code == 200, response.text
    return response.json()["items"]  # type: ignore[no-any-return]


async def test_events_carry_the_settled_cost_and_add_up_to_the_run_summary(api: Api) -> None:
    ids = make_run_ids()
    assert (await api.post_batch(run_events(ids))).status_code == 202
    await api.drain()

    items = await listed(api, ids["run_id"])
    by_seq = {e["sequence"]: e for e in items}
    # price book, provider-reported, the caller's estimate, unpriced (ADR-040 precedence)
    assert by_seq[2]["cost_usd"] == 4.5 and by_seq[2]["cost_unpriced"] is False
    assert by_seq[3]["cost_usd"] == 1.25 and by_seq[3]["cost_unpriced"] is False
    assert by_seq[4]["cost_usd"] == 0.5 and by_seq[4]["cost_unpriced"] is False
    assert by_seq[5]["cost_usd"] == 0 and by_seq[5]["cost_unpriced"] is True
    # not a model call: no cost line, so no figure and not "unpriced"
    for n in (1, 6, 7):
        assert by_seq[n]["cost_usd"] is None and by_seq[n]["cost_unpriced"] is False

    summary = (await api.get(f"/v1/runs/{ids['run_id']}")).json()["summary"]
    total = sum(Decimal(str(e["cost_usd"])) for e in items if e["cost_usd"] is not None)
    assert float(total) == summary["estimated_cost_usd"] == 6.25
    assert sum(1 for e in items if e["cost_unpriced"]) == summary["unpriced_calls"] == 1

    detail = (await api.get(f"/v1/runs/{ids['run_id']}/events/{by_seq[2]['event_id']}")).json()
    assert detail["cost_usd"] == 4.5 and detail["cost_unpriced"] is False


async def test_a_page_of_events_costs_one_lookup_not_one_per_event(api: Api) -> None:
    ids = make_run_ids()
    assert (await api.post_batch(run_events(ids))).status_code == 202
    await api.drain()
    seen: list[str] = []
    original = CostRepository.event_costs

    async def counting(self: CostRepository, run_id: Any, event_ids: Any) -> Any:
        seen.append(f"{len(event_ids)}")
        return await original(self, run_id, event_ids)

    CostRepository.event_costs = counting  # type: ignore[method-assign]
    try:
        await listed(api, ids["run_id"])
    finally:
        CostRepository.event_costs = original  # type: ignore[method-assign]
    assert seen == ["4"]  # one call for the page, covering exactly its four model calls


async def test_events_have_no_cost_until_the_run_is_summarised(api: Api) -> None:
    ids = make_run_ids()
    assert (await api.post_batch(run_events(ids))).status_code == 202
    items = await listed(api, ids["run_id"])  # the worker has not run
    assert len(items) == 7
    assert all(e["cost_usd"] is None and e["cost_unpriced"] is False for e in items)


async def test_cost_lines_are_read_inside_the_callers_workspace_only(api: Api) -> None:
    ids = make_run_ids()
    assert (await api.post_batch(run_events(ids))).status_code == 202
    await api.drain()
    calls = [e["event_id"] for e in await listed(api, ids["run_id"]) if e["cost_usd"] is not None]
    assert len(calls) == 4
    async with api.engine.connect() as conn:
        mine = await CostRepository(conn, api.tenant.context).event_costs(
            to_uuid(ids["run_id"]), [to_uuid(c) for c in calls]
        )
        theirs = await CostRepository(conn, api.other.context).event_costs(
            to_uuid(ids["run_id"]), [to_uuid(c) for c in calls]
        )
    assert len(mine) == 4 and theirs == {}


async def test_the_cost_lookup_uses_the_primary_key(api: Api) -> None:
    """A page asks for up to 500 ids: with a large table around it must stay an index lookup."""
    ids = make_run_ids()
    assert (await api.post_batch(run_events(ids))).status_code == 202
    await api.drain()
    async with api.engine.begin() as conn:
        await conn.execute(
            text(
                """INSERT INTO cost_calculations
                       (workspace_id, event_id, run_id, project_id, agent_slug, occurred_at,
                        run_started_at, input_tokens, output_tokens, cached_input_tokens, source,
                        total)
                   SELECT CAST(:ws AS uuid), gen_random_uuid(), CAST(:run AS uuid),
                          CAST(:prj AS uuid), 'a', now(), now(), 1, 1, 0, 'estimated', 1
                   FROM generate_series(1, 200000)"""
            ),
            {
                "ws": str(api.tenant.context.workspace_id),
                "run": str(to_uuid(ids["run_id"])),
                "prj": str(api.tenant.project_uuids["alpha"]),
            },
        )
        await conn.execute(text("ANALYZE cost_calculations"))
        plan = await conn.execute(
            text(
                """EXPLAIN SELECT event_id, total, source FROM cost_calculations
                   WHERE workspace_id = CAST(:ws AS uuid) AND run_id = CAST(:run AS uuid)
                     AND event_id = ANY(CAST(:ids AS uuid[]))"""
            ),
            {
                "ws": str(api.tenant.context.workspace_id),
                "run": str(to_uuid(ids["run_id"])),
                "ids": [f"00000000-0000-0000-0000-{i:012d}" for i in range(500)],
            },
        )
        text_plan = "\n".join(row[0] for row in plan)
    assert "Index" in text_plan and "Seq Scan" not in text_plan, text_plan[:600]

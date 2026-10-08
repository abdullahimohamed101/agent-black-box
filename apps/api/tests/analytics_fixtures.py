"""Seeded runs for the analytics tests: ingestion -> worker, with a hand-worked scenario."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from abb_event_schema.ids import IdKind, new_id

from tests.api_fixtures import Api
from tests.ingest_helpers import make_run_ids, wire_event

D7 = datetime(2026, 10, 7, 10, 0, 0, tzinfo=UTC)  # "today" in the scenario (the API clock is 12:05)
D6 = D7 - timedelta(days=1)
X = {"llm.provider": "example-provider", "llm.model": "model-x"}  # $3 in / $15 out per million


RUNS: dict[str, str] = {}  # scenario run name -> run id (rebuilt by the fixture)


class Seeder:
    def __init__(self, api: Api, token: str) -> None:
        self.api, self.token = api, token

    async def run(
        self,
        start: datetime,
        *,
        agent: str = "a1",
        end: str | None = "success",  # run.completed status; None = still running
        seconds: int = 10,
        llm: list[dict[str, Any]] | None = None,
        tools: list[tuple[str, str, float]] | None = None,
        retry_scope: bool = False,
    ) -> str:
        ids = make_run_ids()
        n = 0
        events: list[dict[str, Any]] = []

        def add(kind: str, at: datetime, **kw: Any) -> None:
            nonlocal n
            n += 1
            events.append(
                wire_event(
                    ids,
                    n,
                    event_type=kind,
                    agent_id=agent,
                    occurred_at=at.isoformat().replace("+00:00", "Z"),
                    **kw,
                )
            )

        add("run.started", start, span_id=..., attributes={})
        scope = new_id(IdKind.SPAN)
        if retry_scope:
            add("tool.call.started", start, span_id=scope, attributes={"tool.name": "fix"})
        for i, attrs in enumerate(llm or []):
            if retry_scope and i == 1:  # the second call comes after a retry of the scope
                add("retry.attempted", start, span_id=scope, attributes={"retry.attempt": 1})
            add(
                "llm.request.completed", start, status="success", duration_ms=2000,
                span_id=new_id(IdKind.SPAN), parent_span_id=scope if retry_scope else ...,
                attributes=attrs,
            )  # fmt: skip
        if retry_scope:
            add("tool.call.completed", start, span_id=scope, status="success", duration_ms=50,
                attributes={"tool.name": "fix"})  # fmt: skip
        for name, status, ms in tools or []:
            kind = "tool.call.completed" if status == "success" else "tool.call.failed"
            add(kind, start, span_id=new_id(IdKind.SPAN), status=status, duration_ms=ms,
                attributes={"tool.name": name})  # fmt: skip
        if end is not None:
            add("run.completed", start + timedelta(seconds=seconds), span_id=..., status=end,
                attributes={})  # fmt: skip
        response = await self.api.post_batch(events, token=self.token)
        assert response.status_code == 202, response.text
        return ids["run_id"]


@pytest.fixture
async def seeded(api: Api) -> Api:
    alpha, beta, other = Seeder(api, "writer"), Seeder(api, "beta"), Seeder(api, "other")
    t1 = {**X, "llm.input_tokens": 1_000_000}  # $3.00
    half = {**X, "llm.input_tokens": 500_000}  # $1.50
    mini = {
        "llm.provider": "example-provider", "llm.model": "model-x-mini-1",
        "llm.input_tokens": 1_000_000, "llm.output_tokens": 1_000_000,
    }  # fmt: skip  # $0.25 + $1.25
    odd = {
        "llm.provider": "x",
        "llm.model": "mystery",
        "llm.input_tokens": 9,
        "cost.estimated_usd": 0.25,
    }
    RUNS.clear()
    RUNS["r1"] = await alpha.run(
        D7, seconds=10, llm=[t1], tools=[("git", "success", 100), ("sh", "error", 300)]
    )
    RUNS["r2"] = await alpha.run(D7, end="error", seconds=20, llm=[half, half], retry_scope=True)
    RUNS["r3"] = await alpha.run(D7, agent="a2", end="timeout", seconds=30, llm=[odd])
    RUNS["r4"] = await alpha.run(
        D6, agent="a2", seconds=40, llm=[mini], tools=[("git", "success", 200)]
    )
    RUNS["r5"] = await alpha.run(D7, end="cancelled")
    RUNS["r6"] = await alpha.run(D7, agent="a3", end=None)
    RUNS["r7"] = await beta.run(D7, llm=[t1])
    await other.run(D7, llm=[{**X, "llm.input_tokens": 100_000_000}])
    await api.drain()
    return api

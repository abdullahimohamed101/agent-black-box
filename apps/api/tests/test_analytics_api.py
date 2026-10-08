"""Analytics end to end on seeded runs: ingestion -> worker -> `/v1/analytics/*`.

Expected numbers are worked out by hand from the scenario below (not read back from the code under
test), including the tenant and project isolation rules.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from abb_event_schema.ids import IdKind, new_id
from sqlalchemy import text

from abb_api.analytics.postgres import PostgresAnalyticsStore, analytics_timeout
from abb_api.analytics.store import AnalyticsScope
from abb_api.core.errors import AppError
from abb_api.tenancy import TenantContext
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


def approx(value: float | None, expected: float) -> bool:
    return value is not None and abs(value - expected) < 1e-4


async def get(api: Api, kind: str, token: str = "reader", **params: Any) -> Any:
    response = await api.get(f"/v1/analytics/{kind}", token=token, **params)
    assert response.status_code == 200, response.text
    return response.json()


async def test_summary_figures_for_a_project_key(seeded: Api) -> None:
    body = await get(seeded, "summary")
    assert body["runs"] == {
        "total": 6, "active": 1, "finished": 4, "success": 2, "failed": 1,
        "timed_out": 1, "blocked": 0, "cancelled": 1,
    }  # fmt: skip
    rates = body["rates"]
    assert (rates["success_rate"], rates["failure_rate"], rates["timeout_rate"]) == (
        0.5,
        0.25,
        0.25,
    )
    assert approx(rates["retry_rate"], 1 / 6)
    cost = body["cost"]
    assert approx(cost["total_usd"], 7.75) and approx(cost["per_run_usd"], 7.75 / 6)
    assert approx(cost["per_successful_run_usd"], 2.25) and approx(cost["retry_usd"], 1.5)
    assert approx(cost["retry_share"], 1.5 / 7.75)
    assert cost["unpriced_calls"] == 0 and cost["unrebuilt_runs"] == 0
    latency = body["run_latency"]
    assert latency["count"] == 4 and latency["p50_ms"] == 25000 and approx(latency["p95_ms"], 38500)
    behaviour = body["behaviour"]
    assert approx(behaviour["avg_llm_calls"], 5 / 6) and approx(behaviour["avg_tool_calls"], 4 / 6)
    assert approx(behaviour["avg_retries"], 1 / 6) and behaviour["avg_files_modified"] == 0
    assert body["tools"] == {"calls": 4, "success_rate": 0.75}
    assert body["llm"] == {"calls": 5, "success_rate": 1.0}
    assert body["active_agents"] == ["a3"]
    assert body["window"]["end"].startswith("2026-10-07T12:05")


async def test_cost_report_breakdowns_and_retry_share(seeded: Api) -> None:
    body = await get(seeded, "cost")
    assert approx(body["retries"]["initial_usd"], 6.25) and approx(
        body["retries"]["retry_usd"], 1.5
    )
    assert (
        body["retries"]["runs_with_retry_cost"] == 1
        and body["retries"]["retries_unattributed"] == 0
    )
    days = {d["day"]: d for d in body["by_day"]}
    assert set(days) == {"2026-10-06", "2026-10-07"}
    assert approx(days["2026-10-06"]["cost_usd"], 1.5) and days["2026-10-06"]["runs"] == 1
    assert approx(days["2026-10-07"]["cost_usd"], 6.25) and approx(
        days["2026-10-07"]["retry_usd"], 1.5
    )
    agents = {a["agent"]: a for a in body["by_agent"]}
    assert approx(agents["a1"]["cost_usd"], 6.0) and agents["a1"]["calls"] == 3
    assert approx(agents["a2"]["cost_usd"], 1.75) and body["by_agent_other"] is None
    models = {m["model"]: m for m in body["by_model"]}
    assert (
        approx(models["model-x"]["cost_usd"], 6.0)
        and models["model-x"]["input_tokens"] == 2_000_000
    )
    assert approx(models["model-x-mini-1"]["cost_usd"], 1.5) and models["mystery"]["calls"] == 1
    sources = {s["source"]: s["cost_usd"] for s in body["by_source"]}
    assert approx(sources["estimated"], 7.5) and approx(sources["client_estimate"], 0.25)
    assert body["by_project"] is None  # a project key is already one project
    top = [r["run_id"] for r in body["expensive_runs"]]
    assert set(top[:2]) == {RUNS["r1"], RUNS["r2"]} and top[2] == RUNS["r4"]
    retried = next(r for r in body["expensive_runs"] if r["run_id"] == RUNS["r2"])
    assert approx(retried["retry_usd"], 1.5) and retried["retry_count"] == 1


async def test_reliability_report(seeded: Api) -> None:
    body = await get(seeded, "reliability")
    days = {d["day"]: d for d in body["failure_trend"]}
    assert days["2026-10-06"]["finished"] == 1 and days["2026-10-06"]["failure_rate"] == 0
    seventh = days["2026-10-07"]
    assert (seventh["finished"], seventh["success"], seventh["failed"], seventh["timed_out"]) == (
        3,
        1,
        1,
        1,
    )
    assert approx(seventh["failure_rate"], 2 / 3)
    tools = {t["name"]: t for t in body["tools"]}
    assert tools["git"]["calls"] == 2 and tools["git"]["success_rate"] == 1.0
    assert tools["sh"]["success_rate"] == 0.0 and tools["fix"]["calls"] == 1
    assert [r["run_id"] for r in body["retry_heavy_runs"]] == [RUNS["r2"]]
    assert body["retry_heavy_runs"][0]["retry_count"] == 1


async def test_performance_report(seeded: Api) -> None:
    body = await get(seeded, "performance")
    assert body["llm"] == {"count": 5, "p50_ms": 2000, "p95_ms": 2000}
    assert body["tool"]["count"] == 4 and approx(body["tool"]["p95_ms"], 285.0)
    slow = body["slow_operations"]
    assert (slow[0]["kind"], slow[0]["name"]) == ("llm", "example-provider/model-x") or slow[0][
        "kind"
    ] == "llm"
    assert all(s["kind"] != "agent" for s in slow)
    sh = next(s for s in slow if s["name"] == "sh")
    assert sh["p95_ms"] == 300 and sh["calls"] == 1


# ------------------------------------------------------------------ scoping and isolation


async def test_a_workspace_key_sees_every_project_and_can_narrow(seeded: Api) -> None:
    wide = await get(seeded, "summary", token="wide_reader")
    assert wide["runs"]["total"] == 7 and approx(wide["cost"]["total_usd"], 10.75)
    beta = await get(
        seeded, "summary", token="wide_reader", project_id=seeded.tenant.projects["beta"]
    )
    assert beta["runs"]["total"] == 1 and approx(beta["cost"]["total_usd"], 3.0)
    by_project = (await get(seeded, "cost", token="wide_reader"))["by_project"]
    assert len(by_project) == 2 and approx(by_project[0]["cost_usd"], 7.75)
    agent = await get(seeded, "summary", token="wide_reader", agent_id="a2")
    assert agent["runs"]["total"] == 2 and approx(agent["cost"]["total_usd"], 1.75)
    # the agent filter reaches the line-based breakdowns too
    cost = await get(seeded, "cost", token="wide_reader", agent_id="a2")
    assert {a["agent"] for a in cost["by_agent"]} == {"a2"}


async def test_a_project_key_cannot_read_or_probe_other_projects(seeded: Api) -> None:
    beta = seeded.tenant.projects["beta"]
    for kind in ("summary", "cost", "reliability", "performance"):
        response = await seeded.get(f"/v1/analytics/{kind}", project_id=beta)
        assert (
            response.status_code == 404 and response.json()["error"]["code"] == "PROJECT_NOT_FOUND"
        )
        own = await seeded.get(f"/v1/analytics/{kind}", project_id=seeded.tenant.projects["alpha"])
        assert own.status_code == 200
    # a beta-bound key sees only beta, never alpha, whatever it asks for
    assert (await get(seeded, "summary", token="beta"))["runs"]["total"] == 1


async def test_another_workspace_is_invisible_everywhere(seeded: Api) -> None:
    for kind in ("summary", "cost", "reliability", "performance"):
        wide = await get(seeded, kind, token="wide_reader")
        assert "100000000" not in str(wide)  # globex's 100M-token call never leaks
    foreign = seeded.other.projects["p"]
    response = await seeded.get("/v1/analytics/summary", token="wide_reader", project_id=foreign)
    assert response.status_code == 404
    theirs = await get(seeded, "cost", token="other")
    assert approx(theirs["headline"]["total_usd"], 300.0)


async def test_access_and_validation_rules(seeded: Api) -> None:
    for kind in ("summary", "cost", "reliability", "performance"):
        path = f"/v1/analytics/{kind}"
        assert (await seeded.get(path, token=None)).status_code == 401
        assert (await seeded.get(path, token="revoked")).status_code == 401
        assert (await seeded.get(path, token="ingest_only")).status_code == 403
    bad: list[dict[str, Any]] = [
        {"project_id": "nonsense"},
        {"agent_id": "Bad Agent!"},
        {"top": 0},
        {"top": 51},
        {"from": "2026-10-07T00:00:00Z", "to": "2026-10-06T00:00:00Z"},
        {"from": "2026-01-01T00:00:00Z", "to": "2026-10-06T00:00:00Z"},
        {"from": "garbage"},
    ]
    for params in bad:
        response = await seeded.get("/v1/analytics/cost", **params)
        assert response.status_code == 422, params
    ok = await seeded.get(
        "/v1/analytics/summary", **{"from": "2026-10-07T00:00:00", "to": "2026-10-08"}
    )
    assert ok.status_code == 200 and ok.json()["runs"]["total"] == 5  # naive times are UTC


async def test_an_empty_window_has_null_rates_not_zeros(seeded: Api) -> None:
    body = await get(
        seeded, "summary", **{"from": "2025-01-01T00:00:00Z", "to": "2025-01-02T00:00:00Z"}
    )
    assert body["runs"]["total"] == 0 and body["rates"]["success_rate"] is None
    assert body["cost"]["per_run_usd"] is None and body["behaviour"]["avg_llm_calls"] is None
    assert body["run_latency"] == {"count": 0, "p50_ms": None, "p95_ms": None}


async def test_grouped_results_are_bounded_by_top(api: Api) -> None:
    seeder = Seeder(api, "writer")
    llm = [
        {
            "llm.provider": "p",
            "llm.model": f"m{i:02d}",
            "llm.input_tokens": 1,
            "cost.estimated_usd": 0.01 * (i + 1),
        }
        for i in range(12)
    ]
    await seeder.run(D7, llm=llm, tools=[(f"tool{i:02d}", "success", 10) for i in range(15)])
    await api.drain()
    cost = await get(api, "cost", top=3)
    assert len(cost["by_model"]) == 3 and cost["by_model"][0]["model"] == "m11"
    other = cost["by_model_other"]
    assert (
        other["groups"] == 9
        and other["calls"] == 9
        and approx(other["cost_usd"], sum(0.01 * (i + 1) for i in range(9)))
    )
    reliability = await get(api, "reliability", top=5)
    assert len(reliability["tools"]) == 5 and reliability["tools_other"] == {
        "groups": 10,
        "calls": 10,
        "cost_usd": 0.0,
    }
    perf = await get(api, "performance", top=2)
    assert len(perf["slow_operations"]) == 2


async def test_legacy_summaries_are_reported_as_unrebuilt(seeded: Api) -> None:
    async with seeded.engine.begin() as conn:
        await conn.execute(text("UPDATE runs SET summary_version = 1 WHERE project_id IS NOT NULL"))
    assert (await get(seeded, "summary", token="wide_reader"))["cost"]["unrebuilt_runs"] == 7


async def test_a_slow_query_is_cut_off_and_reported_as_retryable(seeded: Api) -> None:
    store = PostgresAnalyticsStore(seeded.engine, timeout_seconds=0.05)
    scope = AnalyticsScope(TenantContext(seeded.tenant.context.workspace_id), D6, D7)

    async def sleepy(conn: Any) -> None:
        await conn.execute(text("SELECT pg_sleep(2)"))

    with pytest.raises(AppError) as caught:
        await store._read(scope, sleepy)
    assert caught.value.code == analytics_timeout().code and caught.value.status_code == 503

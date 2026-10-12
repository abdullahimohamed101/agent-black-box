"""`/v1/analytics/waste` (Phase 22 D12): spend on failed runs and on retries, by agent and model.

Expected numbers are worked out by hand from the seeded scenario (`tests/analytics_fixtures.py`):
r2 failed after two $1.50 calls (the second one after a retry), r3 timed out on a $0.25 client
estimate, so the failed spend is $3.25 and the retry spend $1.50 sits inside it. The report is
reconciled with the cost report (the rollup-backed cost engine figures) so they cannot drift apart.
"""

from typing import Any

from abb_event_schema.ids import to_uuid
from sqlalchemy import text

from tests.analytics_fixtures import D7, RUNS, Seeder, X
from tests.api_fixtures import Api

FAILED = ("FAILED", "TIMED_OUT", "BLOCKED")
HALF = {**X, "llm.input_tokens": 500_000}  # $1.50
GHOST = {"llm.provider": "nobody", "llm.model": "ghost-model", "llm.input_tokens": 5}  # no price


def approx(value: float | None, expected: float) -> bool:
    return value is not None and abs(value - expected) < 1e-6


async def get(api: Api, kind: str = "waste", token: str = "reader", **params: Any) -> Any:
    response = await api.get(f"/v1/analytics/{kind}", token=token, **params)
    assert response.status_code == 200, response.text
    return response.json()


async def test_failed_spend_and_retry_spend_by_agent_and_model(seeded: Api) -> None:
    body = await get(seeded)
    assert body["failed"]["runs"] == 2
    assert approx(body["failed"]["spend_usd"], 3.25)
    # The retried call belongs to a failed run: it is inside the failed spend, never added again.
    assert approx(body["retries"]["total_usd"], 1.5)
    assert approx(body["retries"]["in_failed_runs_usd"], 1.5)
    assert approx(body["retries"]["in_other_runs_usd"], 0)
    assert approx(body["wasted_usd"], 3.25) and approx(body["total_usd"], 7.75)
    assert approx(body["wasted_share"], 3.25 / 7.75)
    agents = {a["agent"]: a for a in body["by_agent"]}
    assert set(agents) == {"a1", "a2"}
    assert approx(agents["a1"]["failed_usd"], 3.0) and agents["a1"]["failed_runs"] == 1
    assert approx(agents["a2"]["wasted_usd"], 0.25)
    assert [a["agent"] for a in body["by_agent"]] == ["a1", "a2"]  # costliest waste first
    models = {m["model"]: m for m in body["by_model"]}
    assert approx(models["model-x"]["failed_usd"], 3.0) and models["model-x"]["calls"] == 2
    assert approx(models["mystery"]["wasted_usd"], 0.25)
    assert body["by_agent_other"] is None and body["by_model_other"] is None
    assert body["unrebuilt_runs"] == 0 and body["failed"]["after_first_error_unknown_runs"] == 0


async def test_it_reconciles_with_the_cost_engine(seeded: Api) -> None:
    waste = await get(seeded)
    cost = await get(seeded, "cost")
    assert approx(waste["total_usd"], cost["headline"]["total_usd"])
    assert approx(waste["retries"]["total_usd"], cost["headline"]["retry_usd"])
    failed_runs = [r for r in cost["expensive_runs"] if r["status"] in FAILED]
    assert approx(waste["failed"]["spend_usd"], sum(r["cost_usd"] for r in failed_runs))
    # Both breakdowns add up to the headline, whichever way they are cut.
    assert approx(sum(a["wasted_usd"] for a in waste["by_agent"]), waste["wasted_usd"])
    assert approx(sum(m["wasted_usd"] for m in waste["by_model"]), waste["wasted_usd"])
    # Each failed-run dollar is one the cost report's own by-agent and by-model figures hold.
    by_agent = {a["agent"]: a["cost_usd"] for a in cost["by_agent"]}
    by_model = {m["model"]: m["cost_usd"] for m in cost["by_model"]}
    for a in waste["by_agent"]:
        assert a["failed_usd"] <= by_agent[a["agent"]] + 1e-6
    for m in waste["by_model"]:
        assert m["failed_usd"] <= by_model[m["model"]] + 1e-6


async def test_spend_after_the_first_error_and_retries_in_runs_that_did_not_fail(
    seeded: Api,
) -> None:
    alpha = Seeder(seeded, "writer")
    # A tool fails, then the agent keeps spending $1.50 before the run ends in error.
    await alpha.run(
        D7, agent="a1", end="error", llm=[HALF], tools=[("sh", "error", 10)], tools_first=True
    )
    # A run that retried a call and still succeeded: its retry spend is waste, the rest is not.
    await alpha.run(D7, agent="a4", end="success", llm=[HALF, HALF], retry_scope=True)
    await seeded.drain()
    body = await get(seeded)
    assert body["failed"]["runs"] == 3 and approx(body["failed"]["spend_usd"], 4.75)
    assert approx(body["failed"]["after_first_error_usd"], 1.5)
    assert approx(body["retries"]["in_other_runs_usd"], 1.5)
    assert approx(body["wasted_usd"], 4.75 + 1.5)
    agents = {a["agent"]: a for a in body["by_agent"]}
    assert approx(agents["a4"]["retry_usd"], 1.5) and agents["a4"]["failed_runs"] == 0
    assert approx(agents["a1"]["failed_usd"], 4.5)
    models = {m["model"]: m for m in body["by_model"]}
    assert approx(models["model-x"]["retry_usd"], 1.5)  # the retried call in the successful run
    cost = await get(seeded, "cost")
    assert approx(body["retries"]["total_usd"], cost["headline"]["retry_usd"])
    assert approx(body["total_usd"], cost["headline"]["total_usd"])


async def test_unpriced_calls_are_reported_not_hidden(seeded: Api) -> None:
    await Seeder(seeded, "writer").run(D7, agent="a5", end="error", llm=[GHOST])
    await seeded.drain()
    body = await get(seeded)
    assert body["failed"]["unpriced_calls"] == 1
    ghost = next(m for m in body["by_model"] if m["model"] == "ghost-model")
    assert ghost["unpriced_calls"] == 1 and ghost["wasted_usd"] == 0


async def test_runs_summarised_before_first_error_facts_are_counted_as_unknown(
    seeded: Api,
) -> None:
    async with seeded.engine.begin() as conn:
        await conn.execute(
            text("UPDATE runs SET summary_version = 4 WHERE id = :id"),
            {"id": to_uuid(RUNS["r2"])},
        )
    body = await get(seeded)
    assert body["failed"]["after_first_error_unknown_runs"] == 1
    assert body["unrebuilt_runs"] >= 1


async def test_a_version_5_failed_run_has_no_trustworthy_first_error_facts(seeded: Api) -> None:
    """Version 5 left a blocked run without a first error (ADR-112), so v5 is below the bar."""
    async with seeded.engine.begin() as conn:
        await conn.execute(
            text("UPDATE runs SET summary_version = 5 WHERE id = :id"),
            {"id": to_uuid(RUNS["r2"])},
        )
    body = await get(seeded)
    assert body["failed"]["after_first_error_unknown_runs"] == 1
    assert body["unrebuilt_runs"] == 1  # every run below the current summary version


async def test_unrebuilt_runs_is_documented_as_what_it_counts(seeded: Api) -> None:
    schema = (await seeded.get("/openapi.json")).json()["components"]["schemas"]["WasteReport"]
    text_ = schema["properties"]["unrebuilt_runs"]["description"]
    assert "below the current summary version" in text_
    assert "first-error" in text_  # not only about cost lines, which arrived at version 2


async def test_top_limits_each_breakdown_and_folds_the_rest(seeded: Api) -> None:
    body = await get(seeded, top=1)
    assert [a["agent"] for a in body["by_agent"]] == ["a1"]
    assert body["by_agent_other"] == {"groups": 1, "wasted_usd": 0.25}
    assert [m["model"] for m in body["by_model"]] == ["model-x"]
    assert body["by_model_other"] == {"groups": 1, "wasted_usd": 0.25}
    assert (await seeded.get("/v1/analytics/waste", top=0)).status_code == 422


async def test_an_empty_window_is_zero_with_a_null_share(seeded: Api) -> None:
    body = await get(seeded, **{"from": "2025-01-01T00:00:00Z", "to": "2025-01-02T00:00:00Z"})
    assert body["wasted_usd"] == 0 and body["total_usd"] == 0 and body["wasted_share"] is None
    assert body["failed"]["runs"] == 0 and body["by_agent"] == [] and body["by_model"] == []


async def test_a_project_key_cannot_read_another_project_and_workspaces_stay_apart(
    seeded: Api,
) -> None:
    beta = seeded.tenant.projects["beta"]
    denied = await seeded.get("/v1/analytics/waste", project_id=beta)
    assert denied.status_code == 404 and denied.json()["error"]["code"] == "PROJECT_NOT_FOUND"
    # A huge failed run in the other workspace never reaches this one; a beta run reaches only
    # workspace-wide readers, not the alpha project key.
    wide_before = await get(seeded, token="wide_reader")
    alpha_before = await get(seeded, token="reader")
    big = {**X, "llm.input_tokens": 100_000_000}
    await Seeder(seeded, "other").run(D7, end="error", llm=[big])
    await Seeder(seeded, "beta").run(D7, agent="b", end="error", llm=[HALF])
    await seeded.drain()
    wide = await get(seeded, token="wide_reader")
    assert approx(wide["failed"]["spend_usd"], wide_before["failed"]["spend_usd"] + 1.5)
    assert "100000000" not in str(wide)
    assert await get(seeded, token="reader") == alpha_before  # confined to its own project
    theirs = await get(seeded, token="other")
    assert approx(theirs["failed"]["spend_usd"], 300.0) and theirs["failed"]["runs"] == 1
    assert {a["agent"] for a in theirs["by_agent"]} == {"a1"}


async def test_access_rules(seeded: Api) -> None:
    path = "/v1/analytics/waste"
    assert (await seeded.get(path, token=None)).status_code == 401
    assert (await seeded.get(path, token="revoked")).status_code == 401
    assert (await seeded.get(path, token="ingest_only")).status_code == 403

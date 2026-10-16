"""What readers see of a run whose trace was removed (ADR-160): an explicit `trace_state`, an
intact record, and empty - never partial - events, spans and findings."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from abb_event_schema.ids import IdKind, from_uuid
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.retention.tick import run_tick
from tests.api_fixtures import Api, person
from tests.auth_helpers import mint_session
from tests.evaluation_helpers import seeded_run
from tests.retention.enforce_helpers import enforcing, make_store


async def tick_at(engine: AsyncEngine, tmp_path: Path, when: datetime) -> None:
    result = await run_tick(
        engine,
        enforcing(),
        budget_seconds=60,
        keep_reports=50,
        now=lambda: when,
        store=make_store(tmp_path),
    )
    assert result.failed == 0


async def sign_in_again(web: Api, headers: dict[str, str]) -> None:
    """The tick removed the test's long-dead session (a fixed window): mint a new one."""
    async with web.engine.connect() as conn:
        user_id = (
            await conn.execute(text("SELECT id FROM users WHERE email = 'o@acme.test'"))
        ).scalar_one()
    headers["cookie"] = f"abb_session={await mint_session(web.engine, user_id, web.clock())}"


async def test_a_run_past_the_trace_tier_says_expired_and_keeps_its_record(
    web: Api, retention_engine: AsyncEngine, tmp_path: Path
) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    run = await seeded_run(web.engine, web.tenant, web.worker, terminal="success", project="alpha")
    run_id = from_uuid(IdKind.RUN, run)

    async def get(path: str, **params: str) -> dict[str, Any]:
        response = await web.client.get(path, params=params, headers=owner)
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    before = await get(f"/v1/runs/{run_id}")
    assert before["trace_state"] == "available"
    events_before = await get(f"/v1/runs/{run_id}/events", limit="500")
    assert events_before["items"]

    # Long after the run: past the trace tier (90 days), inside the run-record tier (365).
    await tick_at(retention_engine, tmp_path, datetime(2027, 3, 1, tzinfo=UTC))
    # The tick also removed the test's long-dead session (fixed windows): sign in again.
    await sign_in_again(web, owner)

    after = await get(f"/v1/runs/{run_id}")
    assert after["trace_state"] == "expired"
    # The record is intact: same summary, status and timings. Only the state differs, and the
    # findings badges, which are derived from detail that is gone, read "not analysed".
    derived = ("trace_state", "findings", "security_findings")
    assert {k: v for k, v in after.items() if k not in derived} == {
        k: v for k, v in before.items() if k not in derived
    }
    assert after["findings"]["state"] == after["security_findings"]["state"] == "none"
    assert (await get(f"/v1/runs/{run_id}/events", limit="500"))["items"] == []
    assert (await get(f"/v1/runs/{run_id}/spans"))["items"] == []
    listed = (await get("/v1/runs"))["items"]
    assert [r["trace_state"] for r in listed if r["id"] == run_id] == ["expired"]

    # Nothing is left to compare or evaluate: both say so, instead of answering about no events.
    compared = await web.client.get(
        f"/v1/runs/{run_id}/compare", params={"with_run_id": run_id}, headers=owner
    )
    assert compared.status_code == 409 and compared.json()["error"]["code"] == "TRACE_EXPIRED"
    evaluated = await web.client.post(
        f"/v1/runs/{run_id}/evaluations",
        json={"evaluator_id": "evr_00000000000000000000000000"},
        headers=owner,
    )
    assert evaluated.status_code == 409, evaluated.text
    assert evaluated.json()["error"]["code"] == "TRACE_EXPIRED"

    # Past the run-record tier the run is simply gone (404), with tombstones left behind.
    await tick_at(retention_engine, tmp_path, datetime(2028, 6, 1, tzinfo=UTC))
    await sign_in_again(web, owner)
    gone = await web.client.get(f"/v1/runs/{run_id}", headers=owner)
    assert gone.status_code == 404

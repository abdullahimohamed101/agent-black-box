"""Expiry: the timer job, the lazy sweeps and what an expired approval is (gate 4)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from tests.api_fixtures import Api
from tests.approvals.helpers import (
    approval_row,
    approved,
    ask,
    consume,
    decide,
    events_of,
    intent,
    later,
    member,
    publish,
)
from tests.test_audit import rows


async def pending_jobs(api: Api) -> list[dict[str, object]]:
    async with api.engine.connect() as conn:
        found = await conn.execute(
            text(
                "SELECT dedupe_key, payload, available_at, now() AS db_now FROM outbox_jobs "
                "WHERE job_type = 'expire_approval' AND status = 'pending'"
            )
        )
        return [dict(r._mapping) for r in found]


async def fire_the_timers(api: Api) -> None:
    """Make the database clock reach the jobs (the tests' injected clock is in the past)."""
    async with api.engine.begin() as conn:
        await conn.execute(
            text("UPDATE outbox_jobs SET available_at = now() WHERE job_type = 'expire_approval'")
        )
    await api.drain()


async def test_creating_an_approval_schedules_a_timer_for_its_expiry(api: Api) -> None:
    await publish(api)
    _body, approval_id = await ask(api)
    (job,) = await pending_jobs(api)
    delta = job["available_at"] - job["db_now"]  # type: ignore[operator]
    assert 14 * 60 <= delta.total_seconds() <= 15 * 60 + 5  # gate-push: 15 minutes
    assert job["payload"] == {"approval_id": str((await approval_row(api, approval_id))["id"])}


async def test_the_timer_expires_waiting_and_unused_approved_requests_only(api: Api) -> None:
    await publish(api)
    waiting_body, waiting = await ask(api, arguments={"n": 1})
    unused_body, unused = await approved(api, arguments={"n": 2})
    spent_body, spent = await approved(api, arguments={"n": 3})
    assert (await consume(api, spent, spent_body)).status_code == 200
    await fire_the_timers(api)
    assert (await approval_row(api, waiting))["state"] == "EXPIRED"
    assert (await approval_row(api, unused))["state"] == "EXPIRED"
    spent_row = await approval_row(api, spent)
    assert spent_row["state"] == "APPROVED" and spent_row["consumed_at"] is not None
    # One event and one audit row (actor: the system) per expiry; none for the spent one.
    expired = [r for r in await rows(api) if r.action == "approval.expire"]
    assert sorted(str(r.resource_id) for r in expired) == sorted([waiting, unused])
    assert {(r.actor_kind, r.actor_id) for r in expired} == {("system", "system:approval-expiry")}
    by_id = {r.resource_id: r.details for r in expired}
    assert by_id[waiting]["was_approved"] is False and by_id[unused]["was_approved"] is True
    for run in (waiting_body["run_id"], unused_body["run_id"]):
        assert len(await events_of(api, run, "approval.expired")) == 1
    assert await events_of(api, spent_body["run_id"], "approval.expired") == []


async def test_the_timer_stamps_the_application_clock_not_the_databases(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Found by `policy-e2e`: the job read `now()` from the database, which in a VM or container
    runs ahead of the host. The approval was then closed (and `approval.expired` stamped) by a
    clock the agent and the API do not share, so it could sort after the end of the agent's run."""
    await publish(api)
    body, approval_id = await ask(api)
    stamp = datetime(2031, 3, 4, 5, 6, 7, tzinfo=UTC)  # nothing like the database's own time
    monkeypatch.setattr("abb_api.approvals.job.system_clock", lambda: stamp, raising=False)
    await fire_the_timers(api)
    row = await approval_row(api, approval_id)
    assert row["state"] == "EXPIRED" and row["closed_at"] == stamp
    assert len(await events_of(api, body["run_id"], "approval.expired")) == 1
    async with api.engine.connect() as conn:
        stamped = (
            (
                await conn.execute(
                    text("SELECT occurred_at FROM events WHERE event_type = 'approval.expired'")
                )
            )
            .scalars()
            .all()
        )
    assert stamped == [stamp]


async def test_running_the_timer_twice_changes_nothing(api: Api) -> None:
    await publish(api)
    body, _approval_id = await ask(api)
    await fire_the_timers(api)
    await fire_the_timers(api)
    assert len(await events_of(api, body["run_id"], "approval.expired")) == 1
    assert len([r for r in await rows(api) if r.action == "approval.expire"]) == 1


async def test_reads_sweep_lazily_without_a_timer(api: Api) -> None:
    await publish(api)
    _body, approval_id = await ask(api)
    owner = await member(api, "owner@acme.test", "OWNER")
    pending = (await api.client.get("/v1/approvals/pending-count", headers=owner)).json()
    assert pending == {"count": 1}
    later(api, minutes=16)
    assert (await api.client.get("/v1/approvals/pending-count", headers=owner)).json() == {
        "count": 0
    }
    assert (await approval_row(api, approval_id))["state"] == "EXPIRED"
    listing = (await api.client.get("/v1/approvals?state=pending", headers=owner)).json()
    assert listing["items"] == []
    decided = (await api.client.get("/v1/approvals?state=decided", headers=owner)).json()
    assert [i["state"] for i in decided["items"]] == ["EXPIRED"]
    assert decided["items"][0]["seconds_left"] is None


async def test_the_rules_fallback_is_recorded_and_returned_to_the_waiter(api: Api) -> None:
    await publish(api)
    body = intent(capability="deploy.run", resource="prod", risk_class="R3")
    out = (await decide(api, body)).json()
    assert out["on_expiry"] == "allow"
    later(api, hours=2)
    response = await api.client.get(
        f"/v1/approvals/{out['approval_id']}/wait",
        params={"timeout": 0},
        headers=api.headers("writer"),
    )
    assert response.json()["state"] == "EXPIRED" and response.json()["on_expiry"] == "allow"
    # Expiry is not consent: an `allow` fallback is the SDK's choice, and the approval is unusable.
    assert (await consume(api, out["approval_id"], body)).json()["error"][
        "code"
    ] == "APPROVAL_EXPIRED"


async def test_after_expiry_the_same_intent_gets_a_fresh_request(api: Api) -> None:
    await publish(api)
    body = intent()
    first = (await decide(api, body)).json()["approval_id"]
    later(api, minutes=16)
    second = (await decide(api, body)).json()
    assert second["approval_id"] != first and second["approval_state"] == "REQUESTED"
    assert (await approval_row(api, first))["state"] == "EXPIRED"

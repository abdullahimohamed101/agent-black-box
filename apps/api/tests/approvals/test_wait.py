"""Long polling for a decision (gate 4): early return, bounded waits, caps, no starvation."""

import asyncio
import time

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.api_fixtures import Api
from tests.approvals.helpers import approve, ask, decision, member, publish
from tests.conftest import make_settings


async def wait(
    api: Api, approval_id: str, seconds: float, token: str = "writer"
) -> dict[str, object]:
    response = await api.client.get(
        f"/v1/approvals/{approval_id}/wait", params={"timeout": seconds}, headers=api.headers(token)
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


async def test_a_waiter_returns_early_when_the_state_changes(api: Api) -> None:
    await publish(api)
    _body, approval_id = await ask(api)
    started = time.monotonic()
    waiter = asyncio.create_task(wait(api, approval_id, 20))
    await asyncio.sleep(0.4)
    assert not waiter.done()
    assert (await approve(api, approval_id)).status_code == 200
    answer = await asyncio.wait_for(waiter, 5)
    assert answer["state"] == "APPROVED" and time.monotonic() - started < 6
    assert answer["policy_id"] == "gate-push" and answer["on_expiry"] == "deny"
    assert answer["consumed"] is False
    assert set(answer) == {
        "approval_id",
        "state",
        "expires_at",
        "on_expiry",
        "policy_id",
        "policy_version",
        "consumed",
    }


async def test_a_timeout_returns_the_current_state_and_a_decided_one_returns_at_once(
    api: Api,
) -> None:
    await publish(api)
    _body, approval_id = await ask(api)
    started = time.monotonic()
    assert (await wait(api, approval_id, 0.6))["state"] == "REQUESTED"
    assert 0.5 <= time.monotonic() - started < 3
    await approve(api, approval_id)
    started = time.monotonic()
    assert (await wait(api, approval_id, 20))["state"] == "APPROVED"
    assert time.monotonic() - started < 2


async def test_the_timeout_is_capped_at_25_seconds(api: Api) -> None:
    await publish(api)
    _body, approval_id = await ask(api)
    response = await api.client.get(
        f"/v1/approvals/{approval_id}/wait", params={"timeout": 26}, headers=api.headers("writer")
    )
    assert response.status_code == 422


async def test_an_expired_approval_is_reported_as_expired_to_a_waiter(api: Api) -> None:
    await publish(api)
    _body, approval_id = await ask(api)
    waiter = asyncio.create_task(wait(api, approval_id, 20))
    await asyncio.sleep(0.3)
    api.clock.now = api.clock.now.replace(year=api.clock.now.year + 1)
    answer = await asyncio.wait_for(waiter, 6)
    assert answer["state"] == "EXPIRED"


async def test_only_the_asking_key_may_wait(api: Api) -> None:
    await publish(api)
    _body, approval_id = await ask(api)
    for token in ("ingest_only", "beta", "other"):
        response = await api.client.get(
            f"/v1/approvals/{approval_id}/wait", params={"timeout": 0}, headers=api.headers(token)
        )
        assert (
            response.status_code == 404 and response.json()["error"]["code"] == "APPROVAL_NOT_FOUND"
        )


async def test_waiters_are_capped_per_workspace_and_ordinary_requests_are_not_starved(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> None:
    from tests.api_fixtures import build_api

    settings = make_settings(
        database_url,
        web_origin="http://localhost:3000",
        approval_waiters_per_workspace=3,
        approval_waiters_per_process=5,
        approval_wait_poll_seconds=0.5,
    )
    async for api in build_api(database_url, engine, settings=settings):
        await publish(api)
        approvals = [(await ask(api, arguments={"i": i}))[1] for i in range(5)]
        waiters = [asyncio.create_task(wait(api, a, 3)) for a in approvals[:3]]
        await asyncio.sleep(0.4)
        refused = await api.client.get(
            f"/v1/approvals/{approvals[3]}/wait",
            params={"timeout": 3},
            headers=api.headers("writer"),
        )
        assert refused.status_code == 429 and refused.json()["error"]["code"] == "TOO_MANY_WAITERS"
        assert refused.headers["retry-after"] == "5"
        # Ordinary traffic goes through while three agents are parked.
        started = time.monotonic()
        for _ in range(10):
            ping = await api.client.get("/v1/runs", headers=api.headers("reader"))
            assert ping.status_code == 200
        assert time.monotonic() - started < 2
        assert (await decide_quick(api)).status_code == 200
        for waiter in waiters:
            assert (await waiter)["state"] == "REQUESTED"
        # Slots are released: the same call now succeeds.
        assert (await wait(api, approvals[3], 0.2))["state"] == "REQUESTED"
        assert api.app.state.approvals.waiters.open_waits == 0


async def decide_quick(api: Api) -> httpx.Response:
    from tests.approvals.helpers import decide, intent

    return await decide(api, intent(capability="x.y", resource="z"))


async def test_a_waiter_that_goes_away_releases_its_slot(api: Api) -> None:
    await publish(api)
    _body, approval_id = await ask(api)
    waiter = asyncio.create_task(wait(api, approval_id, 20))
    await asyncio.sleep(0.4)
    assert api.app.state.approvals.waiters.open_waits == 1
    waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)
    await asyncio.sleep(0.2)
    assert api.app.state.approvals.waiters.open_waits == 0
    headers = await member(api, "admin@acme.test", "ADMIN")
    assert (await decision(api, headers, approval_id, "approve")).status_code == 200


async def test_the_wake_up_is_the_notification_not_the_poll(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> None:
    """With a 10 s fallback poll, a decision still reaches the waiter within a second or two."""
    from tests.api_fixtures import build_api

    settings = make_settings(
        database_url, web_origin="http://localhost:3000", approval_wait_poll_seconds=10
    )
    async for api in build_api(database_url, engine, settings=settings):
        await publish(api)
        _body, approval_id = await ask(api)
        await api.app.state.hub.wait_connected(5)
        waiter = asyncio.create_task(wait(api, approval_id, 20))
        await asyncio.sleep(0.5)
        decided = time.monotonic()
        assert (await approve(api, approval_id)).status_code == 200
        assert (await asyncio.wait_for(waiter, 8))["state"] == "APPROVED"
        assert time.monotonic() - decided < 3


async def test_a_failing_subscribe_gives_the_waiter_slot_back(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review P3-3: the slot was taken before the subscription and leaked if that raised."""
    await publish(api)
    _body, approval_id = await ask(api)

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("subscribe failed")

    monkeypatch.setattr(api.app.state.hub, "subscribe", boom)
    response = await api.client.get(
        f"/v1/approvals/{approval_id}/wait?timeout=1", headers=api.headers("writer")
    )
    assert response.status_code == 500
    assert api.app.state.approvals.waiters.open_waits == 0

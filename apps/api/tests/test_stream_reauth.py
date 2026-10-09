"""Open streams re-check their credential, session and role periodically (KI-033, D-streams)."""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from abb_api.auth import scopes
from abb_api.auth.repository import ApiKeyRepository
from tests.api_fixtures import WEB_ORIGIN, Api
from tests.auth_helpers import add_member, mint_session, remove_member, set_role
from tests.ingest_helpers import make_run_ids
from tests.stream_fixtures import Live, messages, send, serve

REAUTH = 0.4


@pytest.fixture
async def live(web: Api, runtime_database_url: str) -> AsyncIterator[Live]:
    async for instance in serve(
        web, runtime_database_url, web_origin=WEB_ORIGIN, stream_reauth_seconds=REAUTH
    ):
        yield instance


def cookie_headers(api: Api, token: str) -> dict[str, str]:
    return {
        "cookie": f"abb_session={token}",
        "origin": WEB_ORIGIN,
        "x-abb-workspace": api.tenant.workspace_id,
    }


class Watch:
    """One open stream, read in the background so a test can change the world meanwhile."""

    def __init__(self, live: Live, run_id: str, headers: dict[str, str]) -> None:
        self.frames: list[dict[str, Any]] = []
        self.status = 0
        self._task = asyncio.get_running_loop().create_task(self._read(live, run_id, headers))

    async def _read(self, live: Live, run_id: str, headers: dict[str, str]) -> None:
        async with httpx.AsyncClient(base_url=live.base_url, timeout=15) as client:
            async with client.stream("GET", f"/v1/runs/{run_id}/stream", headers=headers) as r:
                self.status = r.status_code
                async for message in messages(r):
                    self.frames.append(message)

    @property
    def done(self) -> bool:
        return self._task.done()

    def errors(self) -> list[dict[str, Any]]:
        return [f for f in self.frames if f.get("event") == "error"]

    async def ended(self, seconds: float = 6) -> None:
        await asyncio.wait_for(self._task, seconds)

    async def opened(self) -> None:
        for _ in range(100):
            if any(f.get("comment") == "open" for f in self.frames):
                return
            await asyncio.sleep(0.05)
        raise AssertionError("the stream never opened")

    async def stop(self) -> None:
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)


async def run_with_event(live: Live) -> dict[str, str]:
    run = make_run_ids()
    await send(live.api, run, 1)
    return run


def assert_unauthorised_end(watch: Watch) -> None:
    assert watch.done
    (frame,) = watch.errors()
    assert frame["data"]["error"]["code"] == "STREAM_UNAUTHORIZED"
    assert frame["data"]["error"]["retryable"] is False
    assert not [f for f in watch.frames if f.get("event") == "run_end"]


async def test_a_revoked_key_ends_its_open_stream(live: Live) -> None:
    run = await run_with_event(live)
    async with live.api.engine.begin() as conn:
        created = await ApiKeyRepository(conn, live.api.tenant.context).create(
            scopes=frozenset({scopes.RUNS_READ})
        )
    watch = Watch(live, run["run_id"], {"authorization": f"Bearer {created.token}"})
    try:
        await watch.opened()
        await asyncio.sleep(REAUTH * 2)
        assert not watch.done  # still valid across several checks
        async with live.api.engine.begin() as conn:
            await conn.execute(
                text("UPDATE api_keys SET revoked_at = now() WHERE key_id = :k"),
                {"k": created.stored.key_id},
            )
        await watch.ended()
        assert_unauthorised_end(watch)
    finally:
        await watch.stop()


async def test_an_expiring_key_ends_its_open_stream_when_the_clock_passes_it(live: Live) -> None:
    run = await run_with_event(live)
    expires = live.api.clock() + timedelta(minutes=5)
    async with live.api.engine.begin() as conn:
        created = await ApiKeyRepository(conn, live.api.tenant.context).create(
            scopes=frozenset({scopes.RUNS_READ}), expires_at=expires
        )
    watch = Watch(live, run["run_id"], {"authorization": f"Bearer {created.token}"})
    try:
        await watch.opened()
        live.api.clock.now += timedelta(minutes=10)
        await watch.ended()
        assert_unauthorised_end(watch)
    finally:
        await watch.stop()


async def test_a_revoked_or_expired_session_ends_its_stream(live: Live) -> None:
    api = live.api
    run = await run_with_event(live)
    user = await add_member(api.engine, api.tenant.context.workspace_id, "a@acme.test", "VIEWER")
    revoked = await mint_session(api.engine, user.id, api.clock())
    lapsing = await mint_session(api.engine, user.id, api.clock(), absolute=timedelta(minutes=5))
    first = Watch(live, run["run_id"], cookie_headers(api, revoked))
    second = Watch(live, run["run_id"], cookie_headers(api, lapsing))
    try:
        await first.opened()
        await second.opened()
        async with api.engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE sessions SET revoked_at = now() WHERE user_id = :u "
                    "AND expires_at > now() + interval '1 day'"
                ),
                {"u": user.id},
            )
        await first.ended()
        assert_unauthorised_end(first)
        assert not second.done
        api.clock.now += timedelta(minutes=10)  # the other session lapses
        await second.ended()
        assert_unauthorised_end(second)
    finally:
        await first.stop()
        await second.stop()


async def test_removal_and_loss_of_run_read_end_the_stream_but_a_lesser_role_does_not(
    live: Live,
) -> None:
    api = live.api
    workspace = api.tenant.context.workspace_id
    run = await run_with_event(live)
    watches: dict[str, Watch] = {}
    users = {}
    for name in ("removed", "billing", "viewer"):
        user = await add_member(api.engine, workspace, f"{name}@acme.test", "DEVELOPER")
        users[name] = user
        token = await mint_session(api.engine, user.id, api.clock())
        watches[name] = Watch(live, run["run_id"], cookie_headers(api, token))
    try:
        for watch in watches.values():
            await watch.opened()
        await remove_member(api.engine, workspace, users["removed"].id)
        await set_role(api.engine, workspace, users["billing"].id, "BILLING")  # no run.read
        await set_role(api.engine, workspace, users["viewer"].id, "VIEWER")  # keeps run.read
        await watches["removed"].ended()
        await watches["billing"].ended()
        assert_unauthorised_end(watches["removed"])
        assert_unauthorised_end(watches["billing"])
        await asyncio.sleep(REAUTH * 3)
        assert not watches["viewer"].done and not watches["viewer"].errors()
        await send(api, run, 2)  # and it still receives events
        for _ in range(60):
            if len([f for f in watches["viewer"].frames if f.get("event") == "trace_event"]) >= 2:
                break
            await asyncio.sleep(0.1)
        else:
            raise AssertionError("the surviving stream stopped delivering")
    finally:
        for watch in watches.values():
            await watch.stop()


async def test_a_still_valid_stream_survives_many_checks_and_does_not_slide_the_session(
    live: Live,
) -> None:
    api = live.api
    run = await run_with_event(live)
    user = await add_member(api.engine, api.tenant.context.workspace_id, "d@acme.test", "OWNER")
    token = await mint_session(api.engine, user.id, api.clock())
    async with api.engine.connect() as conn:
        before = (
            await conn.execute(text("SELECT idle_expires_at, last_seen_at FROM sessions"))
        ).all()
    watch = Watch(live, run["run_id"], cookie_headers(api, token))
    try:
        await watch.opened()
        api.clock.now += timedelta(minutes=30)  # beyond the 5-minute slide resolution
        await asyncio.sleep(REAUTH * 4)
        assert not watch.done and not watch.errors()
    finally:
        await watch.stop()
    async with api.engine.connect() as conn:
        after = (
            await conn.execute(text("SELECT idle_expires_at, last_seen_at FROM sessions"))
        ).all()
    # Minted at the clock's `now`, so opening slides nothing; 30 minutes later only the periodic
    # checks ran, and they must leave both deadlines exactly where they were.
    assert after == before


async def test_stream_limits_are_per_person_not_per_session(
    web: Api, runtime_database_url: str
) -> None:
    async for live in serve(web, runtime_database_url, web_origin=WEB_ORIGIN, stream_max_per_key=2):
        api = live.api
        run = await run_with_event(live)
        workspace = api.tenant.context.workspace_id
        alice = await add_member(api.engine, workspace, "alice@acme.test", "OWNER")
        bob = await add_member(api.engine, workspace, "bob@acme.test", "OWNER")
        alice_tabs = [
            cookie_headers(api, await mint_session(api.engine, alice.id, api.clock()))
            for _ in range(3)
        ]
        bob_headers = cookie_headers(api, await mint_session(api.engine, bob.id, api.clock()))
        watches = [Watch(live, run["run_id"], h) for h in alice_tabs[:2]]
        try:
            for w in watches:
                await w.opened()
            async with httpx.AsyncClient(base_url=live.base_url, timeout=10) as client:
                third = await client.get(f"/v1/runs/{run['run_id']}/stream", headers=alice_tabs[2])
                body = third.json()["error"]
                assert third.status_code == 429 and body["code"] == "STREAM_LIMIT"
                assert body["details"]["scope"] == "user"
            other = Watch(live, run["run_id"], bob_headers)
            watches.append(other)
            await other.opened()  # another person is unaffected
            assert other.status == 200
        finally:
            for w in watches:
                await w.stop()

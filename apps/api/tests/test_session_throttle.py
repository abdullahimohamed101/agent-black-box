"""Failed session cookies are throttled, valid sessions never are (Phase 19c step 3, ADR-169).

KI-176. Same shape as test_auth_throttle.py: failures-only counters per trusted address and
globally, a refusal from memory before any lookup, and the 19a lessons applied (a cold valid
session is not locked out by a flood; the lookups of a flood are counted, never timed).
"""

import secrets
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from abb_api.auth.repository import SessionRepository
from abb_api.auth.service import hash_session_token, session_handle
from abb_api.auth.session_throttle import SessionThrottle
from abb_api.core.errors import AppError
from abb_api.limits import SharedCounters
from abb_api.obs.catalogue import build_observability
from tests.api_fixtures import Api, build_api, person
from tests.auth_helpers import add_member, mint_session
from tests.conftest import UNREACHABLE_DATABASE_URL, make_settings
from tests.limits.helpers import room_in_window
from tests.limits.test_multi_process import another_process
from tests.obs.helpers import value

BIG = 1_000_000


def session_settings(database_url: str, **extra: Any) -> Any:
    options: dict[str, Any] = {
        "trust_proxy": True,
        "session_fail_per_address": BIG,
        "session_fail_global": BIG,
        "limits_block_divisor": 1000,  # blocks of one: every failure is counted exactly
    }
    options.update(extra)
    return make_settings(database_url, **options)


def junk() -> str:
    return secrets.token_urlsafe(32)  # well-formed, unknown


async def probe(
    api: Api, cookie: str, address: str | None = None, path: str = "/v1/me"
) -> httpx.Response:
    headers = {"cookie": f"abb_session={cookie}"}
    if address:
        headers["x-forwarded-for"] = address
    return await api.client.get(path, headers=headers)


def body_of(response: httpx.Response) -> dict[str, Any]:
    error: dict[str, Any] = dict(response.json()["error"])
    error.pop("request_id", None)
    return error


@pytest.fixture
def lookups(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[int]]:
    """One entry per session lookup the application made."""
    seen: list[int] = []
    real = SessionRepository.find_by_hash

    async def spy(self: Any, token_hash: bytes) -> Any:
        seen.append(1)
        return await real(self, token_hash)

    monkeypatch.setattr(SessionRepository, "find_by_hash", spy)
    yield seen


# ------------------------------------------------------------------ a flood is bounded


async def test_a_flood_of_bad_cookies_from_one_address_costs_a_bounded_number_of_lookups(
    database_url: str, engine: AsyncEngine, lookups: list[int]
) -> None:
    settings = session_settings(database_url, session_fail_per_address=5)
    async for api in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        for _ in range(5):
            assert (await probe(api, junk(), "203.0.113.9")).status_code == 401
        assert len(lookups) == 5
        # The failure that uses up the budget is looked up (and counted) and answered 429.
        blocked = await probe(api, junk(), "203.0.113.9")
        assert len(lookups) == 6
        assert blocked.status_code == 429
        assert blocked.json()["error"]["code"] == "SESSION_THROTTLED"
        assert blocked.json()["error"]["category"] == "RATE_LIMIT"
        wait = int(blocked.headers["retry-after"])
        assert 1 <= wait <= 60
        assert blocked.json()["error"]["details"]["retry_after_seconds"] == wait
        for _ in range(100):
            assert (await probe(api, junk(), "203.0.113.9")).status_code == 429
        assert len(lookups) == 6  # the flood reached the database six times, not 106
        # Another address has its own budget while the global one is open.
        assert (await probe(api, junk(), "203.0.113.10")).status_code == 401
        assert len(lookups) == 7


async def test_the_global_ceiling_bounds_a_distributed_flood(
    database_url: str, engine: AsyncEngine, lookups: list[int]
) -> None:
    settings = session_settings(database_url, session_fail_global=8, trust_proxy=False)
    async for api in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        for i in range(8):
            assert (await probe(api, junk(), f"198.51.100.{i}")).status_code == 401
        for _ in range(50):
            assert (await probe(api, junk())).status_code == 429
        assert len(lookups) == 9  # the eight allowed, and the ninth that used the budget up


async def test_one_stale_cookie_polled_again_and_again_costs_one_lookup(
    database_url: str, engine: AsyncEngine, lookups: list[int]
) -> None:
    async for api in build_api(database_url, engine, settings=session_settings(database_url)):
        stale = junk()
        for _ in range(25):
            assert (await probe(api, stale)).status_code == 401
        assert len(lookups) == 1  # the known-invalid cache; each was still counted as a failure


async def test_malformed_and_missing_cookies_are_neither_counted_nor_looked_up(
    database_url: str, engine: AsyncEngine, lookups: list[int]
) -> None:
    settings = session_settings(database_url, session_fail_global=2, trust_proxy=False)
    async for api in build_api(database_url, engine, settings=settings):
        for bad in ("short", "x" * 44, "a" * 42 + "!"):
            answer = await api.client.get("/v1/me", headers={"cookie": f"abb_session={bad}"})
            assert answer.status_code == 401, bad
        assert len(lookups) == 0
        # Nothing was counted: the whole budget of two is still there.
        assert (await probe(api, junk())).status_code == 401
        assert (await probe(api, junk())).status_code == 401
        assert (await probe(api, junk())).status_code == 429


async def test_throttled_bodies_are_uniform_and_say_nothing_about_the_cookie(
    database_url: str, engine: AsyncEngine
) -> None:
    settings = session_settings(database_url, session_fail_global=2, trust_proxy=False)
    async for api in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        member = await person(api, "ada@example.com", "OWNER")
        revoked = member["cookie"].split("=", 1)[1]
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE sessions SET revoked_at = now()"))
        unknown = await probe(api, junk())
        gone = await probe(api, revoked)
        assert unknown.status_code == gone.status_code == 401
        assert body_of(unknown) == body_of(gone)  # unknown and revoked look the same
        first = await probe(api, junk())
        second = await probe(api, revoked)  # a revoked cookie, now refused by the gate
        assert first.status_code == second.status_code == 429
        assert {k: v for k, v in body_of(first).items() if k != "details"} == {
            k: v for k, v in body_of(second).items() if k != "details"
        }
        assert "www-authenticate" not in first.headers


# ------------------------------------------------------------------ valid sessions


async def test_a_flood_never_refuses_a_valid_session_a_key_or_sign_in(
    database_url: str, engine: AsyncEngine
) -> None:
    settings = session_settings(database_url, session_fail_global=10, session_fail_per_address=5)
    async for api in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        address = "203.0.113.50"
        member = await person(api, "ada@example.com", "OWNER")
        cookie = member["cookie"].split("=", 1)[1]
        assert (await probe(api, cookie, address)).status_code == 200  # warms it
        for _ in range(40):
            await probe(api, junk(), address)
        assert (await probe(api, junk(), address)).status_code == 429
        for _ in range(5):  # same address, same process, windows closed: still served
            assert (await probe(api, cookie, address)).status_code == 200
        assert (await api.client.get("/v1/runs", headers=member)).status_code == 200
        # API keys have their own gate; sign-in starts and callbacks carry no session cookie.
        key = await api.client.get(
            "/v1/runs", headers={"authorization": f"Bearer {api.tokens['writer']}"}
        )
        assert key.status_code == 200
        for path in ("/v1/auth/login", "/v1/auth/callback"):
            assert (
                await api.client.get(path, headers={"x-forwarded-for": address})
            ).status_code != 429


async def test_a_cold_valid_session_is_served_while_the_window_is_closed(
    database_url: str, engine: AsyncEngine, lookups: list[int]
) -> None:
    """The 19a P1 applied to sessions: a flood must not lock out a person this process has never
    seen. Their session is in the active hint set (a background refresh), so it reaches its one
    lookup; the junk around it is refused from memory."""
    settings = session_settings(database_url, session_fail_global=4, trust_proxy=False)
    async for api in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        member = await person(api, "ada@example.com", "OWNER")  # minted after startup: cold
        cookie = member["cookie"].split("=", 1)[1]
        throttle: SessionThrottle = api.app.state.session_throttle
        assert throttle.active is not None
        assert await throttle.active.refresh()  # what the background task does every minute
        for _ in range(4):
            assert (await probe(api, junk())).status_code == 401
        assert (await probe(api, junk())).status_code == 429  # the global window is closed
        before = len(lookups)
        answer = await probe(api, cookie)
        assert answer.status_code == 200  # cold, valid, and served: one lookup
        assert len(lookups) == before + 1
        assert (await probe(api, junk())).status_code == 429  # junk is still refused


async def test_a_session_created_on_another_process_waits_at_most_for_the_next_refresh(
    database_url: str, engine: AsyncEngine
) -> None:
    """The documented residual: during a closed window, a session this process has neither seen
    sign in nor loaded is answered 429 (not 401: the web app retries instead of signing out)
    until the hint set refreshes."""
    settings = session_settings(database_url, session_fail_global=2, trust_proxy=False)
    async for api in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        for _ in range(3):  # the third uses the budget of two up and closes the window
            await probe(api, junk())
        member = await person(api, "ada@example.com", "OWNER")
        cookie = member["cookie"].split("=", 1)[1]
        assert (await probe(api, cookie)).status_code == 429
        assert api.app.state.session_throttle.active is not None
        assert await api.app.state.session_throttle.active.refresh()
        assert (await probe(api, cookie)).status_code == 200


async def test_a_session_signed_in_here_is_served_at_once(
    database_url: str, engine: AsyncEngine
) -> None:
    settings = session_settings(database_url, session_fail_global=1, trust_proxy=False)
    async for api in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        for _ in range(2):
            await probe(api, junk())
        user = await add_member(engine, api.tenant.context.workspace_id, "bo@example.com", "OWNER")
        token = await mint_session(engine, user.id, api.clock())
        assert (await probe(api, token)).status_code == 429
        api.app.state.session_throttle.session_created(hash_session_token(token))  # the callback
        assert (await probe(api, token)).status_code == 200


async def test_a_session_revoked_after_it_was_known_valid_stops_working_at_once(
    database_url: str, engine: AsyncEngine
) -> None:
    async for api in build_api(database_url, engine, settings=session_settings(database_url)):
        member = await person(api, "ada@example.com", "OWNER")
        cookie = member["cookie"].split("=", 1)[1]
        assert (await probe(api, cookie)).status_code == 200  # known valid now
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE sessions SET revoked_at = now()"))
        gone = await probe(api, cookie)
        assert gone.status_code == 401 and gone.json()["error"]["code"] == "SESSION_INVALID"


async def test_the_active_hint_set_holds_only_usable_sessions_as_hash_prefixes(
    database_url: str, engine: AsyncEngine
) -> None:
    async for api in build_api(database_url, engine, settings=session_settings(database_url)):
        user = await add_member(engine, api.tenant.context.workspace_id, "ada@example.com", "OWNER")
        live = await mint_session(engine, user.id, api.clock())
        dead = await mint_session(engine, user.id, api.clock())
        old = await mint_session(
            engine, user.id, api.clock() - timedelta(days=30), absolute=timedelta(days=1)
        )
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE sessions SET revoked_at = now() WHERE token_hash = :h"),
                {"h": hash_session_token(dead)},
            )
        active = api.app.state.session_throttle.active
        assert await active.refresh()
        assert session_handle(hash_session_token(live)) in active
        assert session_handle(hash_session_token(dead)) not in active
        assert session_handle(hash_session_token(old)) not in active
        assert all(len(handle) == 32 for handle in active._ids)  # 16 bytes: a hint, no more


# ------------------------------------------------------------------ the address and the processes


async def test_without_a_trusted_proxy_the_header_cannot_pick_a_bucket(
    database_url: str, engine: AsyncEngine
) -> None:
    settings = session_settings(database_url, session_fail_per_address=2, trust_proxy=False)
    async for api in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        for n in range(10):  # one address would be refused after two; none is counted here
            assert (await probe(api, junk(), "203.0.113.7")).status_code == 401
            assert (await probe(api, junk(), f"198.51.100.{n}")).status_code == 401
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text("SELECT count(*) FROM rate_limit_counters WHERE scope = :s"),
                    {"s": "session_fail_address"},
                )
            ).scalar_one()
        assert rows == 0


async def test_with_a_trusted_proxy_only_the_last_entry_counts(
    database_url: str, engine: AsyncEngine
) -> None:
    settings = session_settings(database_url, session_fail_per_address=3)
    async for api in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        for n in range(3):  # the client varies what it sends; the proxy appended the same last
            forged = f"10.0.0.{n}, 203.0.113.77"
            assert (await probe(api, junk(), forged)).status_code == 401
        assert (await probe(api, junk(), "1.2.3.4, 203.0.113.77")).status_code == 429
        assert (await probe(api, junk(), "203.0.113.78")).status_code == 401


async def test_two_processes_share_the_failure_budget(
    database_url: str, engine: AsyncEngine
) -> None:
    settings = session_settings(database_url, session_fail_global=6, trust_proxy=False)
    async for first in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        async with another_process(first) as second:
            for i in range(6):
                target = first if i % 2 == 0 else second
                assert (await probe(target, junk())).status_code == 401
            assert (await probe(first, junk())).status_code == 429
            assert (await probe(second, junk())).status_code == 429


# ------------------------------------------------------------------ failure, logs, metrics


async def test_the_throttle_fails_open_to_the_local_budget_when_counters_are_down() -> None:
    dead = create_async_engine(UNREACHABLE_DATABASE_URL, poolclass=NullPool)
    settings = session_settings(
        "postgresql+asyncpg://x/y", session_fail_global=4, trust_proxy=False
    )
    counters = SharedCounters(dead, timeout_seconds=0.2, backoff_seconds=5.0)
    obs = build_observability()
    throttle = SessionThrottle(settings, counters, metrics=obs.catalogue)
    try:
        outcomes: list[int] = []
        for _ in range(8):
            try:
                attempt = throttle.admit(junk(), None)
            except AppError as gate:  # the window the failures closed
                outcomes.append(gate.status_code)
                continue
            assert attempt is not None
            refused = await throttle.failed(attempt)
            outcomes.append(refused.status_code if refused else 401)
        assert outcomes[:4] == [401, 401, 401, 401]  # never a 500; the local window bounds it
        assert outcomes[4:] == [429] * 4
        with pytest.raises(AppError) as gate:
            throttle.admit(junk(), None)
        assert gate.value.status_code == 429
        assert value(obs, "session_throttle_blocks_total", kind="global") >= 1
    finally:
        await dead.dispose()


async def test_neither_cookies_nor_addresses_are_logged_stored_or_exported(
    database_url: str, engine: AsyncEngine, capsys: pytest.CaptureFixture[str]
) -> None:
    obs = build_observability()
    settings = session_settings(database_url, session_fail_per_address=2, session_fail_global=4)
    async for api in build_api(
        database_url, engine, settings=settings, app_options={"observability": obs}
    ):
        await room_in_window(engine, 60)
        cookies = [junk() for _ in range(8)]
        for cookie in cookies:
            await probe(api, cookie, "198.51.100.23")
        logged = capsys.readouterr().out
        assert "198.51.100.23" not in logged
        for cookie in cookies:
            assert cookie not in logged
            assert hash_session_token(cookie).hex() not in logged
        assert "session lookups throttled" in logged
        async with engine.connect() as conn:
            keys = (await conn.execute(text("SELECT key FROM rate_limit_counters"))).scalars().all()
            audits = (await conn.execute(text("SELECT count(*) FROM audit_log"))).scalar_one()
        assert not any("198.51.100.23" in k for k in keys)
        assert audits == 0  # an anonymous flood writes to no tenant's log
        exported = obs.registry.render()
        assert "198.51.100.23" not in exported
        assert all(cookie not in exported for cookie in cookies)
        assert value(obs, "session_throttle_blocks_total", kind="address") >= 1


async def test_the_kill_switch_turns_the_throttle_off(
    database_url: str, engine: AsyncEngine, lookups: list[int]
) -> None:
    settings = session_settings(
        database_url, session_fail_global=1, session_fail_enabled=False, trust_proxy=False
    )
    async for api in build_api(database_url, engine, settings=settings):
        for _ in range(5):
            assert (await probe(api, junk())).status_code == 401
        assert len(lookups) == 5

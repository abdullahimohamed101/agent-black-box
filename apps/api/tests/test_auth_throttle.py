"""Failed-authentication throttling (Phase 19a step 2, ADR-153, KI-019).

Only failures are counted; three shared counters (address, presented key id, global); a throttled
request is refused from memory; valid keys and sessions are never refused by a flood of bad ones.
"""

import secrets
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.auth.keys import generate_key
from abb_api.auth.repository import ApiKeyLookup
from abb_api.auth.throttle import AuthThrottle, _LocalWindow
from abb_api.core.client_address import client_address
from abb_api.core.errors import AppError
from abb_api.limits import SharedCounters
from tests.api_fixtures import Api, build_api, person
from tests.conftest import UNREACHABLE_DATABASE_URL, make_settings
from tests.limits.helpers import room_in_window
from tests.limits.test_multi_process import another_process

BIG = 1_000_000


def throttle_settings(database_url: str, **extra: Any) -> Any:
    options: dict[str, Any] = {
        "trust_proxy": True,
        "auth_fail_per_address": BIG,
        "auth_fail_per_credential": BIG,
        "auth_fail_global": BIG,
        "limits_block_divisor": 1000,  # blocks of one: every failure is counted exactly
    }
    options.update(extra)
    return make_settings(database_url, **options)


def wrong_secret(token: str) -> str:
    """The same key id with a different (well-formed) secret."""
    return token.split(".", 1)[0] + "." + secrets.token_urlsafe(32)


def unknown_key() -> str:
    return generate_key().token


async def probe(
    api: Api, token: str, address: str | None = None, path: str = "/v1/runs"
) -> httpx.Response:
    headers = {"authorization": f"Bearer {token}"}
    if address:
        headers["x-forwarded-for"] = address
    return await api.client.get(path, headers=headers)


def body_of(response: httpx.Response) -> dict[str, Any]:
    error: dict[str, Any] = dict(response.json()["error"])
    error.pop("request_id", None)
    return error


@pytest.fixture
def lookups(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Every key id the application asked the database about."""
    seen: list[str] = []
    real = ApiKeyLookup.find

    async def spy(self: Any, key_id: str) -> Any:
        seen.append(key_id)
        return await real(self, key_id)

    monkeypatch.setattr(ApiKeyLookup, "find", spy)
    yield seen


# ------------------------------------------------------------------ the address counter


async def test_failures_from_one_address_are_throttled_with_retry_after(
    database_url: str, engine: AsyncEngine, lookups: list[str]
) -> None:
    settings = throttle_settings(database_url, auth_fail_per_address=5)
    async for api in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        for _ in range(5):
            assert (await probe(api, unknown_key(), "203.0.113.9")).status_code == 401
        blocked = await probe(api, unknown_key(), "203.0.113.9")
        assert blocked.status_code == 429
        assert blocked.json()["error"]["code"] == "AUTH_THROTTLED"
        assert blocked.json()["error"]["category"] == "RATE_LIMIT"
        wait = int(blocked.headers["retry-after"])
        assert 1 <= wait <= 60
        assert blocked.json()["error"]["details"]["retry_after_seconds"] == wait
        assert "www-authenticate" not in blocked.headers

        # Throttled means no work at all: no key lookup and no counter write.
        counters = api.app.state.shared_counters
        calls, looked = counters.database_calls, len(lookups)
        for _ in range(50):
            assert (await probe(api, unknown_key(), "203.0.113.9")).status_code == 429
        assert counters.database_calls == calls
        assert len(lookups) == looked

        # Another address is untouched.
        assert (await probe(api, unknown_key(), "203.0.113.10")).status_code == 401


async def test_only_the_last_forwarded_entry_counts_and_junk_is_ignored(
    database_url: str, engine: AsyncEngine
) -> None:
    settings = throttle_settings(database_url, auth_fail_per_address=3)
    async for api in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        # Client-supplied leading entries cannot pick or dodge a bucket: the proxy's entry counts.
        for i in range(3):
            reply = await probe(api, unknown_key(), f"10.0.0.{i}, 198.51.100.7")
            assert reply.status_code == 401
        assert (await probe(api, unknown_key(), "10.9.9.9, 198.51.100.7")).status_code == 429
        # An unparseable address is no address: nothing to throttle on.
        for _ in range(8):
            assert (await probe(api, unknown_key(), "not-an-address")).status_code == 401
        # IPv6 is bucketed by its /64: rotating the low bits does not evade.
        for i in range(3):
            assert (await probe(api, unknown_key(), f"2001:db8:1:2::{i + 1}")).status_code == 401
        assert (await probe(api, unknown_key(), "2001:db8:1:2:ffff::9")).status_code == 429


async def test_without_a_trusted_proxy_the_header_is_ignored(
    database_url: str, engine: AsyncEngine
) -> None:
    settings = throttle_settings(database_url, trust_proxy=False, auth_fail_per_address=2)
    async for api in build_api(database_url, engine, settings=settings):
        # Cannot be used to evade: rotating the header changes nothing, there is no address bucket.
        for i in range(10):
            assert (await probe(api, unknown_key(), f"203.0.113.{i}")).status_code == 401
        # Cannot be used to frame: flooding "as" a victim's address leaves the victim untouched.
        for _ in range(10):
            await probe(api, unknown_key(), "198.51.100.77")
        assert (await probe(api, unknown_key(), "198.51.100.77")).status_code == 401
        async with api.engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM rate_limit_counters WHERE scope = 'auth_fail_address'"
                    )
                )
            ).scalar_one()
        assert rows == 0


def test_client_address_rules() -> None:
    def request(header: str | None) -> Any:
        headers = {"x-forwarded-for": header} if header is not None else {}
        return httpx.Request("GET", "http://x", headers=headers)

    class Wrapper:
        def __init__(self, header: str | None) -> None:
            self.headers = request(header).headers

    assert client_address(Wrapper("1.2.3.4"), trust_proxy=False) is None  # type: ignore[arg-type]
    assert client_address(Wrapper("1.2.3.4"), trust_proxy=True) == "1.2.3.4"  # type: ignore[arg-type]
    assert client_address(Wrapper("9.9.9.9, 1.2.3.4"), trust_proxy=True) == "1.2.3.4"  # type: ignore[arg-type]
    assert client_address(Wrapper("::ffff:1.2.3.4"), trust_proxy=True) == "1.2.3.4"  # type: ignore[arg-type]
    assert client_address(Wrapper(None), trust_proxy=True) is None  # type: ignore[arg-type]
    assert client_address(Wrapper("x" * 80), trust_proxy=True) is None  # type: ignore[arg-type]


# ------------------------------------------------------------------ the credential counter


async def test_one_hammered_key_id_throttles_only_that_key_id(
    database_url: str, engine: AsyncEngine
) -> None:
    settings = throttle_settings(database_url, auth_fail_per_credential=4)
    async for api in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        real = api.tokens["writer"]
        for _ in range(4):
            assert (await probe(api, wrong_secret(real))).status_code == 401
        refused = await probe(api, wrong_secret(real))
        assert refused.status_code == 429
        # Other key ids, real or not, are untouched, and so is the same key id on a fresh address.
        assert (await probe(api, wrong_secret(api.tokens["reader"]))).status_code == 401
        assert (await probe(api, unknown_key())).status_code == 401


async def test_throttled_bodies_do_not_reveal_whether_a_key_exists(
    database_url: str, engine: AsyncEngine
) -> None:
    settings = throttle_settings(database_url, auth_fail_per_credential=2)
    async for api in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        real = wrong_secret(api.tokens["writer"])
        ghost = unknown_key()
        # Unknown, wrong-secret and revoked keys answer with one and the same 401 body.
        unknown_reply = await probe(api, ghost)
        wrong_reply = await probe(api, real)
        revoked_reply = await probe(api, api.tokens["revoked"])
        assert body_of(unknown_reply) == body_of(wrong_reply) == body_of(revoked_reply)
        # And once a key id is throttled, an existing and a never-issued one look the same.
        await probe(api, real)
        await probe(api, ghost)
        throttled_real, throttled_ghost = await probe(api, real), await probe(api, ghost)
        assert throttled_real.status_code == throttled_ghost.status_code == 429
        a, b = body_of(throttled_real), body_of(throttled_ghost)
        for body in (a, b):
            body["details"].pop("retry_after_seconds")
        assert a == b


# ------------------------------------------------------------------ the global ceiling


async def test_bad_keys_spread_over_many_key_ids_hit_the_global_ceiling(
    database_url: str, engine: AsyncEngine, lookups: list[str]
) -> None:
    settings = throttle_settings(database_url, auth_fail_global=25)
    async for api in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        base = len(lookups)
        codes = [(await probe(api, unknown_key())).status_code for _ in range(60)]
        assert codes[:25] == [401] * 25
        assert set(codes[25:]) == {429}
        # The database was asked about at most the ceiling, however many requests came.
        # (the request that used up the last unit was looked up too, hence ceiling + 1)
        assert len(lookups) - base <= 26


async def test_a_closed_window_stays_closed_for_the_rest_of_the_window(
    database_url: str, engine: AsyncEngine, lookups: list[str]
) -> None:
    """Regression: a refusal from a refilling local bucket reported "retry in 0.1 s", so the gate
    reopened ten times a second and a long flood kept costing lookups and counter writes."""
    import asyncio

    settings = throttle_settings(database_url, auth_fail_global=20)
    async for api in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60, minimum=10.0)
        base = len(lookups)
        counters = api.app.state.shared_counters
        for i in range(60):
            await probe(api, unknown_key(), f"198.51.100.{i}")
        calls = counters.database_calls
        for round_ in range(8):  # two seconds of continued flooding
            await asyncio.sleep(0.25)
            for i in range(25):
                assert (
                    await probe(api, unknown_key(), f"192.0.2.{round_ * 25 + i}")
                ).status_code == 429
        assert len(lookups) - base <= 21
        assert counters.database_calls == calls


def test_the_local_window_reports_the_time_left_in_its_window() -> None:
    now = [1000.5]
    window = _LocalWindow(2, 60, 10, wall=lambda: now[0])
    assert window.acquire("k", events=1, bytes_=0) is None
    assert window.acquire("k", events=1, bytes_=0) is None
    wait = window.acquire("k", events=1, bytes_=0)
    assert wait is not None and 19 < wait < 20  # window 960..1020, now 1000.5
    now[0] = 1020.0
    assert window.acquire("k", events=1, bytes_=0) is None  # next window, fresh budget


async def test_a_flood_never_refuses_a_valid_key_or_a_session(
    database_url: str, engine: AsyncEngine
) -> None:
    settings = throttle_settings(
        database_url, auth_fail_global=10, auth_fail_per_address=5, auth_fail_per_credential=5
    )
    async for api in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        address = "203.0.113.50"
        assert (await probe(api, api.tokens["writer"], address)).status_code == 200  # warms it
        member = await person(api, "ada@example.com", "OWNER")
        for _ in range(40):
            await probe(api, unknown_key(), address)
        assert (await probe(api, unknown_key(), address)).status_code == 429
        # The same address, the same process, a ceiling in force: the valid key still works...
        for _ in range(5):
            assert (await probe(api, api.tokens["writer"], address)).status_code == 200
        reply = await api.client.post(
            "/v1/events/batch",
            content=b'{"events": []}',
            headers=api.headers("writer", **{"x-forwarded-for": address}),
        )
        assert reply.status_code != 429
        # ...a person with a session is never looked at by the key throttle...
        assert (await api.client.get("/v1/me", headers=member)).status_code == 200
        assert (await api.client.get("/v1/runs", headers=member)).status_code == 200
        # ...and nor is a request that carries no credential at all (it gets the plain 401).
        assert (await api.client.get("/v1/runs")).status_code == 401


async def test_a_key_not_yet_seen_by_this_process_waits_out_a_closed_window(
    database_url: str, engine: AsyncEngine
) -> None:
    """The stated cost of refusing before any lookup (ADR-153): while a window is closed for an
    address or the whole installation, a valid key this process has not authenticated recently is
    refused with Retry-After too (an SDK retries); a key it has seen keeps working."""
    settings = throttle_settings(database_url, auth_fail_global=3)
    async for api in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        assert (await probe(api, api.tokens["writer"])).status_code == 200
        for _ in range(6):
            await probe(api, unknown_key())
        cold = await probe(api, api.tokens["beta"])
        assert cold.status_code == 429
        assert int(cold.headers["retry-after"]) >= 1
        assert (await probe(api, api.tokens["writer"])).status_code == 200


async def test_a_key_revoked_after_it_was_known_valid_stops_working_at_once(
    database_url: str, engine: AsyncEngine
) -> None:
    settings = throttle_settings(database_url)
    async for api in build_api(database_url, engine, settings=settings):
        token = api.tokens["writer"]
        assert (await probe(api, token)).status_code == 200
        key_id = token.removeprefix("abb_live_").split(".")[0]
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE api_keys SET revoked_at = now() WHERE key_id = :k"), {"k": key_id}
            )
        reply = await probe(api, token)
        assert reply.status_code == 401  # the gate exemption never skips authentication
        assert reply.json()["error"]["code"] == "API_KEY_INVALID"


# ------------------------------------------------------------------ the caches


async def test_a_new_key_works_at_once_after_its_neighbours_failed(
    database_url: str, engine: AsyncEngine, lookups: list[str]
) -> None:
    from abb_api.auth import scopes
    from abb_api.auth.repository import ApiKeyRepository

    settings = throttle_settings(database_url, auth_fail_per_credential=8)
    async for api in build_api(database_url, engine, settings=settings):
        real = api.tokens["writer"]
        for _ in range(3):
            assert (await probe(api, wrong_secret(real))).status_code == 401
        repeated = wrong_secret(real)
        await probe(api, repeated)
        before = len(lookups)
        await probe(api, repeated)
        assert len(lookups) == before  # negative cache: the same bad token is not looked up again
        # The real key (same key id, different token) is not affected by what was cached.
        assert (await probe(api, real)).status_code == 200
        # A key created right now works on its first request.
        async with engine.begin() as conn:
            fresh = await ApiKeyRepository(conn, api.tenant.context).create(
                scopes=frozenset({scopes.RUNS_READ}), project_id=api.tenant.project_uuids["alpha"]
            )
        assert (await probe(api, fresh.token)).status_code == 200


def test_the_caches_are_bounded_and_expire(database_url: str) -> None:
    import asyncio

    now = [1000.0]
    settings = throttle_settings(
        database_url,
        auth_fail_cache_entries=50,
        auth_fail_negative_seconds=30,
        auth_fail_known_valid_seconds=10,
    )
    throttle = AuthThrottle(settings, None, None, monotonic=lambda: now[0])  # type: ignore[arg-type]

    async def run() -> None:
        for _ in range(500):
            attempt = throttle.admit(unknown_key(), None)
            assert attempt is not None
            await throttle.failed(attempt, None)
            throttle.succeeded(attempt)
        assert len(throttle._known_invalid) <= 50
        assert len(throttle._known_valid) <= 50
        assert len(throttle._blocked) <= 50
        probe_attempt = throttle.admit(unknown_key(), None)
        assert probe_attempt is not None
        await throttle.failed(probe_attempt, None)
        assert throttle.known_invalid(probe_attempt)
        now[0] += 29
        assert throttle.known_invalid(probe_attempt)
        now[0] += 2
        assert not throttle.known_invalid(probe_attempt)  # expired
        throttle.succeeded(probe_attempt)
        assert throttle._known_valid.get(probe_attempt.digest)[0]
        await throttle.failed(probe_attempt, None)  # a failure evicts the "known valid" mark
        assert not throttle._known_valid.get(probe_attempt.digest)[0]

    asyncio.run(run())


async def test_successes_are_never_negatively_cached(
    database_url: str, engine: AsyncEngine, lookups: list[str]
) -> None:
    async for api in build_api(database_url, engine, settings=throttle_settings(database_url)):
        token = api.tokens["writer"]
        base = len(lookups)
        for _ in range(5):
            assert (await probe(api, token)).status_code == 200
        assert len(lookups) - base == 5  # every success is authenticated for real
        throttle: AuthThrottle = api.app.state.auth_throttle
        assert len(throttle._known_invalid) == 0


# ------------------------------------------------------------------ across processes


async def test_two_processes_share_the_failure_budget(
    database_url: str, engine: AsyncEngine
) -> None:
    settings = throttle_settings(database_url, auth_fail_per_credential=6)
    async for first in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        async with another_process(first) as second:
            real = first.tokens["writer"]
            for i in range(6):
                target = first if i % 2 == 0 else second
                assert (await probe(target, wrong_secret(real))).status_code == 401
            assert (await probe(first, wrong_secret(real))).status_code == 429
            assert (await probe(second, wrong_secret(real))).status_code == 429


async def test_a_throttling_leaves_one_audit_row_per_window(
    database_url: str, engine: AsyncEngine
) -> None:
    settings = throttle_settings(database_url, auth_fail_per_credential=3)
    async for first in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        async with another_process(first) as second:
            real = first.tokens["writer"]
            for i in range(60):
                await probe(first if i % 2 == 0 else second, wrong_secret(real))
            await probe(first, wrong_secret(first.tokens["reader"]))
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT actor_kind, actor_id, outcome, resource_kind, details::text"
                        " FROM audit_log WHERE action = 'auth.throttled'"
                    )
                )
            ).all()
        assert len(rows) == 1  # a workspace, a window: one row, however many failures and processes
        kind, actor, outcome, resource, details = rows[0]
        assert (kind, outcome, resource) == ("system", "denied", "api_key")
        assert actor == "system:auth-throttle"
        assert "credential" in details and "window_seconds" in details
        assert "abb_live_" not in details


async def test_a_throttling_of_an_unknown_key_writes_no_audit_row(
    database_url: str, engine: AsyncEngine
) -> None:
    settings = throttle_settings(database_url, auth_fail_global=3)
    async for api in build_api(database_url, engine, settings=settings):
        for _ in range(10):
            await probe(api, unknown_key())
        async with engine.connect() as conn:
            count = (
                await conn.execute(
                    text("SELECT count(*) FROM audit_log WHERE action = 'auth.throttled'")
                )
            ).scalar_one()
        assert count == 0  # no workspace to write it in; the log line carries the scope name


# ------------------------------------------------------------------ logs and failure


async def test_neither_secrets_nor_addresses_are_logged_or_stored(
    database_url: str, engine: AsyncEngine, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = throttle_settings(database_url, auth_fail_per_address=2, auth_fail_per_credential=2)
    async for api in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        real = api.tokens["writer"]
        tokens = [wrong_secret(real) for _ in range(6)]
        for token in tokens:
            await probe(api, token, "198.51.100.23")
        text_logged = capsys.readouterr().out
        assert "198.51.100.23" not in text_logged
        for token in tokens:
            assert token.split(".", 1)[1] not in text_logged
        async with engine.connect() as conn:
            keys = (await conn.execute(text("SELECT key FROM rate_limit_counters"))).scalars().all()
            audits = (
                (await conn.execute(text("SELECT details::text FROM audit_log"))).scalars().all()
            )
        assert not any("198.51.100.23" in k for k in keys)
        assert not any("198.51.100.23" in a for a in audits)
        assert "authentication throttled" in text_logged


async def test_the_throttle_fails_open_to_the_local_budget_when_counters_are_down(
    database_url: str, engine: AsyncEngine
) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    dead = create_async_engine(UNREACHABLE_DATABASE_URL, poolclass=NullPool)
    settings = throttle_settings(database_url, auth_fail_per_credential=4)
    counters = SharedCounters(dead, timeout_seconds=0.2, backoff_seconds=5.0)
    throttle = AuthThrottle(settings, counters, dead)
    key_id = generate_key().key_id
    codes: list[int] = []
    for _ in range(8):
        try:
            attempt = throttle.admit(f"abb_live_{key_id}.{secrets.token_urlsafe(32)}", None)
        except AppError as gate:
            codes.append(gate.status_code)
            continue
        assert attempt is not None
        refused = await throttle.failed(attempt, uuid.uuid4())
        codes.append(refused.status_code if refused else 401)
    # No exception, no lock-out of everyone: the local bucket still bounds one key id.
    assert codes[:4] == [401] * 4
    assert 429 in codes
    assert counters.fail_open_events >= 1
    # An unrelated key id is untouched by the outage.
    other = throttle.admit(generate_key().token, None)
    assert other is not None
    await dead.dispose()


# ------------------------------------------------------------------ login paths


async def test_oidc_callbacks_and_sign_in_starts_are_not_behind_the_key_throttle(
    database_url: str, engine: AsyncEngine
) -> None:
    from tests.test_auth_sessions import oidc_settings

    settings = oidc_settings(
        database_url,
        trust_proxy=True,
        auth_fail_global=2,
        auth_fail_per_address=2,
        auth_fail_per_credential=2,
        limits_block_divisor=1000,
    )
    async for api in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        for _ in range(8):
            await probe(api, unknown_key(), "203.0.113.77")
        assert (await probe(api, unknown_key(), "203.0.113.77")).status_code == 429
        callback = await api.client.get(
            "/v1/auth/callback?code=x&state=y", headers={"x-forwarded-for": "203.0.113.77"}
        )
        assert callback.status_code != 429  # refused for its own reasons (400), never throttled
        start = await api.client.get("/v1/auth/login", headers={"x-forwarded-for": "203.0.113.77"})
        assert start.status_code != 429

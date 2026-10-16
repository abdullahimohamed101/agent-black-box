"""A valid key this process has never seen is not locked out by a flood (ADR-153 point 3, KI-175).

Every test here goes through HTTP only, so they describe behaviour a client can see: while the
global or an address window is closed, a request for a key id that exists reaches full
authentication (bounded by that key id's own credential budget) and a random key id is refused from
memory without a lookup.
"""

import secrets
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.auth import scopes
from abb_api.auth.throttle import AuthThrottle
from tests.api_fixtures import WEB_ORIGIN, build_api, person
from tests.limits.helpers import room_in_window
from tests.limits.test_multi_process import another_process
from tests.test_auth_throttle import (
    lookups as lookups,
)
from tests.test_auth_throttle import (
    probe,
    throttle_settings,
    unknown_key,
    wrong_secret,
)


def key_id_of(token: str) -> str:
    return token.removeprefix("abb_live_").split(".")[0]


async def flood_until_closed(api: Any, *, address: str | None = None, rounds: int = 40) -> None:
    for _ in range(rounds):
        await probe(api, unknown_key(), address)


async def test_a_cold_valid_key_authenticates_while_the_global_window_is_closed(
    database_url: str, engine: AsyncEngine
) -> None:
    """The reviewer's repro: the flood closes the global window; a key this process has never
    authenticated used to be refused with 429 for the rest of the window."""
    settings = throttle_settings(database_url, auth_fail_global=3)
    async for api in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        await flood_until_closed(api)
        assert (await probe(api, unknown_key())).status_code == 429  # the window is closed
        for name in ("writer", "reader", "beta", "wide_reader"):  # none has been used here
            assert (await probe(api, api.tokens[name])).status_code == 200, name


async def test_a_cold_valid_key_authenticates_while_an_address_window_is_closed(
    database_url: str, engine: AsyncEngine
) -> None:
    settings = throttle_settings(database_url, auth_fail_per_address=3)
    async for api in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        await flood_until_closed(api, address="203.0.113.5", rounds=10)
        assert (await probe(api, unknown_key(), "203.0.113.5")).status_code == 429
        assert (await probe(api, api.tokens["beta"], "203.0.113.5")).status_code == 200


async def test_random_key_ids_are_still_refused_from_memory_with_no_lookup(
    database_url: str, engine: AsyncEngine, lookups: list[str]
) -> None:
    settings = throttle_settings(database_url, auth_fail_global=3)
    async for api in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        await flood_until_closed(api)
        counters = api.app.state.shared_counters
        looked, calls = len(lookups), counters.database_calls
        for _ in range(100):
            assert (await probe(api, unknown_key())).status_code == 429
        assert len(lookups) == looked
        assert counters.database_calls == calls


async def test_lookups_for_a_real_key_id_under_attack_are_bounded_by_its_credential_budget(
    database_url: str, engine: AsyncEngine, lookups: list[str]
) -> None:
    """The database work an attacker can cause while the global window is closed is bounded by
    (real key ids) x (per-credential budget), not by what the attacker sends."""
    settings = throttle_settings(database_url, auth_fail_global=3, auth_fail_per_credential=4)
    async for api in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        await flood_until_closed(api)
        real = [api.tokens[n] for n in ("writer", "reader", "beta")]
        looked = len(lookups)
        codes = []
        for _ in range(60):
            for token in real:
                codes.append((await probe(api, wrong_secret(token))).status_code)
        assert len(lookups) - looked <= len(real) * 4 + 3  # +1 per id for the budget-closing try
        assert codes.count(401) <= len(real) * 4 + 3
        assert set(codes[-9:]) == {429}


async def test_a_real_key_id_whose_own_window_is_closed_is_refused_even_for_its_valid_key(
    database_url: str, engine: AsyncEngine
) -> None:
    """The residual, stated plainly (ADR-153): whoever knows a key id can close that key id's own
    window with bad secrets, for up to one window."""
    settings = throttle_settings(database_url, auth_fail_per_credential=3)
    async for api in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        real = api.tokens["beta"]
        for _ in range(5):
            await probe(api, wrong_secret(real))
        assert (await probe(api, real)).status_code == 429
        assert (await probe(api, api.tokens["writer"])).status_code == 200  # others unaffected


async def test_a_revoked_key_is_rejected_by_full_auth_and_drops_out_at_the_next_refresh(
    database_url: str, engine: AsyncEngine, lookups: list[str]
) -> None:
    settings = throttle_settings(database_url, auth_fail_global=3)
    async for api in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        token = api.tokens["beta"]
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE api_keys SET revoked_at = now() WHERE key_id = :k"),
                {"k": key_id_of(token)},
            )
        await flood_until_closed(api)
        throttle: AuthThrottle = api.app.state.auth_throttle
        assert key_id_of(token) in throttle.active_keys  # type: ignore[operator]
        # In between: still in the set, so it reaches the lookup, which rejects it. (The failure
        # is answered 429 because it is counted against the closed global window.)
        before = len(lookups)
        reply = await probe(api, token)
        assert reply.status_code in (401, 429)
        assert len(lookups) == before + 1
        # After the refresh it is no longer a real key id as far as the gate is concerned.
        assert await throttle.active_keys.refresh()  # type: ignore[union-attr]
        assert key_id_of(token) not in throttle.active_keys  # type: ignore[operator]
        before = len(lookups)
        assert (await probe(api, wrong_secret(token))).status_code == 429
        assert len(lookups) == before


async def test_an_expired_key_drops_out_at_the_next_refresh(
    database_url: str, engine: AsyncEngine
) -> None:
    settings = throttle_settings(database_url)
    async for api in build_api(database_url, engine, settings=settings):
        throttle: AuthThrottle = api.app.state.auth_throttle
        assert key_id_of(api.tokens["expired"]) not in throttle.active_keys  # type: ignore[operator]
        assert key_id_of(api.tokens["revoked"]) not in throttle.active_keys  # type: ignore[operator]
        assert key_id_of(api.tokens["writer"]) in throttle.active_keys  # type: ignore[operator]


async def test_a_key_created_through_the_api_is_admitted_immediately(
    database_url: str, engine: AsyncEngine
) -> None:
    settings = throttle_settings(database_url, auth_fail_global=3, web_origin=WEB_ORIGIN)
    async for api in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        owner = await person(api, "o@acme.test", "OWNER")
        created = await api.client.post(
            "/v1/api-keys", json={"scopes": [scopes.RUNS_READ]}, headers=owner
        )
        assert created.status_code == 201
        token = created.json()["token"]
        await flood_until_closed(api)
        assert (await probe(api, unknown_key())).status_code == 429
        assert (await probe(api, token)).status_code == 200  # no refresh has run


async def test_a_key_created_elsewhere_is_admitted_after_the_next_refresh(
    database_url: str, engine: AsyncEngine
) -> None:
    from abb_api.auth.repository import ApiKeyRepository

    settings = throttle_settings(database_url, auth_fail_global=3)
    async for api in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        await flood_until_closed(api)
        async with engine.begin() as conn:  # the CLI, or another API process
            fresh = await ApiKeyRepository(conn, api.tenant.context).create(
                scopes=frozenset({scopes.RUNS_READ}), project_id=api.tenant.project_uuids["alpha"]
            )
        assert (await probe(api, fresh.token)).status_code == 429  # bounded: until the refresh
        throttle: AuthThrottle = api.app.state.auth_throttle
        assert await throttle.active_keys.refresh()  # type: ignore[union-attr]
        assert (await probe(api, fresh.token)).status_code == 200


async def test_a_second_process_that_started_cold_admits_keys_it_has_never_seen(
    database_url: str, engine: AsyncEngine
) -> None:
    """After a deploy every key is cold in the new process; the flood keeps its window closed."""
    settings = throttle_settings(database_url, auth_fail_global=3)
    async for first in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        async with another_process(first) as second:
            await flood_until_closed(first)
            await flood_until_closed(second)  # the second process learns the window is closed
            assert (await probe(second, unknown_key())).status_code == 429
            assert (await probe(second, first.tokens["wide_reader"])).status_code == 200
            assert (await probe(first, first.tokens["beta"])).status_code == 200


async def test_the_unloaded_set_behaves_as_before_and_a_nudge_loads_it(
    database_url: str, engine: AsyncEngine
) -> None:
    """Startup race: until the first load succeeded nothing is known about key ids, so a closed
    window refuses as it always did; that request asks for a load and a later one is served."""
    import asyncio

    settings = throttle_settings(database_url, auth_fail_global=3)
    async for api in build_api(database_url, engine, settings=settings):
        await room_in_window(engine, 60)
        throttle: AuthThrottle = api.app.state.auth_throttle
        keys = throttle.active_keys
        assert keys is not None
        await keys.stop()
        keys._loaded = False  # as if the first load had failed
        keys._ids = set()
        keys._retry = 0.05
        # The retry loop would otherwise load the set during the flood whenever the machine is
        # slow (parallel workers, a busy Docker VM) and the "unknown yet" answer would be a race.
        # The load is held back until the refusal has been observed; the nudge path is unchanged.
        gate = {"open": False}

        async def refresh_when_allowed(
            real_refresh: Any = keys.refresh, gate: dict[str, bool] = gate
        ) -> bool:
            return bool(await real_refresh()) if gate["open"] else False

        keys.refresh = refresh_when_allowed  # type: ignore[method-assign]
        keys._task = asyncio.create_task(keys._run())
        await flood_until_closed(api)
        assert (await probe(api, api.tokens["beta"])).status_code == 429  # unknown yet
        gate["open"] = True
        for _ in range(100):
            if keys.loaded:
                break
            await asyncio.sleep(0.05)
        assert keys.loaded
        assert (await probe(api, api.tokens["beta"])).status_code == 200
        await keys.stop()


def test_random_secrets_are_not_key_ids() -> None:
    assert key_id_of(unknown_key()) != key_id_of(unknown_key()) and secrets.token_hex(1)

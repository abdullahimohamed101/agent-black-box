"""The in-memory set of usable key ids (ADR-153 point 3): loading, refreshing, bounds, failure."""

import asyncio
import logging
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.auth.active_keys import ActiveKeyIds
from abb_api.auth.repository import ApiKeyLookup
from abb_api.auth.throttle import AuthThrottle
from abb_api.core.errors import AppError
from tests.api_fixtures import NOW, build_api
from tests.conftest import make_settings


def loader_of(*batches: object):  # type: ignore[no-untyped-def]
    """A loader returning each batch in turn (an exception instance is raised)."""
    queue = list(batches)
    calls: list[int] = []

    async def load(limit: int):  # type: ignore[no-untyped-def]
        calls.append(limit)
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, BaseException):
            raise item
        return item

    load.calls = calls  # type: ignore[attr-defined]
    return load


async def test_refresh_replaces_the_set_and_a_failure_keeps_the_previous_one() -> None:
    load = loader_of(["a", "b"], RuntimeError("db down"), ["b", "c"])
    keys = ActiveKeyIds(load)
    assert not keys.loaded and "a" not in keys
    assert await keys.refresh()
    assert keys.loaded and "a" in keys and "b" in keys
    assert not await keys.refresh()  # failure: previous set kept, still serving
    assert keys.loaded and "a" in keys and keys.failures == 1
    assert await keys.refresh()
    assert "a" not in keys and "c" in keys  # a revoked or expired key dropped out


async def test_a_slow_load_is_bounded_and_keeps_the_previous_set() -> None:
    async def slow(limit: int) -> list[str]:
        await asyncio.sleep(10)
        return []

    keys = ActiveKeyIds(slow, timeout_seconds=0.05)
    keys.add("x")
    assert not await keys.refresh()
    assert "x" in keys and not keys.loaded


async def test_a_key_added_while_a_load_runs_is_not_lost() -> None:
    gate = asyncio.Event()

    async def load(limit: int) -> list[str]:
        await gate.wait()
        return ["old"]  # a snapshot from before the new key was committed

    keys = ActiveKeyIds(load)
    task = asyncio.create_task(keys.refresh())
    await asyncio.sleep(0.01)
    keys.add("fresh")
    gate.set()
    assert await task
    assert "old" in keys and "fresh" in keys


async def test_the_cap_bounds_the_set_and_is_logged_once(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    load = loader_of([f"k{i}" for i in range(11)])
    keys = ActiveKeyIds(load, cap=10)
    assert await keys.refresh() and await keys.refresh()
    assert len(keys) == 10
    assert load.calls == [11, 11]  # asks for one more than the cap, to know it was exceeded
    assert sum("more active API keys than the cap" in r.message for r in caplog.records) == 1
    keys.add("extra")
    assert len(keys) == 10  # nothing grows it past the cap


async def test_start_retries_until_the_first_load_succeeds_and_stop_ends_the_task() -> None:
    load = loader_of(RuntimeError("starting up"), RuntimeError("still"), ["a"])
    keys = ActiveKeyIds(load, retry_seconds=0.02, refresh_seconds=3600)
    await keys.start()  # the first load fails; the task keeps trying
    assert not keys.loaded
    for _ in range(100):
        if keys.loaded:
            break
        await asyncio.sleep(0.02)
    assert keys.loaded and "a" in keys
    await keys.stop()
    assert keys._task is None


async def test_nudges_are_rate_limited_by_the_retry_floor() -> None:
    """Requests nudging an unloaded set must not become a query per request."""
    load = loader_of(RuntimeError("down"))
    keys = ActiveKeyIds(load, retry_seconds=0.2, refresh_seconds=3600)
    await keys.start()
    for _ in range(200):
        keys.nudge()
        await asyncio.sleep(0)
    await asyncio.sleep(0.1)
    await keys.stop()
    assert len(load.calls) <= 3  # the start, the first nudge, at most one retry


def test_without_a_set_the_throttle_behaves_as_before() -> None:
    async def run() -> None:
        settings = make_settings("postgresql+asyncpg://x", auth_fail_global=1)
        throttle = AuthThrottle(settings, None, None)  # type: ignore[arg-type]
        from tests.test_auth_throttle import unknown_key

        refused = 0
        for _ in range(4):
            try:
                attempt = throttle.admit(unknown_key(), None)
            except AppError:
                refused += 1
                continue
            assert attempt is not None
            await throttle.failed(attempt, None)
        assert refused >= 2

    asyncio.run(run())


async def test_the_query_returns_only_usable_key_ids_and_honours_the_limit(
    database_url: str, engine: AsyncEngine
) -> None:
    async for api in build_api(database_url, engine, settings=make_settings(database_url)):
        async with engine.connect() as conn:
            ids = await ApiKeyLookup(conn).active_key_ids(NOW, 1000)
            total = (await conn.execute(text("SELECT count(*) FROM api_keys"))).scalar_one()
            few = await ApiKeyLookup(conn).active_key_ids(NOW, 2)
            later = await ApiKeyLookup(conn).active_key_ids(NOW + timedelta(days=3650), 1000)
        mapping = {n: t.removeprefix("abb_live_").split(".")[0] for n, t in api.tokens.items()}
        assert mapping["revoked"] not in ids and mapping["expired"] not in ids
        assert mapping["writer"] in ids and mapping["other"] in ids
        assert len(ids) == total - 2 and len(few) == 2
        assert mapping["writer"] in later  # no expiry set: still usable

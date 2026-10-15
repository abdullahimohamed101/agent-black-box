"""The shared counter: exactness under concurrency, window edges, tenancy, expiry, failure."""

import asyncio
import logging
import time
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.limits import Scope, SharedCounters, SharedLimiterUnavailable
from tests.conftest import UNREACHABLE_DATABASE_URL
from tests.limits.helpers import pooled, room_in_window, workspace


async def rows(engine: AsyncEngine, where: str = "true") -> list[tuple[str, uuid.UUID, str, int]]:
    async with engine.connect() as conn:
        result = await conn.execute(
            text(f"SELECT scope, workspace_id, key, count FROM rate_limit_counters WHERE {where}")  # noqa: S608
        )
        return [(r[0], r[1], r[2], r[3]) for r in result]


async def test_concurrent_callers_get_exactly_the_limit_and_lose_no_updates(
    database_url: str, engine: AsyncEngine
) -> None:
    ws = workspace()
    async with pooled(database_url) as pool:
        await room_in_window(pool, 3600)
        counters = SharedCounters(pool, timeout_seconds=5)
        decisions = await asyncio.gather(
            *(
                counters.check_and_increment(Scope.POLICY_WRITE, "w", 50, 3600, workspace_id=ws)
                for _ in range(200)
            )
        )
    assert sum(d.allowed for d in decisions) == 50  # never 51, never 49
    refused = [d for d in decisions if not d.allowed]
    assert len(refused) == 150 and all(0 < d.retry_after <= 3600 for d in refused)
    (row,) = await rows(engine)
    # Every call that reached the database was counted (the rest were answered from memory).
    assert row[3] >= 50
    assert counters.database_calls == row[3]  # one unit per call: no lost updates


async def test_block_reservations_grant_exactly_the_limit(
    database_url: str, engine: AsyncEngine
) -> None:
    ws = workspace()
    async with pooled(database_url) as pool:
        await room_in_window(pool, 3600)
        counters = SharedCounters(pool, timeout_seconds=5)
        grants = await asyncio.gather(
            *(
                counters.reserve(Scope.INGEST_EVENTS, "p", 1000, 3600, amount=37, workspace_id=ws)
                for _ in range(60)
            )
        )
    assert sum(g.granted for g in grants) == 1000
    assert all(0 <= g.granted <= 37 for g in grants)


async def test_window_edge_a_new_window_starts_from_zero_on_the_database_clock(
    database_url: str, engine: AsyncEngine
) -> None:
    ws = workspace()
    async with pooled(database_url) as pool:
        counters = SharedCounters(pool, timeout_seconds=5)
        await room_in_window(pool, 3, minimum=2.0)
        first = [
            await counters.check_and_increment(Scope.ALERT_WRITE, "k", 3, 3, workspace_id=ws)
            for _ in range(5)
        ]
        assert [d.allowed for d in first] == [True, True, True, False, False]
        wait = first[-1].retry_after
        assert 0 < wait <= 3
        await asyncio.sleep(wait + 0.15)  # the memory of "full" lapses with the window
        again = await counters.check_and_increment(Scope.ALERT_WRITE, "k", 3, 3, workspace_id=ws)
        assert again.allowed
    starts = await rows(engine)
    assert len(starts) == 2  # one row per window, never a rewritten one


async def test_keys_never_cross_workspaces_or_scopes(
    database_url: str, engine: AsyncEngine
) -> None:
    a, b = workspace(), workspace()
    async with pooled(database_url) as pool:
        await room_in_window(pool, 3600)
        counters = SharedCounters(pool, timeout_seconds=5)
        for _ in range(2):
            assert (
                await counters.check_and_increment(Scope.POLICY_WRITE, "k", 2, 3600, workspace_id=a)
            ).allowed
        assert not (
            await counters.check_and_increment(Scope.POLICY_WRITE, "k", 2, 3600, workspace_id=a)
        ).allowed
        # The same key under another workspace, and another scope under the same one, are fresh.
        assert (
            await counters.check_and_increment(Scope.POLICY_WRITE, "k", 2, 3600, workspace_id=b)
        ).allowed
        assert (
            await counters.check_and_increment(Scope.ALERT_WRITE, "k", 2, 3600, workspace_id=a)
        ).allowed
        stored = {(r[0], r[1]) for r in await rows(engine)}
        assert stored == {("policy_write", a), ("policy_write", b), ("alert_write", a)}


async def test_scopes_enforce_their_workspace_rules(engine: AsyncEngine) -> None:
    counters = SharedCounters(engine)
    with pytest.raises(ValueError, match="needs a workspace"):
        await counters.check_and_increment(Scope.POLICY_WRITE, "k", 1, 60)
    with pytest.raises(ValueError, match="global"):
        await counters.check_and_increment(Scope.LOGIN_START, "k", 1, 60, workspace_id=uuid.uuid4())
    assert (await counters.check_and_increment(Scope.LOGIN_START, "auth", 5, 60)).allowed
    (row,) = await rows(engine)
    assert row[:2] == ("login_start", uuid.UUID(int=0))


async def test_an_exhausted_window_is_answered_from_memory(
    database_url: str, engine: AsyncEngine
) -> None:
    ws = workspace()
    async with pooled(database_url) as pool:
        await room_in_window(pool, 3600)
        counters = SharedCounters(pool, timeout_seconds=5)
        for _ in range(5):
            await counters.check_and_increment(Scope.ALERT_TEST, "k", 5, 3600, workspace_id=ws)
        calls = counters.database_calls
        refused = [
            await counters.check_and_increment(Scope.ALERT_TEST, "k", 5, 3600, workspace_id=ws)
            for _ in range(500)
        ]
    assert not any(d.allowed for d in refused)
    assert counters.database_calls == calls  # a flood of refusals writes nothing
    assert all(0 < d.retry_after <= 3600 for d in refused)


async def test_expired_windows_are_pruned_in_bounded_batches(
    database_url: str, engine: AsyncEngine
) -> None:
    ws = workspace()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO rate_limit_counters (scope, workspace_id, key, window_start, count,"
                " expires_at) SELECT 'alert_write', :ws, 'old' || g, now() - interval '2 hours',"
                " 1, now() - interval '1 hour' FROM generate_series(1, 25) g"
            ),
            {"ws": ws},
        )
    async with pooled(database_url) as pool:
        counters = SharedCounters(pool, timeout_seconds=5, prune_batch=10)
        assert await counters.prune_expired() == 10  # a batch, not the whole table
        assert len(await rows(engine)) == 15
        # Opportunistic: the next reservation prunes again, and a full batch brings the next
        # prune forward until the backlog is gone.
        for _ in range(4):
            counters._next_prune = 0.0
            await counters.reserve(Scope.ALERT_WRITE, "live", 100, 3600, amount=1, workspace_id=ws)
            await counters.wait_for_prune()  # the prune runs in the background (P2-3)
    remaining = await rows(engine)
    assert [r[2] for r in remaining] == ["live"]  # live windows are never touched


async def test_a_live_window_is_never_pruned(database_url: str, engine: AsyncEngine) -> None:
    ws = workspace()
    async with pooled(database_url) as pool:
        counters = SharedCounters(pool, timeout_seconds=5)
        await counters.reserve(Scope.ALERT_WRITE, "k", 5, 3600, amount=1, workspace_id=ws)
        assert await counters.prune_expired() == 0
    assert len(await rows(engine)) == 1


async def test_an_unreachable_database_raises_unavailable_fast_then_short_circuits(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    dead = create_async_engine(UNREACHABLE_DATABASE_URL)
    counters = SharedCounters(
        dead, timeout_seconds=1.0, backoff_seconds=30, log_interval_seconds=60
    )
    caplog.set_level(logging.WARNING)
    started = time.monotonic()
    with pytest.raises(SharedLimiterUnavailable):
        await counters.check_and_increment(
            Scope.POLICY_WRITE, "secret-key", 5, 60, workspace_id=workspace()
        )
    assert time.monotonic() - started < 2.0
    calls = counters.database_calls
    for _ in range(50):  # breaker open: no database attempt, no delay
        with pytest.raises(SharedLimiterUnavailable):
            await counters.check_and_increment(
                Scope.POLICY_WRITE, "secret-key", 5, 60, workspace_id=workspace()
            )
    assert counters.database_calls == calls
    assert counters.fail_open_events == 1
    logged = [r for r in caplog.records if "failing open" in r.getMessage()]
    assert len(logged) == 1  # once per interval, not once per request
    assert "secret-key" not in caplog.text and "abb:x" not in caplog.text
    assert getattr(logged[0], "error_class", "")
    await dead.dispose()


async def test_a_slow_database_cannot_stall_the_caller(
    database_url: str, engine: AsyncEngine
) -> None:
    """A real stall: another transaction holds the table, so the upsert blocks on a lock."""
    ws = workspace()
    async with pooled(database_url) as pool:
        counters = SharedCounters(pool, timeout_seconds=0.3, backoff_seconds=30)
        async with engine.begin() as blocker:
            await blocker.execute(text("LOCK TABLE rate_limit_counters IN ACCESS EXCLUSIVE MODE"))
            started = time.monotonic()
            with pytest.raises(SharedLimiterUnavailable):
                await counters.check_and_increment(Scope.POLICY_WRITE, "k", 5, 60, workspace_id=ws)
            assert time.monotonic() - started < 1.5
        # The breaker is open for backoff_seconds: still no database attempt after the lock is gone.
        with pytest.raises(SharedLimiterUnavailable):
            await counters.check_and_increment(Scope.POLICY_WRITE, "k", 5, 60, workspace_id=ws)
        counters._unavailable_until.clear()
        assert (
            await counters.check_and_increment(Scope.POLICY_WRITE, "k", 5, 60, workspace_id=ws)
        ).allowed


async def test_a_slow_statement_on_one_scope_does_not_disable_the_other_scopes(
    database_url: str, engine: AsyncEngine
) -> None:
    """The breaker is per scope: one timeout on one hot row used to switch the shared tier off for
    every scope (login ceiling, failed-auth counters) for the whole backoff."""
    ws = workspace()
    async with pooled(database_url) as pool:
        counters = SharedCounters(pool, timeout_seconds=1.0, backoff_seconds=30)
        real = counters._upsert

        async def hot_row_times_out(scope: Scope, *args: object) -> tuple[int, float]:
            if scope is Scope.POLICY_WRITE:
                raise TimeoutError
            return await real(scope, *args)  # type: ignore[arg-type]

        counters._upsert = hot_row_times_out  # type: ignore[method-assign]
        with pytest.raises(SharedLimiterUnavailable):
            await counters.check_and_increment(Scope.POLICY_WRITE, "hot", 5, 60, workspace_id=ws)
        # The failing scope is short-circuited for the backoff...
        before = counters.database_calls
        with pytest.raises(SharedLimiterUnavailable):
            await counters.check_and_increment(Scope.POLICY_WRITE, "hot", 5, 60, workspace_id=ws)
        assert counters.database_calls == before
        # ...every other scope still reaches the database.
        assert (
            await counters.check_and_increment(Scope.ALERT_WRITE, "k", 5, 60, workspace_id=ws)
        ).allowed
        assert (await counters.check_and_increment(Scope.AUTH_FAIL_GLOBAL, "all", 5, 60)).allowed
        assert counters.fail_open_events == 1


async def test_pruning_never_delays_the_request_that_triggers_it(
    database_url: str, engine: AsyncEngine
) -> None:
    ws = workspace()
    async with pooled(database_url) as pool:
        counters = SharedCounters(pool, timeout_seconds=5)
        calls: list[int] = []

        async def slow_delete(batch: int | None = None) -> int:
            calls.append(1)
            await asyncio.sleep(0.6)
            return 0

        counters.prune_expired = slow_delete  # type: ignore[method-assign]
        counters._next_prune = 0.0
        started = time.monotonic()
        await counters.reserve(Scope.ALERT_WRITE, "k", 100, 3600, amount=1, workspace_id=ws)
        assert time.monotonic() - started < 0.4  # the 0.6 s delete is not on the request path
        await asyncio.sleep(0.05)
        assert calls == [1]  # ...but it did start
        await counters.wait_for_prune()
        assert counters._prune_task is None


async def test_pruning_is_single_flight_and_survives_a_failing_delete(
    database_url: str, engine: AsyncEngine
) -> None:
    ws = workspace()
    async with pooled(database_url) as pool:
        counters = SharedCounters(pool, timeout_seconds=5, prune_interval_seconds=0.0)
        calls: list[int] = []

        async def failing_delete(batch: int | None = None) -> int:
            calls.append(1)
            await asyncio.sleep(0.2)
            raise RuntimeError("delete failed")

        counters.prune_expired = failing_delete  # type: ignore[method-assign]
        await asyncio.gather(
            *(
                counters.reserve(Scope.ALERT_WRITE, f"k{i}", 100, 3600, amount=1, workspace_id=ws)
                for i in range(20)
            )
        )
        assert calls == [1]  # twenty callers, one delete in flight
        await counters.wait_for_prune()  # the exception was captured, not raised
        await counters.reserve(Scope.ALERT_WRITE, "again", 100, 3600, amount=1, workspace_id=ws)
        await counters.wait_for_prune()
        assert calls == [1, 1]  # and the next interval tries again


async def test_close_cancels_a_prune_in_flight(database_url: str, engine: AsyncEngine) -> None:
    async with pooled(database_url) as pool:
        counters = SharedCounters(pool, timeout_seconds=5)

        async def forever(batch: int | None = None) -> int:
            await asyncio.sleep(60)
            return 0

        counters.prune_expired = forever  # type: ignore[method-assign]
        await counters.reserve(
            Scope.ALERT_WRITE, "k", 100, 3600, amount=1, workspace_id=workspace()
        )
        await asyncio.sleep(0.05)
        assert counters._prune_task is not None
        await counters.close()
        assert counters._prune_task is None

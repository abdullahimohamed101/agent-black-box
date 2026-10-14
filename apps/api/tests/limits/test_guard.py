"""The two-tier guard: local first, credit blocks, the stated bound across processes, fail open."""

import asyncio
import time

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from abb_api.ingestion.ratelimit import InMemoryRateLimiter
from abb_api.limits import Dimension, GuardedLimiter, Scope, SharedCounters
from tests.conftest import UNREACHABLE_DATABASE_URL
from tests.limits.helpers import generous_local, pooled, room_in_window, workspace


def events(limit: int, window: int = 3600) -> list[Dimension]:
    return [Dimension(Scope.POLICY_WRITE, "events", limit, window)]


async def test_a_locally_refused_request_never_touches_the_database(
    database_url: str,
) -> None:
    ws = workspace()
    async with pooled(database_url) as pool:
        counters = SharedCounters(pool, timeout_seconds=5)
        local = InMemoryRateLimiter(
            events_per_second=0.001, burst_events=3, bytes_per_second=1.0, burst_bytes=1
        )
        guard = GuardedLimiter(local, counters, events(1000), block_divisor=1)
        results = [await guard.acquire("k", workspace_id=ws) for _ in range(3)]
        assert results == [None, None, None]
        calls = counters.database_calls
        refused = [await guard.acquire("k", workspace_id=ws) for _ in range(200)]
        assert all(wait is not None for wait in refused)
        assert counters.database_calls == calls  # 200 refusals, zero database work


async def test_credit_blocks_cut_database_writes(database_url: str, engine: AsyncEngine) -> None:
    ws = workspace()
    async with pooled(database_url) as pool:
        await room_in_window(pool, 3600)
        counters = SharedCounters(pool, timeout_seconds=5)
        guard = GuardedLimiter(generous_local(), counters, events(1000), block_divisor=20)
        for _ in range(100):
            assert await guard.acquire("k", workspace_id=ws) is None
        # block = 1000 // 20 = 50 units per write: 100 requests cost 2 writes, not 100.
        assert counters.database_calls == 2


async def test_credit_does_not_outlive_its_window(database_url: str, engine: AsyncEngine) -> None:
    ws = workspace()
    async with pooled(database_url) as pool:
        await room_in_window(pool, 2, minimum=1.5)
        counters = SharedCounters(pool, timeout_seconds=5)
        guard = GuardedLimiter(
            generous_local(), counters, events(100, 2), block_divisor=2
        )  # blocks of 50
        assert await guard.acquire("k", workspace_id=ws) is None
        assert counters.database_calls == 1
        await asyncio.sleep(2.3)  # the window the 49 spare units were granted in is over
        assert await guard.acquire("k", workspace_id=ws) is None
        assert counters.database_calls == 2


async def test_two_processes_share_one_budget_within_the_stated_bound(
    database_url: str, engine: AsyncEngine
) -> None:
    """Two limiter instances (two API processes) each with a local guard that would admit all
    300 requests. The fleet admits at most `limit` per window and never fewer than
    `limit - N * (block - 1)`."""
    ws = workspace()
    limit, processes, divisor = 100, 2, 20
    block = max(1, limit // divisor)
    async with pooled(database_url) as pool_a, pooled(database_url) as pool_b:
        await room_in_window(pool_a, 3600)
        fleet = [
            GuardedLimiter(
                generous_local(),
                SharedCounters(pool, timeout_seconds=5),
                events(limit),
                block_divisor=divisor,
            )
            for pool in (pool_a, pool_b)
        ]
        results = await asyncio.gather(
            *(fleet[i % 2].acquire("project", workspace_id=ws) for i in range(300))
        )
    admitted = sum(wait is None for wait in results)
    assert admitted <= limit  # no overshoot
    assert admitted >= limit - processes * (block - 1)  # bounded early refusal
    assert all(wait is not None and wait > 0 for wait in results if wait is not None)


async def test_the_unshared_combined_rate_is_n_times_the_limit_without_the_shared_tier() -> None:
    """The failure the shared tier fixes: local-only guards admit N times the limit."""
    guards = [
        GuardedLimiter(
            InMemoryRateLimiter(
                events_per_second=0.001, burst_events=100, bytes_per_second=1.0, burst_bytes=1
            ),
            None,
            events(100),
        )
        for _ in range(3)
    ]
    admitted = 0
    for i in range(600):
        admitted += await guards[i % 3].acquire("p", workspace_id=workspace()) is None
    assert admitted == 300


async def test_bytes_and_events_are_both_charged_and_one_refusal_is_enough(
    database_url: str,
) -> None:
    ws = workspace()
    dims = [
        Dimension(Scope.INGEST_EVENTS, "events", 1000, 3600),
        Dimension(Scope.INGEST_BYTES, "bytes", 5000, 3600),
    ]
    async with pooled(database_url) as pool:
        await room_in_window(pool, 3600)
        guard = GuardedLimiter(
            generous_local(), SharedCounters(pool, timeout_seconds=5), dims, block_divisor=1000
        )
        assert await guard.acquire("p", events=10, bytes_=4000, workspace_id=ws) is None
        refused = await guard.acquire("p", events=10, bytes_=2000, workspace_id=ws)
        assert refused is not None and refused > 0  # bytes ran out, events had room
        assert await guard.acquire("p", events=10, bytes_=1000, workspace_id=ws) is None


async def test_the_database_down_fails_open_to_the_local_limiter_without_delay() -> None:
    dead = create_async_engine(UNREACHABLE_DATABASE_URL)
    counters = SharedCounters(dead, timeout_seconds=1.0, backoff_seconds=60)
    local = InMemoryRateLimiter(
        events_per_second=0.001, burst_events=5, bytes_per_second=1.0, burst_bytes=1
    )
    guard = GuardedLimiter(local, counters, events(1000))
    ws = workspace()
    started = time.monotonic()
    outcomes = [await guard.acquire("k", workspace_id=ws) for _ in range(20)]
    assert time.monotonic() - started < 3.0  # one failed attempt, then the breaker is open
    assert outcomes[:5] == [None] * 5  # the shared tier is skipped: local decides
    assert all(wait is not None for wait in outcomes[5:])  # and the local limit still holds
    assert counters.fail_open_events == 1
    await dead.dispose()


async def test_a_limiter_with_no_shared_tier_is_just_the_local_one() -> None:
    guard = GuardedLimiter(
        InMemoryRateLimiter(
            events_per_second=0.001, burst_events=2, bytes_per_second=1.0, burst_bytes=1
        ),
        None,
        events(10),
    )
    ws = workspace()
    assert [await guard.acquire("k", workspace_id=ws) for _ in range(3)][2] is not None

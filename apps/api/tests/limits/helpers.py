"""Shared pieces of the limiter tests."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from abb_api.ingestion.ratelimit import InMemoryRateLimiter


@asynccontextmanager
async def pooled(url: str, size: int = 30) -> AsyncIterator[AsyncEngine]:
    """A pooled engine, so concurrent callers really run at the same time."""
    engine = create_async_engine(url, pool_size=size, max_overflow=0)
    try:
        yield engine
    finally:
        await engine.dispose()


async def room_in_window(engine: AsyncEngine, window: int, minimum: float = 5.0) -> None:
    """Wait until the database's current window has at least `minimum` seconds left, so a test
    that counts inside one window is not split by a boundary."""
    async with engine.connect() as conn:
        left = float(
            (
                await conn.execute(
                    text("SELECT :w - mod(extract(epoch FROM now())::numeric, :w)"),
                    {"w": window},
                )
            ).scalar_one()
        )
    if left < minimum:
        await asyncio.sleep(left + 0.2)


def generous_local() -> InMemoryRateLimiter:
    """A local guard that never refuses, so the shared tier is what is being tested."""
    return InMemoryRateLimiter(
        events_per_second=10**9,
        burst_events=10**9,
        bytes_per_second=10**9,
        burst_bytes=10**9,
    )


def workspace() -> uuid.UUID:
    return uuid.uuid4()

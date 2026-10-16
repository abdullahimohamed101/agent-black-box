"""Control and bounded batch deletes shared by the run procedure and the plain clean-ups (ADR-160).

`Control.checkpoint` is the kill switch and the bounds: it is called before every batch, so
switching the mode away from `enforce`, telling the process to stop, spending the tick's time
budget or the pass's row allowance all end the work at the next batch boundary.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

# Names the crash-injection hook is called with, after the thing they name has committed.
HOOK_POINTS: Final = (
    "selected",  # a candidate run was chosen; nothing of it is touched yet
    "begun",  # progress row written (nothing deleted yet)
    "marked",  # the run is marked deleting (trace and run tiers, after the artifacts)
    "artifact_batch",  # one batch of artifacts removed (files and rows)
    "artifacts",  # every artifact of the run removed
    "search",  # the search entry removed
    "detail_batch",  # one batch of events or other detail rows removed
    "detail",  # trace detail gone, run marked expired, tombstone written
    "record",  # run record deleted, tombstone written
    "analytics",  # the rollups of the day rebuilt
)

Hook = Callable[[str], Awaitable[None]]


async def no_hook(point: str) -> None:
    return None


class Halt(Exception):
    """Stop the pass at a batch boundary. `reason` is `mode`, `stop`, `budget` or `rows`."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class Control:
    """The kill switch and the bounds, consulted before every batch."""

    mode_probe: Callable[[], Awaitable[str]]
    deadline: float
    monotonic: Callable[[], float]
    max_rows: int
    stop: asyncio.Event | None = None
    rows: int = 0
    halted: str | None = None

    def begin_pass(self) -> None:
        self.rows = 0

    def spend(self, rows: int) -> None:
        self.rows += rows

    async def checkpoint(self) -> None:
        if self.stop is not None and self.stop.is_set():
            raise Halt("stop")
        if await self.mode_probe() != "enforce":
            raise Halt("mode")
        if self.monotonic() >= self.deadline:
            raise Halt("budget")
        if self.rows >= self.max_rows:
            raise Halt("rows")


class Batcher:
    """Short transactions with a statement timeout, and deletes a batch at a time."""

    def __init__(
        self,
        engine: AsyncEngine,
        control: Control,
        *,
        batch_size: int,
        timeout_ms: int,
        hook: Hook = no_hook,
    ) -> None:
        self._engine = engine
        self.control = control
        self._batch = batch_size
        self._timeout_ms = timeout_ms
        self._hook = hook

    @asynccontextmanager
    async def _tx(self) -> AsyncIterator[AsyncConnection]:
        async with self._engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL statement_timeout = {int(self._timeout_ms)}"))
            yield conn

    async def _drain(
        self,
        table: str,
        where: str,
        params: dict[str, Any],
        *,
        point: str | None = "detail_batch",
        key: str = "ctid",
    ) -> int:
        """Delete the rows of `table` matching `where`, a batch per transaction. Returns the count.

        `table` and `where` come from the inventory and this package, never from data. The ctid
        form deletes exactly the batch the sub-select picked. A table the role may read only by
        column (sessions) names a key column instead: system columns need table-wide SELECT.
        """
        total = 0
        sql = text(
            f"DELETE FROM {table} WHERE {key} = ANY(ARRAY("  # noqa: S608
            f"SELECT {key} FROM {table} WHERE {where} LIMIT :batch_size))"
        )
        while True:
            await self.control.checkpoint()
            async with self._tx() as conn:
                result = await conn.execute(sql, {**params, "batch_size": self._batch})
                n = int(result.rowcount)
            total += n
            self.control.spend(n)
            if n and point:
                await self._hook(point)
            if n < self._batch:
                return total

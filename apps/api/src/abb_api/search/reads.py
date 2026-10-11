"""Bounds on search reads: a per-process concurrency cap and one wall-clock budget per request.

The cap is a semaphore acquired **before** a pooled connection is taken, so search can hold at most
`max_concurrent` of the process's connections and ingestion is never starved by it (a full gate is
`503 SEARCH_BUSY`, immediately). The budget is one deadline for the whole request: each statement
runs read-only under `statement_timeout` set to what remains, and the optional facets statement
only runs while enough of the budget is left (plan D8). Both are per process, like every limiter
today (KI-082).
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Result
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.search.errors import search_busy, search_timeout

logger = logging.getLogger(__name__)

FACETS_MIN_BUDGET_FRACTION = 0.4


class SearchBudget:
    """One request's time. Arm each statement on a connection through `run`."""

    def __init__(self, total_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._total = total_seconds
        self._clock = clock
        self._deadline = clock() + total_seconds
        self._read_only: set[int] = set()

    def remaining_seconds(self) -> float:
        return max(self._deadline - self._clock(), 0.0)

    def remaining_fraction(self) -> float:
        return self.remaining_seconds() / self._total

    def allows_facets(self) -> bool:
        return self.remaining_fraction() >= FACETS_MIN_BUDGET_FRACTION

    async def run(self, conn: AsyncConnection, statement: Any) -> Result[Any]:
        """Execute read-only with the remaining time as the statement timeout.

        A cancelled statement (or no time left) is `SEARCH_TIMEOUT`. The transaction is read-only
        for its whole life, so a bug in a compiled statement cannot write.
        """
        remaining_ms = int(self.remaining_seconds() * 1000)
        if remaining_ms <= 0:
            raise search_timeout()
        try:
            if id(conn) not in self._read_only:
                await conn.execute(text("SET TRANSACTION READ ONLY"))
                self._read_only.add(id(conn))
            await conn.execute(text(f"SET LOCAL statement_timeout = {remaining_ms}"))
            result: Result[Any] = await conn.execute(statement)
            return result
        except DBAPIError as exc:
            if "statement timeout" in str(exc.orig) or "canceling statement" in str(exc.orig):
                logger.warning("search statement timed out")
                raise search_timeout() from exc
            raise


class SearchGate:
    """The per-process cap on concurrent searches, and the source of per-request budgets."""

    def __init__(self, *, max_concurrent: int, timeout_seconds: float) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._timeout = timeout_seconds

    @asynccontextmanager
    async def request(self) -> AsyncIterator[SearchBudget]:
        # `locked()` and `acquire()` do not yield to the loop between them when a permit is free,
        # so a full gate refuses at once instead of queueing a search behind the others.
        if self._semaphore.locked():
            raise search_busy()
        await self._semaphore.acquire()
        try:
            yield SearchBudget(self._timeout)
        finally:
            self._semaphore.release()

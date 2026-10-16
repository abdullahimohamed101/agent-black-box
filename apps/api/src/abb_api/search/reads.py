"""Bounds on search reads: a per-process, fair-share concurrency cap and one wall-clock budget per
request.

A permit is taken **before** a pooled connection, so search can hold at most `max_concurrent` of
the process's connections and ingestion is never starved by it (a full gate is `503 SEARCH_BUSY`,
immediately); the wait for a connection counts against the budget. The budget is one deadline for
the whole request: each statement runs read-only under `statement_timeout` set to what remains,
and the optional facets statement only runs while enough of the budget is left (plan D8).
Concurrency is per process by nature; who may hold how many permits is `SearchGate`'s fair share
(KI-082, ADR-158).
"""

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Result
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from abb_api.obs.catalogue import METRICS, Catalogue
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

    @asynccontextmanager
    async def connect(self, engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
        """A pooled connection whose wait is part of the budget. A starved pool is a
        `SEARCH_TIMEOUT` when the budget ends, not a `DEPENDENCY_UNAVAILABLE` after the pool's own
        timeout, and the permit is never held longer than the budget for want of a connection."""
        conn = engine.connect()
        try:
            async with asyncio.timeout(self.remaining_seconds()):
                await conn.start()
        except (TimeoutError, PoolTimeout):
            with contextlib.suppress(Exception):
                await conn.close()
            logger.warning("search gave up waiting for a database connection")
            raise search_timeout() from None
        try:
            yield conn
        finally:
            await conn.close()

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
    """The per-process admission of searches: a fair share per workspace, a cap per actor, and
    the source of per-request budgets (KI-082, ADR-158).

    Search concurrency is a process-local resource by nature (it protects this process's share of
    the database pool and its event loop), so nothing here is shared between processes; the
    request *rate* is the shared limiter's job. Admission is one synchronous check-and-increment
    (no `await` in between), so on one event loop it is atomic without a lock, and a refusal is
    immediate: a search is never queued behind others.

    Rules, with `cap` = `max_concurrent`:

    * an actor (API key or person) holds at most `min(2, max(1, cap // 2))` permits;
    * a workspace holds at most `max(1, cap // 2)` while any other workspace holds a permit, and at
      most `max(that, cap - 1)` while it is the only one searching, so a lone user is not held to
      half the process, yet one permit always stays free for the next workspace to arrive;
    * the process holds at most `cap` in total.

    A refusal is `SEARCH_BUSY` with a fixed `details.reason`: `actor_limit`, `workspace_share` or
    `process_full`. With `cap` 1 there is nothing to share: the single permit goes to whoever
    asks first.
    """

    def __init__(
        self, *, max_concurrent: int, timeout_seconds: float, metrics: Catalogue = METRICS
    ) -> None:
        self._permits = metrics.search_permits_in_use
        self._cap = max_concurrent
        self._share = max(1, max_concurrent // 2)
        self._lone = max(self._share, max_concurrent - 1)
        self._per_actor = min(2, self._share)
        self._timeout = timeout_seconds
        self._total = 0
        self._by_workspace: dict[uuid.UUID, int] = {}
        self._by_actor: dict[tuple[uuid.UUID, str], int] = {}

    @property
    def held(self) -> int:
        """Permits in use right now (tests assert this returns to zero)."""
        return self._total

    def _admit(self, workspace_id: uuid.UUID, actor: str) -> None:
        key = (workspace_id, actor)
        if self._by_actor.get(key, 0) >= self._per_actor:
            raise search_busy("actor_limit")
        mine = self._by_workspace.get(workspace_id, 0)
        others_searching = self._total > mine
        if mine >= (self._share if others_searching else self._lone):
            raise search_busy("workspace_share")
        if self._total >= self._cap:
            raise search_busy("process_full")
        self._total += 1
        self._permits.inc()
        self._by_workspace[workspace_id] = mine + 1
        self._by_actor[key] = self._by_actor.get(key, 0) + 1

    def _release(self, workspace_id: uuid.UUID, actor: str) -> None:
        self._total -= 1
        self._permits.dec()
        _decrement(self._by_workspace, workspace_id)
        _decrement(self._by_actor, (workspace_id, actor))

    @asynccontextmanager
    async def request(self, workspace_id: uuid.UUID, actor: str) -> AsyncIterator[SearchBudget]:
        self._admit(workspace_id, actor)
        try:
            yield SearchBudget(self._timeout)  # the clock starts with the permit
        finally:
            self._release(workspace_id, actor)  # every path: error, timeout, cancel, disconnect


def _decrement[K](table: dict[K, int], key: K) -> None:
    remaining = table[key] - 1
    if remaining:
        table[key] = remaining
    else:
        del table[key]  # an idle workspace or actor leaves nothing behind

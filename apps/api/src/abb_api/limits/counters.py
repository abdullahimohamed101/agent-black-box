"""PostgreSQL-backed fixed-window counters (ADR-152).

One row per (scope, workspace, key, window). Windows are aligned to the *database* clock
(`now()`), so every API process agrees on where a window starts regardless of its own clock. A
reservation is one `INSERT ... ON CONFLICT DO UPDATE ... RETURNING`: the row lock serialises
concurrent callers and the returned count is exact, so there are no lost updates.

The counter only ever increases inside a window. `reserve` turns the returned count into a grant
that never exceeds the limit: the total granted per window is at most `limit`, exactly.

This module owns the failure contract: a slow or unreachable database raises
`SharedLimiterUnavailable` within `timeout_seconds` and then short-circuits for `backoff_seconds`
so the shared tier can never stall a request. The backoff is per scope: a slow statement on one hot
row must not disable the login ceiling or the failed-auth counters. Callers (see `guard.py`) fail open to the local
limiter. Logs carry error class names only, never keys.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.limits.scopes import Scope, workspace_for

logger = logging.getLogger(__name__)

# Rows live this long past the end of their window before they may be deleted: slack for clock
# differences between the database and a reader that still looks at the previous window.
EXPIRY_GRACE_SECONDS = 60

_UPSERT = text(
    """
    WITH w AS (
        SELECT to_timestamp(floor(extract(epoch FROM now()) / CAST(:window AS double precision))
                            * CAST(:window AS double precision)) AS start
    )
    INSERT INTO rate_limit_counters AS c
        (scope, workspace_id, key, window_start, count, expires_at)
    SELECT CAST(:scope AS text), CAST(:workspace AS uuid), CAST(:key AS text), w.start,
           CAST(:amount AS bigint),
           w.start + make_interval(
               secs => CAST(:window AS double precision) + CAST(:grace AS double precision))
    FROM w
    ON CONFLICT (scope, workspace_id, key, window_start)
    DO UPDATE SET count = c.count + EXCLUDED.count
    RETURNING c.count AS count,
              extract(epoch FROM (c.window_start
                  + make_interval(secs => CAST(:window AS double precision)) - now())) AS left_s
    """
)

_PRUNE = text(
    """
    DELETE FROM rate_limit_counters WHERE ctid IN (
        SELECT ctid FROM rate_limit_counters WHERE expires_at < now()
        ORDER BY expires_at LIMIT :batch
    )
    """
)

_SET_TIMEOUT = text("SELECT set_config('statement_timeout', :ms, true)")


class SharedLimiterUnavailable(Exception):
    """The shared counter could not answer in time. The caller decides what that means."""


@dataclass(frozen=True)
class Reservation:
    granted: int  # 0..amount; the sum granted in one window never exceeds the limit
    seconds_left: float  # until the window ends (never negative)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    remaining: int
    retry_after: float  # seconds until the window resets; 0 when allowed


class SharedCounters:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        timeout_seconds: float = 0.5,
        backoff_seconds: float = 5.0,
        log_interval_seconds: float = 30.0,
        prune_interval_seconds: float = 30.0,
        prune_batch: int = 1000,
        max_exhausted: int = 10_000,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._engine = engine
        self._timeout = timeout_seconds
        self._backoff = backoff_seconds
        self._log_interval = log_interval_seconds
        self._prune_interval = prune_interval_seconds
        self._prune_batch = prune_batch
        self._max_exhausted = max_exhausted
        self._mono = monotonic
        self._unavailable_until: dict[str, float] = {}  # scope -> breaker open until
        self._last_log = float("-inf")
        self._next_prune = 0.0
        self._prune_task: asyncio.Task[None] | None = None
        # Windows already seen full: further asks are answered from memory until the window ends,
        # so a flood of rejected requests costs the database one write per process per window.
        self._exhausted: dict[tuple[str, uuid.UUID, str, int, int], float] = {}
        self.fail_open_events = 0  # observable by tests and operators (also logged)
        self.database_calls = 0

    # ------------------------------------------------------------------ public API

    async def check_and_increment(
        self,
        scope: Scope,
        key: str,
        limit: int,
        window_seconds: int,
        *,
        workspace_id: uuid.UUID | None = None,
    ) -> Decision:
        """Count one use. Raises `SharedLimiterUnavailable` when the database cannot answer."""
        got = await self.reserve(
            scope, key, limit, window_seconds, amount=1, workspace_id=workspace_id
        )
        if got.granted:
            # The grant is exact; remaining is advisory (other callers may have moved the count).
            return Decision(True, max(limit - 1, 0), 0.0)
        return Decision(False, 0, got.seconds_left)

    async def reserve(
        self,
        scope: Scope,
        key: str,
        limit: int,
        window_seconds: int,
        *,
        amount: int,
        workspace_id: uuid.UUID | None = None,
    ) -> Reservation:
        """Take up to `amount` units of this window's `limit`. Raises `SharedLimiterUnavailable`."""
        if limit < 1 or window_seconds < 1 or amount < 1:
            raise ValueError("limit, window_seconds and amount must be positive")
        workspace = workspace_for(scope, workspace_id)
        amount = min(amount, limit)
        cache_key = (scope.value, workspace, key, limit, window_seconds)
        now = self._mono()
        until = self._exhausted.get(cache_key)
        if until is not None:
            if now < until:
                return Reservation(0, until - now)
            del self._exhausted[cache_key]
        if now < self._unavailable_until.get(scope.value, 0.0):
            raise SharedLimiterUnavailable
        try:
            count, left = await self._upsert(scope, workspace, key, window_seconds, amount)
        except Exception as exc:
            self._note_failure(scope, exc)
            raise SharedLimiterUnavailable from exc
        before = count - amount
        granted = max(0, min(amount, limit - before))
        left = max(left, 0.0)
        if count >= limit:
            self._remember_exhausted(cache_key, now + left)
        self._maybe_prune()
        return Reservation(granted, left)

    async def prune_expired(self, batch: int | None = None) -> int:
        """Delete up to `batch` expired windows; returns how many went."""
        size = batch or self._prune_batch
        async with self._engine.begin() as conn:
            result = await conn.execute(_PRUNE, {"batch": size})
        return result.rowcount or 0

    # ------------------------------------------------------------------ internals

    async def _upsert(
        self, scope: Scope, workspace: uuid.UUID, key: str, window: int, amount: int
    ) -> tuple[int, float]:
        self.database_calls += 1
        async with asyncio.timeout(self._timeout):
            async with self._engine.begin() as conn:
                # The deadline also bounds the statement on the server, not just our wait for it.
                await conn.execute(_SET_TIMEOUT, {"ms": str(max(int(self._timeout * 1000), 1))})
                row = (
                    await conn.execute(
                        _UPSERT,
                        {
                            "scope": scope.value,
                            "workspace": str(workspace),
                            "key": key,
                            "window": window,
                            "amount": amount,
                            "grace": EXPIRY_GRACE_SECONDS,
                        },
                    )
                ).one()
        return int(row[0]), float(row[1])

    def _note_failure(self, scope: Scope, exc: BaseException) -> None:
        now = self._mono()
        if len(self._unavailable_until) > 64:  # scopes are a closed enum; this is only a guard
            self._unavailable_until = {k: v for k, v in self._unavailable_until.items() if v > now}
        self._unavailable_until[scope.value] = now + self._backoff
        self.fail_open_events += 1
        if now - self._last_log >= self._log_interval:
            self._last_log = now
            # Class names only: a driver message can carry the connection string or a key.
            logger.warning(
                "shared limiter unavailable; failing open to the local limiter",
                extra={
                    "error_class": type(exc).__name__,
                    "fail_open_events": self.fail_open_events,
                    "backoff_seconds": self._backoff,
                },
            )

    def _remember_exhausted(
        self, cache_key: tuple[str, uuid.UUID, str, int, int], until: float
    ) -> None:
        if len(self._exhausted) >= self._max_exhausted:
            now = self._mono()
            self._exhausted = {k: v for k, v in self._exhausted.items() if v > now}
            if len(self._exhausted) >= self._max_exhausted:  # all live: forget the oldest tenth
                for stale in list(self._exhausted)[: max(1, self._max_exhausted // 10)]:
                    del self._exhausted[stale]
        self._exhausted[cache_key] = until

    def _maybe_prune(self) -> None:
        """Bounded opportunistic cleanup so the table cannot grow without bound.

        Off the request path: a due prune runs as one background task per process (single flight),
        so a slow delete never delays the caller that happened to trigger it. At most one small
        delete per interval per process; a full batch means a backlog, so the next one may run
        almost at once. Every process prunes, so cleanup does not depend on any one of them.
        """
        now = self._mono()
        if now < self._next_prune or self._prune_task is not None:
            return
        self._next_prune = now + self._prune_interval
        self._prune_task = asyncio.create_task(self._prune_in_background(now))

    async def _prune_in_background(self, started: float) -> None:
        try:
            async with asyncio.timeout(self._timeout):
                deleted = await self.prune_expired()
            if deleted >= self._prune_batch:
                self._next_prune = started + 1.0
        except Exception as exc:  # cleanup is never on the critical path; the next interval retries
            logger.debug("rate limit prune failed", extra={"error_class": type(exc).__name__})
        finally:
            self._prune_task = None

    async def wait_for_prune(self) -> None:
        """Wait for a prune in flight, if any (tests and shutdown)."""
        task = self._prune_task
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def close(self) -> None:
        task = self._prune_task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

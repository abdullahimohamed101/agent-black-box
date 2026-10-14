"""The two-tier limiter (ADR-152): a cheap local guard in front, shared counters behind.

A request is refused locally without touching the database whenever the local token bucket or a
remembered exhausted window says no. Otherwise it spends *credit*: units reserved from the shared
counter in blocks of `k = max(1, limit // block_divisor)`. The database is written once per block
rather than once per request.

Bounds (per key, per window, N API processes, database reachable):

* Admitted by the whole fleet in one aligned window: at most `limit`, exactly. Every unit spent was
  granted by the counter, a grant never exceeds the limit, and credit expires with its window.
* Fixed windows allow up to `2 * limit` in an interval that straddles a boundary.
* Early refusals: credit sitting idle in a process is unavailable to the others, so a caller can be
  refused while the true count is below the limit by at most `N * (k - 1)`.
* Database unreachable: the local limiter alone applies, so the fleet admits up to N times the
  local limit (the pre-19a behaviour) until the database answers again.
"""

import asyncio
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

from abb_api.ingestion.ratelimit import RateLimiter
from abb_api.limits.counters import SharedCounters, SharedLimiterUnavailable
from abb_api.limits.scopes import Scope, workspace_for


class AsyncRateLimiter(Protocol):
    async def acquire(
        self, key: str, *, events: int = 1, bytes_: int = 0, workspace_id: uuid.UUID | None = None
    ) -> float | None:
        """Take capacity, or return how many seconds to wait. Never raises for a limiter fault."""
        ...


@dataclass(frozen=True)
class Dimension:
    """One shared counter a request is charged against."""

    scope: Scope
    unit: Literal["events", "bytes"]
    limit: int  # per window
    window_seconds: int


@dataclass
class _Credit:
    units: int
    deadline: float  # local monotonic time at which the window it was granted in ends
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class GuardedLimiter:
    def __init__(
        self,
        local: RateLimiter,
        counters: SharedCounters | None,
        dimensions: Sequence[Dimension],
        *,
        block_divisor: int = 20,
        max_keys: int = 10_000,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._local = local
        self._counters = counters
        self._dimensions = tuple(dimensions)
        self._divisor = max(1, block_divisor)
        self._max_keys = max_keys
        self._mono = monotonic
        self._credit: dict[tuple[Scope, uuid.UUID, str], _Credit] = {}

    @property
    def counters(self) -> SharedCounters | None:
        return self._counters

    async def acquire(
        self, key: str, *, events: int = 1, bytes_: int = 0, workspace_id: uuid.UUID | None = None
    ) -> float | None:
        wait = self._local.acquire(key, events=events, bytes_=bytes_)
        if wait is not None:
            return wait  # refused locally: no database work at all
        if self._counters is None:
            return None
        worst: float | None = None
        for dim in self._dimensions:
            cost = events if dim.unit == "events" else bytes_
            if cost <= 0:
                continue
            retry = await self._take(dim, key, workspace_id, cost)
            if retry is not None:
                worst = retry if worst is None else max(worst, retry)
                break  # one refusal is enough; do not spend the other dimension's credit
        return worst

    async def _take(
        self, dim: Dimension, key: str, workspace_id: uuid.UUID | None, cost: int
    ) -> float | None:
        assert self._counters is not None
        workspace = workspace_for(dim.scope, workspace_id)
        slot = (dim.scope, workspace, key)
        credit = self._credit.get(slot) or self._remember(slot)
        if self._spend(credit, cost):
            return None
        # One reservation in flight per key and process: a burst of requests waits for the first
        # block instead of each asking for its own (which would write once per request).
        async with credit.lock:
            if self._spend(credit, cost):
                return None
            block = max(1, dim.limit // self._divisor)
            started = self._mono()
            try:
                got = await self._counters.reserve(
                    dim.scope,
                    key,
                    dim.limit,
                    dim.window_seconds,
                    amount=max(cost - credit.units, block),
                    workspace_id=None if dim.scope.is_global else workspace_id,
                )
            except SharedLimiterUnavailable:
                return None  # fail open: the local limiter has already said yes
            # Conservative: the window ends `seconds_left` after the database answered, which is
            # no earlier than when we asked.
            credit.deadline = started + got.seconds_left
            credit.units += got.granted
            # Spend straight away: the grant belongs to the window the database named, even if
            # that window ends a millisecond from now. Only what is left over expires with it.
            if credit.units >= cost:
                credit.units -= cost
                return None
            return max(got.seconds_left, 0.001)

    def _spend(self, credit: "_Credit", cost: int) -> bool:
        if self._mono() >= credit.deadline:
            credit.units = 0  # credit never outlives the window it was granted in
        if credit.units >= cost:
            credit.units -= cost
            return True
        return False

    def _remember(self, slot: tuple[Scope, uuid.UUID, str]) -> _Credit:
        if len(self._credit) >= self._max_keys:
            now = self._mono()
            self._credit = {k: v for k, v in self._credit.items() if v.deadline > now}
            if len(self._credit) >= self._max_keys:
                for stale in list(self._credit)[: max(1, self._max_keys // 10)]:
                    del self._credit[stale]
        credit = self._credit[slot] = _Credit(0, 0.0)
        return credit


def window_limit(per_second: float, burst: int, window_seconds: int) -> int:
    """The per-window budget for a rate/burst pair: the sustained rate over the window, never
    less than one burst (so a request the local bucket admits can always fit)."""
    return max(int(per_second * window_seconds + 0.999999), burst)

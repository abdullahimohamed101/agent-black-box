"""Per-project token buckets (spec §71.3, FR-ING-007): events/s and bytes/s, with bursts.

In-process only, so N API processes allow N times the rate; a shared limiter is a Phase 19 item
(plan R5). The clock is injected so behaviour is testable without sleeping.
"""

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


class RateLimiter(Protocol):
    def acquire(self, key: str, *, events: int, bytes_: int) -> float | None:
        """Take capacity, or return how many seconds to wait. Never partially consumes."""
        ...


@dataclass
class _Bucket:
    tokens: float
    updated: float


class InMemoryRateLimiter:
    def __init__(
        self,
        *,
        events_per_second: float,
        burst_events: int,
        bytes_per_second: float,
        burst_bytes: int,
        monotonic: Callable[[], float] = time.monotonic,
        max_keys: int = 10_000,
    ) -> None:
        self._rates = (events_per_second, bytes_per_second)
        self._bursts = (float(burst_events), float(burst_bytes))
        self._monotonic = monotonic
        self._max_keys = max_keys
        self._buckets: dict[str, tuple[_Bucket, _Bucket]] = {}

    def acquire(self, key: str, *, events: int, bytes_: int) -> float | None:
        now = self._monotonic()
        pair = self._buckets.get(key)
        if pair is None:
            self._evict_if_full(now)
            pair = (_Bucket(self._bursts[0], now), _Bucket(self._bursts[1], now))
            self._buckets[key] = pair
        costs = (float(events), float(bytes_))
        waits: list[float] = []
        for bucket, rate, burst, cost in zip(pair, self._rates, self._bursts, costs, strict=True):
            bucket.tokens = min(burst, bucket.tokens + (now - bucket.updated) * rate)
            bucket.updated = now
            if cost > bucket.tokens:
                waits.append((cost - bucket.tokens) / rate)
        if waits:
            return max(waits)
        for bucket, cost in zip(pair, costs, strict=True):
            bucket.tokens -= cost
        return None

    def _evict_if_full(self, now: float) -> None:
        """Bound memory: drop the buckets that have been idle longest."""
        if len(self._buckets) < self._max_keys:
            return
        idle = sorted(self._buckets.items(), key=lambda kv: kv[1][0].updated)
        for key, _ in idle[: max(1, self._max_keys // 10)]:
            del self._buckets[key]


def retry_after_seconds(wait: float) -> int:
    return max(1, math.ceil(wait))

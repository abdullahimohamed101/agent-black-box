"""Bounds on concurrent live streams (ADR-022, D5).

Per API process; a shared limiter is Phase 19 (KI-017).
"""

import threading
from collections import Counter

from abb_api.core.errors import AppError, ErrorCategory


def stream_limit(scope: str) -> AppError:
    return AppError(
        "STREAM_LIMIT",
        f"Too many open streams ({scope}). Close one or retry shortly.",
        category=ErrorCategory.RATE_LIMIT,
        status_code=429,
        retryable=True,
        details={"scope": scope, "retry_after_seconds": 5},
        headers={"Retry-After": "5"},
    )


class StreamLease:
    """One open stream's slot. Releasing twice is harmless."""

    def __init__(self, limiter: "StreamLimiter", key_id: str) -> None:
        self._limiter = limiter
        self._key_id = key_id
        self._released = False

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._limiter.release(self._key_id)


class StreamLimiter:
    def __init__(self, max_total: int, max_per_key: int) -> None:
        self._max_total = max_total
        self._max_per_key = max_per_key
        self._per_key: Counter[str] = Counter()
        self._lock = threading.Lock()

    @property
    def open_streams(self) -> int:
        with self._lock:
            return sum(self._per_key.values())

    def acquire(self, key_id: str) -> StreamLease:
        with self._lock:
            if sum(self._per_key.values()) >= self._max_total:
                raise stream_limit("server")
            if self._per_key[key_id] >= self._max_per_key:
                raise stream_limit("key")
            self._per_key[key_id] += 1
        return StreamLease(self, key_id)

    def release(self, key_id: str) -> None:
        with self._lock:
            self._per_key[key_id] -= 1
            if self._per_key[key_id] <= 0:
                del self._per_key[key_id]

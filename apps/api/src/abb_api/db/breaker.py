"""A circuit breaker on database connectivity (Phase 19c step 3, ADR-169).

When the database is down or the pool cannot hand out connections, every request would otherwise
wait for the connect timeout or the pool timeout (3 to 5 seconds) before answering 503, and the
waiting requests pile up on the event loop. After `threshold` consecutive checkout failures within
`window` seconds the breaker opens: for `open_seconds` a checkout fails at once with `BreakerOpen`
(mapped to 503 DEPENDENCY_UNAVAILABLE with `Retry-After`), before the pool is touched. Then one
probe checkout is allowed (half-open): its success closes the breaker, its failure reopens it.

What counts: only a failure of the checkout itself (``Pool.connect``: a new connection, the
pre-ping, the wait for a free one). A slow query, a statement timeout, a constraint violation or
any other error after a connection was handed out is not a failure of connectivity and never
counts; a success while closed resets the count. The breaker is per process (each API process
learns about an outage from its own traffic, within `threshold` requests) and in memory only.

Single-threaded by design (the event loop), with a lock anyway so a metrics thread can read it.
"""

import math
import threading
import time
from collections.abc import Callable
from typing import Literal

from abb_api.obs.catalogue import METRICS, Catalogue

State = Literal["closed", "half_open", "open"]
_STATE_VALUE = {"closed": 0.0, "half_open": 1.0, "open": 2.0}


class BreakerOpen(Exception):
    """The breaker is shedding load; carries how long until the next probe (never any detail)."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("database circuit breaker is open")
        self.retry_after_seconds = retry_after_seconds


class DbBreaker:
    def __init__(
        self,
        *,
        threshold: int = 5,
        open_seconds: float = 5.0,
        window_seconds: float = 30.0,
        enabled: bool = True,
        clock: Callable[[], float] = time.monotonic,
        metrics: Catalogue = METRICS,
    ) -> None:
        self._threshold = threshold
        self._open_seconds = open_seconds
        self._window = window_seconds
        self.enabled = enabled
        self._clock = clock
        self._metrics = metrics
        self._lock = threading.Lock()
        self._state: State = "closed"
        self._failures = 0
        self._first_failure = 0.0
        self._since = 0.0  # when it opened, or when the current probe was let through
        self.trips = 0  # closed/half-open -> open transitions, for tests
        metrics.db_breaker_state.set(0)

    @property
    def state(self) -> State:
        """What a caller would meet now: an open breaker past its time is ready for a probe."""
        with self._lock:
            if self._state == "open" and self._clock() - self._since >= self._open_seconds:
                return "half_open"
            return self._state

    @property
    def shedding(self) -> bool:
        return self.state == "open"

    def _set(self, state: State) -> None:
        self._state = state
        self._metrics.db_breaker_state.set(_STATE_VALUE[state])

    def before_connect(self) -> None:
        """Raises `BreakerOpen` when a checkout must not be attempted now. Cheap and memory-only."""
        if not self.enabled:
            return
        with self._lock:
            if self._state == "closed":
                return
            now = self._clock()
            elapsed = now - self._since
            if elapsed < self._open_seconds:
                # Open, or a probe still in flight: shed. A probe that never reports back (its
                # request was cancelled) is replaced after one more open period.
                raise BreakerOpen(max(1, math.ceil(self._open_seconds - elapsed)))
            self._set("half_open")
            self._since = now  # this caller is the probe

    def record_success(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            if self._state == "closed":
                self._failures = 0
            elif self._state == "half_open":
                self._failures = 0
                self._set("closed")
            # open: a straggler that started before it opened; only the probe may close it

    def record_failure(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            now = self._clock()
            if self._state == "half_open":
                self._open(now)
            elif self._state == "closed":
                if self._failures == 0 or now - self._first_failure > self._window:
                    self._failures, self._first_failure = 0, now
                self._failures += 1
                if self._failures >= self._threshold:
                    self._open(now)
            # open: a straggler; it must not extend the period

    def _open(self, now: float) -> None:
        self._set("open")
        self._since = now
        self._failures = 0
        self.trips += 1
        self._metrics.db_breaker_trips_total.inc()

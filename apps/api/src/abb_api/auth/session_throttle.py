"""Failed *session* authentication is throttled, valid sessions never are (ADR-169, KI-176).

A bad session cookie costs one indexed lookup. The tokens are 256 random bits, so guessing is not
the risk; the load of an anonymous flood is. This mirrors the failed-API-key throttle (ADR-153)
with two shared counters (ADR-152), both counting *failures* in fixed windows:

* per client address, only when `ABB_TRUST_PROXY=1` gave a trustworthy one;
* one global ceiling, which bounds the database load of a distributed flood.

There is no per-credential counter: a key id is a public, enumerable name, a session token is 256
random bits and every junk cookie is a new one, so a per-token budget would bound nothing. What
bounds repeated use of *one* cookie is the known-invalid cache below (one lookup per token per
`session_fail_negative_seconds` per process).

A throttled request is refused **before** the lookup, from memory, with `429 SESSION_THROTTLED`
and `Retry-After`. That is the one deliberate difference from an unknown cookie (401
`SESSION_INVALID`, which makes the web app sign the person out): a refusal that did not look at
the cookie must never read as "your session is dead". Both bodies are fixed and identical for
every throttled caller, and say nothing about the cookie, so no validity is revealed.

A closed window must not lock out a person whose session is valid but unknown to this process
(first request after a restart, a session created on another API process). Three small bounded
memory-only helpers, the same lesson as the 19a P1 for API keys:

* known valid: cookies (by a digest of the whole value) that authenticated here within
  `session_fail_known_valid_seconds` skip the *gate*. Full authentication still runs every time,
  so a revoked session stops working at once.
* active session hints (`ActiveKeyIds` reused): while the address or global window is closed, a
  cookie whose stored-hash prefix is in this background-refreshed set still reaches its single
  lookup. A junk cookie is not in the set and is refused from memory. The set holds 16-byte hash
  prefixes only (no token, no user, no workspace), is capped, and is a hint, never a substitute for
  authentication. A session created by this process is added at once; one created elsewhere is
  covered by the next refresh (`session_fail_active_refresh_seconds`), so during a flood a
  brand-new session on another process may be refused with a 429 for up to that long (it retries).
  When the set is not loaded (a startup race, a database outage) the gate behaves as without it.
* known invalid: cookies that failed within `session_fail_negative_seconds` are refused with the
  uniform 401 without a lookup (still counted as failures), so one stale cookie polled by a browser
  costs one lookup per period.

The throttle fails open: a counter problem is logged and the request carries on. It writes no
audit row: a failed session names no workspace, and an unauthenticated flood must not be able to
write to any tenant's log; there is one log line per scope per window per process.
"""

import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from abb_api.auth.active_keys import ActiveKeyIds
from abb_api.auth.service import hash_session_token, is_session_token, session_handle
from abb_api.auth.throttle import _Expiring, _LocalWindow
from abb_api.core.client_address import address_counter_key
from abb_api.core.config import Settings
from abb_api.core.errors import AppError, ErrorCategory
from abb_api.ingestion.ratelimit import retry_after_seconds
from abb_api.limits import Dimension, GuardedLimiter, Scope, SharedCounters
from abb_api.obs.catalogue import METRICS, Catalogue

logger = logging.getLogger(__name__)

_GLOBAL_KEY = "all"
_KIND = {Scope.SESSION_FAIL_ADDRESS: "address", Scope.SESSION_FAIL_GLOBAL: "global"}


def session_throttled(wait: float) -> AppError:
    """One body for every throttled request, whatever scope tripped and whether or not the cookie
    is any good: the caller learns that it is being refused for now, nothing about the cookie."""
    seconds = retry_after_seconds(wait)
    return AppError(
        "SESSION_THROTTLED",
        "Too many failed session lookups. Retry later.",
        category=ErrorCategory.RATE_LIMIT,
        status_code=429,
        retryable=True,
        details={"retry_after_seconds": seconds},
        headers={"Retry-After": str(seconds)},
    )


@dataclass(frozen=True)
class SessionAttempt:
    """A well-formed cookie presented to the API; never holds the cookie value itself."""

    digest: str  # sha256 of the whole cookie value, memory only
    handle: str  # the stored-hash prefix, for the active set
    address: str | None  # counter key of the trusted client address (a digest), or None


class SessionThrottle:
    def __init__(
        self,
        settings: Settings,
        counters: SharedCounters | None,
        *,
        active: ActiveKeyIds | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        metrics: Catalogue = METRICS,
    ) -> None:
        self._metrics = metrics
        self._enabled = settings.session_fail_enabled
        self._window = settings.session_fail_window_seconds
        self._negative_ttl = settings.session_fail_negative_seconds
        self._valid_ttl = settings.session_fail_known_valid_seconds
        self.active = active
        size = settings.session_fail_cache_entries
        self._known_invalid = _Expiring(size, monotonic)
        self._known_valid = _Expiring(size, monotonic)
        self._blocked = _Expiring(size, monotonic)  # "scope:key" -> window still closed
        self._limiters = {
            Scope.SESSION_FAIL_ADDRESS: self._limiter(
                settings,
                counters,
                wall,
                Scope.SESSION_FAIL_ADDRESS,
                settings.session_fail_per_address,
                size,
            ),
            Scope.SESSION_FAIL_GLOBAL: self._limiter(
                settings, counters, wall, Scope.SESSION_FAIL_GLOBAL, settings.session_fail_global, 4
            ),
        }

    @staticmethod
    def _limiter(
        settings: Settings,
        counters: SharedCounters | None,
        wall: Callable[[], float],
        scope: Scope,
        limit: int,
        keys: int,
    ) -> GuardedLimiter:
        window = settings.session_fail_window_seconds
        # The local budget is the fallback when the database is down: the same limit per window.
        return GuardedLimiter(
            _LocalWindow(limit, window, keys, wall),
            counters,
            [Dimension(scope, "events", limit, window)],
            block_divisor=settings.limits_block_divisor,
        )

    # ------------------------------------------------------------------ the gate

    def admit(self, token: str | None, address: str | None) -> SessionAttempt | None:
        """Memory-only. Raises 429 when a window is closed for this request; returns None for
        anything that is not a well-formed cookie (refused without a lookup, not counted)."""
        if not self._enabled or not token or not is_session_token(token):
            return None
        attempt = SessionAttempt(
            digest=hashlib.sha256(token.encode("ascii")).hexdigest(),
            handle=session_handle(hash_session_token(token)),
            address=address_counter_key(address) if address else None,
        )
        if self._known_valid.get(attempt.digest)[0]:
            return attempt  # authenticated here recently: the gate is not consulted
        closed = [
            (self._blocked.remaining(f"{scope.value}:{key}"), scope)
            for scope, key in self._targets(attempt)
        ]
        wait, widest = max(closed, key=lambda item: item[0], default=(0.0, None))
        if wait > 0 and widest is not None and not self._is_active(attempt.handle):
            self._metrics.session_throttle_blocks_total.inc(_KIND[widest])
            raise session_throttled(wait)
        # Open, or closed while this is a session that exists: on to full authentication.
        return attempt

    def _is_active(self, handle: str) -> bool:
        keys = self.active
        if keys is None:
            return False
        if not keys.loaded:
            keys.nudge()
            return False
        return handle in keys

    def session_created(self, token_hash: bytes) -> None:
        """A session was committed by this process: it must not wait for the next refresh."""
        if self.active is not None:
            self.active.add(session_handle(token_hash))

    def known_invalid(self, attempt: SessionAttempt) -> bool:
        return self._known_invalid.get(attempt.digest)[0]

    def succeeded(self, attempt: SessionAttempt) -> None:
        self._known_valid.put(attempt.digest, self._valid_ttl)

    # ------------------------------------------------------------------ failures

    async def failed(self, attempt: SessionAttempt) -> AppError | None:
        """Count one failure against every scope that applies. Returns the 429 to answer with
        when this failure used up a budget, else None (the caller answers its own 401)."""
        self._known_valid.discard(attempt.digest)
        if not self._known_invalid.get(attempt.digest)[0]:
            self._known_invalid.put(attempt.digest, self._negative_ttl)
        try:
            tripped: dict[Scope, float] = {}
            fresh: list[Scope] = []
            for scope, key in self._targets(attempt):
                wait = await self._limiters[scope].acquire(key, events=1)
                if wait is None:
                    continue
                tripped[scope] = wait
                if self._blocked.put(f"{scope.value}:{key}", max(wait, 0.001)):
                    fresh.append(scope)
            if not tripped:
                return None
            self._metrics.session_throttle_blocks_total.inc(
                _KIND[max(tripped, key=tripped.__getitem__)]
            )
            if fresh:  # once per scope per window per process; never an address or a count
                logger.warning(
                    "session lookups throttled",
                    extra={"scopes": sorted(_KIND[s] for s in fresh)},
                )
            return session_throttled(max(tripped.values()))
        except Exception as exc:  # the throttle must never turn a 401 into a 500
            logger.warning("session throttle failed open", extra={"error_type": type(exc).__name__})
            return None

    @staticmethod
    def _targets(attempt: SessionAttempt) -> list[tuple[Scope, str]]:
        targets = [(Scope.SESSION_FAIL_GLOBAL, _GLOBAL_KEY)]
        if attempt.address is not None:
            targets.insert(0, (Scope.SESSION_FAIL_ADDRESS, attempt.address))
        return targets

"""Failed API-key authentication is throttled, valid traffic never is (ADR-153, KI-019).

Three shared counters (ADR-152), all counting *failures* in fixed windows:

* per client address, only when `ABB_TRUST_PROXY=1` gave a trustworthy one;
* per presented key id, so one key (existing or not) cannot be hammered;
* one global ceiling, which is what bounds the database load of a distributed flood.

Exceeding any of them answers `429 AUTH_THROTTLED` with `Retry-After`. A request that is already
throttled is refused from memory: no key lookup, no counter write, no database at all. The only
hashing is one SHA-256 of the presented token (to recognise it in the two caches below).

Because a throttled request is refused before the key is looked up, the throttle alone would lock
out valid keys too. Three small bounded memory-only helpers keep that from happening:

* known valid: tokens (keyed by a digest of the *whole* presented token) that authenticated in this
  process within `auth_fail_known_valid_seconds` skip the throttle *gate*. They still go through
  full authentication every time, so a revoked key stops working at once. A guessed key id with the
  wrong secret does not match it.
* active key ids (`active_keys.py`): while the *address* or *global* window is closed, a request
  for a key id that really exists still reaches full authentication, so a key this process has
  never seen is not locked out by a flood. The key id's own credential window still applies, which
  bounds the lookups to `auth_fail_per_credential` per real key id per window. A random key id is
  refused from memory.
* known invalid: tokens that failed within `auth_fail_negative_seconds` are refused without a
  database lookup. Successes are never put here, and a key created later has a different token, so
  it is unaffected by anything cached about earlier guesses.

The throttle fails open: a counter or audit problem is logged and the request carries on.
"""

import hashlib
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.audit.repository import AuditEntry, record_audit
from abb_api.auth.active_keys import ActiveKeyIds
from abb_api.auth.keys import parse_key
from abb_api.core.client_address import address_counter_key
from abb_api.core.config import Settings
from abb_api.core.errors import AppError, ErrorCategory
from abb_api.ingestion.ratelimit import retry_after_seconds
from abb_api.limits import Dimension, GuardedLimiter, Scope, SharedCounters
from abb_api.obs.catalogue import METRICS, Catalogue

logger = logging.getLogger(__name__)

AUDIT_ACTION = "auth.throttled"
AUDIT_ACTOR = "system:auth-throttle"
_GLOBAL_KEY = "all"
_KIND = {
    Scope.AUTH_FAIL_ADDRESS: "address",
    Scope.AUTH_FAIL_CREDENTIAL: "credential",
    Scope.AUTH_FAIL_GLOBAL: "global",
}


def throttled(wait: float) -> AppError:
    """One body for every throttled request, whatever scope tripped and whether or not the key
    exists: the caller learns that it is being refused for now, nothing about the credential."""
    seconds = retry_after_seconds(wait)
    return AppError(
        "AUTH_THROTTLED",
        "Too many failed authentication attempts. Retry later.",
        category=ErrorCategory.RATE_LIMIT,
        status_code=429,
        retryable=True,
        details={"retry_after_seconds": seconds},
        headers={"Retry-After": str(seconds)},
    )


@dataclass(frozen=True)
class Attempt:
    """A well-formed key presented to the API; never holds the secret itself."""

    digest: str  # sha256 of the whole token, memory only
    key_id: str
    address: str | None  # counter key of the trusted client address (a digest), or None


class _Expiring:
    """A bounded map of key -> (deadline, value). Oldest entries go first when full."""

    def __init__(self, max_entries: int, clock: Callable[[], float]) -> None:
        self._max = max_entries
        self._clock = clock
        self._items: dict[str, tuple[float, uuid.UUID | None]] = {}

    def get(self, key: str) -> tuple[bool, uuid.UUID | None]:
        found = self._items.get(key)
        if found is None:
            return False, None
        if found[0] <= self._clock():
            del self._items[key]
            return False, None
        return True, found[1]

    def put(self, key: str, ttl: float, value: uuid.UUID | None = None) -> bool:
        """True when the key was not live before."""
        if ttl <= 0:
            return False
        now = self._clock()
        previous = self._items.get(key)
        fresh = previous is None or previous[0] <= now
        if fresh and len(self._items) >= self._max:
            self._items = {k: v for k, v in self._items.items() if v[0] > now}
            if len(self._items) >= self._max:
                for stale in list(self._items)[: max(1, self._max // 10)]:
                    del self._items[stale]
        self._items[key] = (now + ttl, value)
        return fresh

    def discard(self, key: str) -> None:
        self._items.pop(key, None)

    def remaining(self, key: str) -> float:
        found = self._items.get(key)
        return max(0.0, found[0] - self._clock()) if found else 0.0

    def __len__(self) -> int:
        return len(self._items)


class _LocalWindow:
    """The per-process budget behind the shared one: the same fixed windows, aligned to the wall
    clock like the database's, so a refusal reports the time left in the window. (A token bucket
    here would say "retry in 0.1 s" once empty and the gate would reopen ten times a second.)
    It is what bounds a key id or an address while the database is down (ADR-152 fail-open)."""

    def __init__(
        self, limit: int, window: int, max_keys: int, wall: Callable[[], float] = time.time
    ) -> None:
        self._limit = limit
        self._window = window
        self._max = max_keys
        self._wall = wall
        self._counts: dict[str, tuple[float, int]] = {}

    def acquire(self, key: str, *, events: int, bytes_: int) -> float | None:
        now = self._wall()
        start = now - now % self._window
        seen = self._counts.get(key)
        used = seen[1] if seen is not None and seen[0] == start else 0
        if used + events > self._limit:
            return max(start + self._window - now, 0.001)
        if seen is None and len(self._counts) >= self._max:
            live = {k: v for k, v in self._counts.items() if v[0] == start}
            self._counts = live
            if len(self._counts) >= self._max:
                for stale in list(self._counts)[: max(1, self._max // 10)]:
                    del self._counts[stale]
        self._counts[key] = (start, used + events)
        return None


class AuthThrottle:
    def __init__(
        self,
        settings: Settings,
        counters: SharedCounters | None,
        engine: AsyncEngine,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        active_keys: ActiveKeyIds | None = None,
        metrics: Catalogue = METRICS,
    ) -> None:
        self._metrics = metrics
        self._wall = wall
        self._mono = monotonic
        self._malformed_count = 0
        self._malformed_since = float("-inf")
        self.active_keys = active_keys
        self._enabled = settings.auth_fail_enabled
        self._window = settings.auth_fail_window_seconds
        self._engine = engine
        self._counters = counters
        self._negative_ttl = settings.auth_fail_negative_seconds
        self._valid_ttl = settings.auth_fail_known_valid_seconds
        size = settings.auth_fail_cache_entries
        self._known_invalid = _Expiring(size, monotonic)
        self._known_valid = _Expiring(size, monotonic)
        self._blocked = _Expiring(size, monotonic)  # "scope:key" -> window still closed
        self._limiters = {
            Scope.AUTH_FAIL_ADDRESS: self._limiter(
                settings, Scope.AUTH_FAIL_ADDRESS, settings.auth_fail_per_address, size
            ),
            Scope.AUTH_FAIL_CREDENTIAL: self._limiter(
                settings, Scope.AUTH_FAIL_CREDENTIAL, settings.auth_fail_per_credential, size
            ),
            Scope.AUTH_FAIL_GLOBAL: self._limiter(
                settings, Scope.AUTH_FAIL_GLOBAL, settings.auth_fail_global, 4
            ),
        }

    def _limiter(self, settings: Settings, scope: Scope, limit: int, keys: int) -> GuardedLimiter:
        window = settings.auth_fail_window_seconds
        # The local budget is the fallback when the database is down: the same limit per window.
        # With the database up the shared counter is the authority (ADR-152).
        local = _LocalWindow(limit, window, keys, self._wall)
        return GuardedLimiter(
            local,
            self._counters,
            [Dimension(scope, "events", limit, window)],
            block_divisor=settings.limits_block_divisor,
        )

    # ------------------------------------------------------------------ the gate

    def admit(self, token: str | None, address: str | None) -> Attempt | None:
        """Memory-only. Raises 429 when a window is closed for this request; returns None for
        anything that is not a well-formed key (those are cheap to refuse and are not counted)."""
        if not self._enabled or not token:
            return None
        parsed = parse_key(token)
        if parsed is None:
            return None
        digest = hashlib.sha256(token.encode("ascii", "replace")).hexdigest()
        attempt = Attempt(
            digest=digest,
            key_id=parsed.key_id,
            address=address_counter_key(address) if address else None,
        )
        if self._known_valid.get(digest)[0]:
            return attempt  # authenticated here recently: the gate is not consulted
        own = self._blocked.remaining(f"{Scope.AUTH_FAIL_CREDENTIAL.value}:{attempt.key_id}")
        if own > 0:
            self._metrics.auth_throttle_blocks_total.inc("credential")
            raise throttled(own)  # this key id's own budget is spent: refused from memory
        closed_scopes = [
            (self._blocked.remaining(f"{scope.value}:{key}"), scope)
            for scope, key in self._targets(attempt)
            if scope is not Scope.AUTH_FAIL_CREDENTIAL
        ]
        shared, widest = max(closed_scopes, key=lambda item: item[0], default=(0.0, None))
        if shared > 0 and widest is not None and not self._is_active(attempt.key_id):
            self._metrics.auth_throttle_blocks_total.inc(_KIND[widest])
            raise throttled(shared)
        # Open, or closed only by an address / the installation while this is a real key id: on to
        # full authentication, which costs at most `auth_fail_per_credential` lookups for it.
        return attempt

    def malformed_to_log(self) -> int | None:
        """Missing and malformed keys are not counted by the throttle, so a flood of them must not
        become a log line each: one line per window, carrying how many there were. Returns the
        count to log now, or None while the window is still collecting."""
        self._malformed_count += 1
        now = self._mono()
        if now - self._malformed_since < self._window:
            return None
        count, self._malformed_count = self._malformed_count, 0
        self._malformed_since = now
        return count

    def _is_active(self, key_id: str) -> bool:
        keys = self.active_keys
        if keys is None:
            return False
        if not keys.loaded:
            keys.nudge()
            return False
        return key_id in keys

    def key_created(self, key_id: str) -> None:
        """A key was committed by this process: it must not wait for the next refresh."""
        if self.active_keys is not None:
            self.active_keys.add(key_id)

    def known_invalid(self, attempt: Attempt) -> bool:
        return self._known_invalid.get(attempt.digest)[0]

    def succeeded(self, attempt: Attempt) -> None:
        self._known_valid.put(attempt.digest, self._valid_ttl)

    # ------------------------------------------------------------------ failures

    async def failed(self, attempt: Attempt, workspace_id: uuid.UUID | None) -> AppError | None:
        """Count one failure against every scope that applies. Returns the 429 to answer with
        when this failure used up a budget, else None (the caller answers its own 401)."""
        self._known_valid.discard(attempt.digest)
        known, cached_workspace = self._known_invalid.get(attempt.digest)
        if not known:
            self._known_invalid.put(attempt.digest, self._negative_ttl, workspace_id)
        workspace_id = workspace_id or cached_workspace
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
            self._metrics.auth_throttle_blocks_total.inc(
                _KIND[max(tripped, key=tripped.__getitem__)]
            )
            if fresh:
                await self._note(fresh, attempt.key_id, workspace_id)
            return throttled(max(tripped.values()))
        except Exception as exc:  # the throttle must never turn a 401 into a 500
            logger.warning("auth throttle failed open", extra={"error_class": type(exc).__name__})
            return None

    def _targets(self, attempt: Attempt) -> list[tuple[Scope, str]]:
        targets = [
            (Scope.AUTH_FAIL_CREDENTIAL, attempt.key_id),
            (Scope.AUTH_FAIL_GLOBAL, _GLOBAL_KEY),
        ]
        if attempt.address is not None:
            targets.insert(0, (Scope.AUTH_FAIL_ADDRESS, attempt.address))
        return targets

    async def _note(self, scopes: list[Scope], key_id: str, workspace_id: uuid.UUID | None) -> None:
        """One log line per scope per window per process, and at most one audit row per
        workspace per window across processes. Never an address, a secret or a count."""
        names = sorted(s.value.removeprefix("auth_fail_") for s in scopes)
        logger.warning("authentication throttled", extra={"scopes": names})
        if workspace_id is None or self._counters is None:
            return  # no key of ours was involved (or no shared cap to hold the audit to one row)
        try:
            decision = await self._counters.check_and_increment(
                Scope.AUTH_THROTTLE_AUDIT, "throttled", 1, self._window, workspace_id=workspace_id
            )
        except Exception:
            return  # cannot prove the cap, so write nothing rather than risk a row per failure
        if not decision.allowed:
            return
        await record_audit(
            self._engine,
            workspace_id,
            AuditEntry(
                actor_kind="system",
                actor_id=AUDIT_ACTOR,
                action=AUDIT_ACTION,
                outcome="denied",
                resource_kind="api_key",
                resource_id=key_id,
                details={"scopes": names, "window_seconds": self._window},
            ),
        )

"""The ids of the API keys that are currently usable, kept in memory (ADR-153 point 3).

The failed-auth throttle refuses a request from memory while one of its windows is closed. That
would lock out a valid key this process has never seen (a new key, a restart, first use on another
process), so while the *address* or *global* window is closed a request whose key id is in this set
is let through to full authentication. Only the credential window of that key id still gates it,
which bounds the lookups to `auth_fail_per_credential` per real key id per window: bounded by the
installation's data, not by the attacker. A random key id is not in the set and is refused from
memory with no lookup.

The set holds public key ids only: no secret, no hash, no workspace. It is a hint about which
requests may reach authentication, never a substitute for it, so a revoked key that is still in the
set is rejected by the normal lookup and drops out at the next refresh.

Loading: one bounded query at startup and again every `refresh_seconds` in a background task, never
on the request path. A failed or slow refresh keeps the previous set. Until the first load succeeds
the set is "not loaded" and the throttle behaves as it did without it, while the task retries
every few seconds. A key created by this process is inserted at once; one created elsewhere (the
CLI, another API process) is picked up by the next refresh.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable

from abb_api.obs.catalogue import METRICS, Catalogue
from abb_api.obs.metrics import Counter, Gauge

logger = logging.getLogger(__name__)

Loader = Callable[[int], Awaitable[Iterable[str]]]

RETRY_SECONDS = 2.0
LOAD_TIMEOUT_SECONDS = 5.0


class ActiveKeyIds:
    def __init__(
        self,
        load: Loader,
        *,
        refresh_seconds: float = 60.0,
        cap: int = 100_000,
        timeout_seconds: float = LOAD_TIMEOUT_SECONDS,
        retry_seconds: float = RETRY_SECONDS,
        metrics: Catalogue = METRICS,
        size_gauge: Gauge | None = None,
        failure_counter: Counter | None = None,
    ) -> None:
        # The same mechanism holds the active session hints (auth/session_throttle.py), which
        # report to their own series.
        self._size = size_gauge or metrics.auth_active_keys
        self._failed = failure_counter or metrics.auth_active_key_refresh_failures_total
        self._load = load
        self._refresh = refresh_seconds
        self._cap = cap
        self._timeout = timeout_seconds
        self._retry = retry_seconds
        self._ids: set[str] = set()
        self._loaded = False
        self._added_during_load: set[str] | None = None
        self._capped_logged = False
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self.refreshes = 0  # successful loads, observable by tests
        self.failures = 0

    @property
    def loaded(self) -> bool:
        return self._loaded

    def __contains__(self, key_id: str) -> bool:
        return key_id in self._ids

    def __len__(self) -> int:
        return len(self._ids)

    def add(self, key_id: str) -> None:
        """A key was created (and committed) by this process: usable at once."""
        if len(self._ids) < self._cap:
            self._ids.add(key_id)
            self._size.set(len(self._ids))
        if self._added_during_load is not None:
            self._added_during_load.add(key_id)

    async def refresh(self) -> bool:
        """Replace the set from the database. False (previous set kept) on any failure."""
        self._added_during_load = set()
        try:
            async with asyncio.timeout(self._timeout):
                got = list(await self._load(self._cap + 1))
        except Exception as exc:
            self.failures += 1
            self._failed.inc()
            # Class name only; the loop retries, and the old set (if any) keeps serving.
            logger.warning(
                "active key ids not refreshed", extra={"error_class": type(exc).__name__}
            )
            return False
        finally:
            added, self._added_during_load = self._added_during_load or set(), None
        if len(got) > self._cap:
            got = got[: self._cap]
            if not self._capped_logged:
                self._capped_logged = True
                logger.warning(
                    "more active API keys than the cap; keys beyond it behave as before while a "
                    "failed-auth window is closed",
                    extra={"cap": self._cap},
                )
        # Keys created here while the query ran may have missed its snapshot.
        self._ids = set(got) | added
        self._loaded = True
        self.refreshes += 1
        self._size.set(len(self._ids))
        return True

    def nudge(self) -> None:
        """Ask the background task to load now (set never loaded: a startup race or an outage)."""
        if not self._loaded:
            self._wake.set()

    async def start(self) -> None:
        """First load (bounded by the timeout), then keep refreshing in the background."""
        await self.refresh()
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="active-key-ids")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _run(self) -> None:
        while True:
            interval = self._refresh if self._loaded else self._retry
            try:
                await asyncio.wait_for(self._wake.wait(), interval)
            except TimeoutError:
                pass
            await self.refresh()
            if not self._loaded:
                # A floor on retries, so requests nudging an unloaded set cannot become a
                # query per request.
                await asyncio.sleep(self._retry)
            self._wake.clear()

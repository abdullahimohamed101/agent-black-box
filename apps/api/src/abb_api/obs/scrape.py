"""Gauges that are read from the database at scrape time (Phase 19c step 2, ADR-165, ADR-168).

Queue depth, the age of the oldest due job, dead letters, destination states and the retention
heartbeat describe the *database*, not this process. They are sampled here, never on a request path,
and never in a way that can make a scrape fail or hang:

- One bounded sample at most every ``ttl_seconds`` (15 s), whatever the scrape rate. A failed
  attempt counts, so a database outage costs one attempt per window, not one per scrape.
- The sample runs in its own task. A scrape waits for it at most ``wait_seconds`` and otherwise
  serves what was cached, with ``abb_scrape_database_up`` at 0 until a sample succeeds again.
- Every statement runs with a server-side ``statement_timeout`` and the whole sample under an
  ``asyncio`` deadline; every query is bounded by a partial index or a LIMIT (see ADR-168 for the
  plans). No tenant data leaves the database: only counts, ages and fixed words.
- A statement that fails keeps its previous values (the other statements still update), and the
  gauge reports 0 until all of them succeed in the same sample.

The queries run as the runtime role on tables it already reads (the worker's claim query, the
alert delivery tick, the retention API). No migration, no new privilege.
"""

import asyncio
import contextlib
import logging
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.obs.catalogue import RETENTION_MODES, Catalogue

logger = logging.getLogger(__name__)

TTL_SECONDS: Final = 15.0
WAIT_SECONDS: Final = 1.5  # how long one scrape waits for a sample before serving the cache
STATEMENT_TIMEOUT_MS: Final = 2000
DEADLINE_SECONDS: Final = 8.0  # connection acquisition (pool 5 s, connect 3 s) plus the statements
# The pending-job query looks at this many due jobs, oldest first: depth saturates there, the lag
# stays exact for the oldest type (ADR-168).
DUE_JOBS_LOOKAHEAD: Final = 10_000

_DUE_JOBS = text(
    """
    SELECT job_type, count(*) AS n, extract(epoch FROM (now() - min(available_at))) AS lag
    FROM (
        SELECT job_type, available_at FROM outbox_jobs
        WHERE status = 'pending' AND available_at <= now()
        ORDER BY available_at
        LIMIT :lookahead
    ) AS due
    GROUP BY job_type
    """
)
_DEAD_LETTERS = text("SELECT count(*) FROM alert_notifications WHERE status = 'dead'")
_DESTINATIONS = text("SELECT state, count(*) FROM alert_destinations GROUP BY state")
_RETENTION = text(
    """
    SELECT mode, extract(epoch FROM (now() - finished_at))
    FROM retention_reports ORDER BY id DESC LIMIT 1
    """
)


@dataclass(frozen=True)
class _Probe:
    name: str
    statement: Any
    params: dict[str, Any]
    apply: Callable[[Sequence[Any]], None]


class ScrapeSampler:
    def __init__(
        self,
        engine_of: Callable[[], AsyncEngine | None],
        catalogue: Catalogue,
        *,
        ttl_seconds: float = TTL_SECONDS,
        wait_seconds: float = WAIT_SECONDS,
        statement_timeout_ms: int = STATEMENT_TIMEOUT_MS,
        deadline_seconds: float = DEADLINE_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._engine_of = engine_of
        self._cat = catalogue
        self._ttl = ttl_seconds
        self._wait = wait_seconds
        self._timeout_ms = statement_timeout_ms
        self._deadline = deadline_seconds
        self._mono = monotonic
        self._last: float | None = None
        self._task: asyncio.Task[None] | None = None
        self.samples = 0  # attempts started, for tests and the benchmark
        catalogue.retention_seconds_since_last_pass.set(math.nan)
        catalogue.abb_scrape_database_up.set(0)
        self._probes = (
            _Probe("jobs", _DUE_JOBS, {"lookahead": DUE_JOBS_LOOKAHEAD}, self._apply_jobs),
            _Probe("dead_letters", _DEAD_LETTERS, {}, self._apply_dead_letters),
            _Probe("destinations", _DESTINATIONS, {}, self._apply_destinations),
            _Probe("retention", _RETENTION, {}, self._apply_retention),
        )

    async def refresh(self) -> None:
        """Make sure the gauges are at most ``ttl`` old, without ever raising or blocking long."""
        now = self._mono()
        if self._task is None and (self._last is None or now - self._last >= self._ttl):
            self._last = now
            self.samples += 1
            self._task = asyncio.create_task(self._sample(), name="abb-scrape-sample")
        task = self._task
        if task is None:
            return
        with contextlib.suppress(Exception):  # TimeoutError: serve the cache, the task goes on
            await asyncio.wait_for(asyncio.shield(task), self._wait)

    async def _sample(self) -> None:
        up = False
        try:
            up = await self._run()
        except Exception as exc:  # never propagates: a scrape must not fail on the database
            logger.warning("scrape-time sample failed", extra={"error_type": type(exc).__name__})
        finally:
            self._cat.abb_scrape_database_up.set(1 if up else 0)
            self._task = None

    async def _run(self) -> bool:
        engine = self._engine_of()
        if engine is None:
            return False
        ok = True
        async with asyncio.timeout(self._deadline):
            async with engine.connect() as conn:
                for probe in self._probes:
                    try:
                        async with conn.begin():
                            await conn.execute(
                                text("SELECT set_config('statement_timeout', :ms, true)"),
                                {"ms": str(self._timeout_ms)},
                            )
                            rows = (await conn.execute(probe.statement, probe.params)).all()
                        probe.apply(rows)
                    except Exception as exc:  # one slow statement keeps its old values
                        ok = False
                        logger.warning(
                            "scrape-time probe failed",
                            extra={"probe": probe.name, "error_type": type(exc).__name__},
                        )
        return ok

    # ------------------------------------------------------------------ appliers

    def _apply_jobs(self, rows: Sequence[Any]) -> None:
        self._cat.worker_jobs_pending.replace({(str(r[0]),): float(r[1]) for r in rows})
        self._cat.worker_job_lag_seconds.replace(
            {(str(r[0]),): max(float(r[2]), 0.0) for r in rows}
        )

    def _apply_dead_letters(self, rows: Sequence[Any]) -> None:
        self._cat.alert_dead_letters.set(float(rows[0][0]) if rows else 0.0)

    def _apply_destinations(self, rows: Sequence[Any]) -> None:
        self._cat.alert_destinations.replace({(str(r[0]),): float(r[1]) for r in rows})

    def _apply_retention(self, rows: Sequence[Any]) -> None:
        if not rows:
            self._cat.retention_mode.replace({})
            self._cat.retention_seconds_since_last_pass.set(math.nan)
            return
        mode, age = str(rows[0][0]), float(rows[0][1])
        self._cat.retention_mode.replace({(m,): 1.0 if m == mode else 0.0 for m in RETENTION_MODES})
        self._cat.retention_seconds_since_last_pass.set(max(age, 0.0))

"""The background worker (spec §72): claim jobs, run handlers, retry, dead-letter.

A handler runs in the same transaction that marks its job done, so its writes and the job's
completion commit or roll back together. If the lease was lost meanwhile (the job was reclaimed
by another worker) the transaction is rolled back and the job is left to its new owner.
Delivery is at-least-once: handlers must be idempotent.
"""

import asyncio
import contextlib
import logging
import os
import signal
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from abb_api.clock import Clock
from abb_api.core.config import Settings, get_settings
from abb_api.core.logging import configure_logging
from abb_api.db import create_engine
from abb_api.jobs.handlers import HANDLERS
from abb_api.jobs.outbox import Job, JobQueue, backoff_seconds

logger = logging.getLogger(__name__)

Handler = Callable[[Job, AsyncConnection], Awaitable[None]]


class LeaseLostError(Exception):
    """The job was taken over while this worker was processing it."""


class Worker:
    def __init__(
        self,
        engine: AsyncEngine,
        handlers: Mapping[str, Handler],
        settings: Settings,
        *,
        clock: Clock | None = None,
        owner: str | None = None,
    ) -> None:
        self._engine = engine
        self._handlers = handlers
        self._settings = settings
        # None: the database clock schedules everything (see JobQueue.claim). Tests inject one.
        self._clock = clock
        self.owner = owner or f"{os.uname().nodename}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

    def _now(self) -> datetime | None:
        return self._clock() if self._clock is not None else None

    async def run_once(self) -> int:
        """Claim and process one batch of jobs; returns how many were claimed."""
        async with self._engine.begin() as conn:
            jobs = await JobQueue(conn).claim(
                owner=self.owner,
                limit=self._settings.worker_batch_size,
                lease=timedelta(seconds=self._settings.worker_lease_seconds),
                now=self._now(),
                max_attempts=self._settings.worker_max_attempts,
            )
        for job in jobs:
            await self._process(job)
        return len(jobs)

    async def run_forever(self, stop: asyncio.Event) -> None:
        logger.info("worker started", extra={"owner": self.owner})
        while not stop.is_set():
            try:
                processed = await self.run_once()
            except Exception:
                logger.exception("worker loop error")  # e.g. the database restarted; keep going
                processed = 0
            if processed == 0:
                try:
                    await asyncio.wait_for(stop.wait(), self._settings.worker_poll_interval_seconds)
                except TimeoutError:
                    pass
        logger.info("worker stopped", extra={"owner": self.owner})

    async def _process(self, job: Job) -> None:
        handler = self._handlers.get(job.job_type)
        if handler is None:
            async with self._engine.begin() as conn:
                await JobQueue(conn).dead_letter(
                    job, error=f"no handler for job type '{job.job_type}'", now=self._now()
                )
            logger.error(
                "job has no handler", extra={"job_id": str(job.id), "job_type": job.job_type}
            )
            return
        heartbeat = asyncio.create_task(self._keep_lease(job))
        try:
            async with self._engine.begin() as conn:
                await handler(job, conn)
                if not await JobQueue(conn).complete(job, self._now()):
                    raise LeaseLostError  # rolls the handler's writes back
        except LeaseLostError:
            logger.warning(
                "lease lost; leaving the job to its new owner", extra={"job_id": str(job.id)}
            )
        except Exception as exc:
            await self._record_failure(job, exc)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def _keep_lease(self, job: Job) -> None:
        """Extend the lease every third of its length while the handler runs.

        Without this a legitimately slow job (a huge run) outlives its lease, is reclaimed and run
        again in parallel, and its own result is discarded as a lost lease.
        """
        lease = timedelta(seconds=self._settings.worker_lease_seconds)
        while True:
            await asyncio.sleep(lease.total_seconds() / 3)
            try:
                async with self._engine.begin() as conn:
                    if not await JobQueue(conn).extend_lease(job, lease, self._now()):
                        logger.warning("lease lost while running", extra={"job_id": str(job.id)})
                        return
            except (
                Exception
            ):  # a database hiccup must not kill the heartbeat; the next beat retries
                logger.warning(
                    "lease heartbeat failed", extra={"job_id": str(job.id)}, exc_info=True
                )

    async def _record_failure(self, job: Job, exc: Exception) -> None:
        s = self._settings
        message = f"{type(exc).__name__}: {exc}"
        async with self._engine.begin() as conn:
            outcome = await JobQueue(conn).fail(
                job,
                error=message,
                now=self._now(),
                max_attempts=s.worker_max_attempts,
                retry_in=backoff_seconds(
                    job.attempt_count, s.worker_backoff_base_seconds, s.worker_backoff_max_seconds
                ),
            )
        log = logger.error if outcome == "dead_letter" else logger.warning
        log(
            "job failed",
            extra={
                "job_id": str(job.id),
                "job_type": job.job_type,
                "attempt": job.attempt_count,
                "outcome": outcome,
                "error_type": type(exc).__name__,
            },
            exc_info=outcome == "dead_letter",
        )


async def _main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, service="abb-worker")
    engine = create_engine(settings.database_url)
    worker = Worker(engine, HANDLERS, settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)  # finish the current batch, then exit cleanly
    try:
        await worker.run_forever(stop)
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()

"""The background worker (spec §72): claim jobs, run handlers, retry, dead-letter.

A handler runs in the same transaction that marks its job done, so its writes and the job's
completion commit or roll back together. If the lease was lost meanwhile (the job was reclaimed
by another worker) the transaction is rolled back and the job is left to its new owner.
Delivery is at-least-once: handlers must be idempotent.
"""

import asyncio
import logging
import os
import signal
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from abb_api.clock import Clock, system_clock
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
        clock: Clock = system_clock,
        owner: str | None = None,
    ) -> None:
        self._engine = engine
        self._handlers = handlers
        self._settings = settings
        self._clock = clock
        self.owner = owner or f"{os.uname().nodename}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

    async def run_once(self) -> int:
        """Claim and process one batch of jobs; returns how many were claimed."""
        async with self._engine.begin() as conn:
            jobs = await JobQueue(conn).claim(
                owner=self.owner,
                limit=self._settings.worker_batch_size,
                lease=timedelta(seconds=self._settings.worker_lease_seconds),
                now=self._clock(),
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
                    job, error=f"no handler for job type '{job.job_type}'", now=self._clock()
                )
            logger.error(
                "job has no handler", extra={"job_id": str(job.id), "job_type": job.job_type}
            )
            return
        try:
            async with self._engine.begin() as conn:
                await handler(job, conn)
                if not await JobQueue(conn).complete(job, self._clock()):
                    raise LeaseLostError  # rolls the handler's writes back
        except LeaseLostError:
            logger.warning(
                "lease lost; leaving the job to its new owner", extra={"job_id": str(job.id)}
            )
        except Exception as exc:
            await self._record_failure(job, exc)

    async def _record_failure(self, job: Job, exc: Exception) -> None:
        s = self._settings
        message = f"{type(exc).__name__}: {exc}"
        async with self._engine.begin() as conn:
            outcome = await JobQueue(conn).fail(
                job,
                error=message,
                now=self._clock(),
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

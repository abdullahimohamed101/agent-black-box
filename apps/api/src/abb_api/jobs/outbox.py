"""Transactional outbox (spec §72): enqueue in the same transaction as the data, claim with leases.

Delivery is at-least-once, so handlers must be idempotent. A job moves
`pending -> running -> done`, back to `pending` with a backoff after a failure, and to
`dead_letter` once its attempts are used up. A `running` job whose lease expired (a crashed
worker) is claimable again.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.db import tables as t
from abb_api.tenancy import TenantContext

SUMMARIZE_RUN = "summarize_run"
REFRESH_ANALYTICS_DAY = "refresh_analytics_day"
MAX_ERROR_LENGTH = 2000


@dataclass(frozen=True)
class Job:
    id: uuid.UUID
    job_type: str
    workspace_id: uuid.UUID
    dedupe_key: str
    payload: dict[str, Any]
    attempt_count: int  # including the attempt this claim starts
    lease_owner: str


def backoff_seconds(attempt: int, base: float, cap: float) -> float:
    """Delay before retry number `attempt` (1-based): base, 2*base, 4*base ... up to cap."""
    return float(min(cap, base * (2 ** (attempt - 1))))


def oldest_first(rows: Iterable[Any]) -> list[Any]:
    """UPDATE ... RETURNING does not preserve the CTE's ORDER BY. Restore the order in which jobs
    became due so that workers process them oldest first."""
    return sorted(rows, key=lambda r: (r.available_at, r.created_at, r.id))


def _at(now: datetime | None) -> Any:
    """An explicit time (tests) or the database clock."""
    return now if now is not None else func.now()


def _truncate(error: str) -> str:
    return error if len(error) <= MAX_ERROR_LENGTH else error[:MAX_ERROR_LENGTH] + "…"


def summarize_key(workspace_id: uuid.UUID, run_id: uuid.UUID) -> str:
    """The coalescing key of a run's summarize job (shared by ingestion and rebuilds)."""
    return f"{workspace_id}:{run_id}"


class OutboxRepository:
    """Tenant-scoped producer side: jobs are always created for one workspace."""

    def __init__(self, conn: AsyncConnection, tenant: TenantContext) -> None:
        self._conn = conn
        self._tenant = tenant

    async def enqueue(
        self,
        *,
        job_type: str,
        dedupe_key: str,
        payload: dict[str, Any],
        delay: timedelta | None = None,
    ) -> bool:
        """Add a pending job unless an identical one is already pending (coalescing).

        `delay` postpones the job; events arriving meanwhile coalesce into it. Returns True if a
        new job was created. Written in the caller's transaction, so a job
        exists if and only if the data that needs processing committed.
        """
        values: dict[str, Any] = {
            "id": uuid.uuid4(),
            "job_type": job_type,
            "workspace_id": self._tenant.workspace_id,
            "dedupe_key": dedupe_key,
            "payload": payload,
        }
        if delay:  # relative to the database clock, like every other scheduling decision
            values["available_at"] = func.now() + delay
        result = await self._conn.execute(
            insert(t.outbox_jobs)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=["job_type", "dedupe_key"], index_where=text("status = 'pending'")
            )
            .returning(t.outbox_jobs.c.id)
        )
        return result.first() is not None

    async def enqueue_summarize(self, run_ids: Sequence[uuid.UUID]) -> int:
        """Ask for these runs to be re-derived (cost or rule changes); returns jobs created."""
        created = 0
        for run_id in sorted(run_ids, key=lambda r: r.bytes):
            created += await self.enqueue(
                job_type=SUMMARIZE_RUN,
                dedupe_key=summarize_key(self._tenant.workspace_id, run_id),
                payload={"run_id": str(run_id)},
            )
        return created


class JobQueue:
    """Consumer side: workers serve every tenant, so this is deliberately not tenant-scoped."""

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def claim(
        self,
        *,
        owner: str,
        limit: int,
        lease: timedelta,
        now: datetime | None = None,
        max_attempts: int | None = None,
    ) -> list[Job]:
        """Take up to `limit` runnable jobs. Concurrent claimers never receive the same job.

        `now=None` (production) uses the database clock, the single time source for scheduling:
        `available_at` is written by `now()` in the database, so comparing it with a host clock
        that is a few milliseconds off would hide fresh jobs. Tests pass an explicit time.

        With `max_attempts`, a job whose lease expired after its last allowed attempt is
        dead-lettered instead of reclaimed: a job that kills its worker (say, out of memory on a
        huge run) must not crash a fresh worker every lease interval forever.
        """
        if max_attempts is not None:
            await self._conn.execute(
                text(
                    """
                    UPDATE outbox_jobs
                    SET status = 'dead_letter', lease_owner = NULL, lease_expires_at = NULL,
                        last_error = 'lease expired after ' || attempt_count
                            || ' attempts (the worker died or the job outlived its lease)',
                        updated_at = COALESCE(CAST(:now AS timestamptz), now())
                    WHERE status = 'running'
                      AND lease_expires_at < COALESCE(CAST(:now AS timestamptz), now())
                      AND attempt_count >= :max_attempts
                    """
                ),
                {"now": now, "max_attempts": max_attempts},
            )
        claimed = await self._conn.execute(
            text(
                """
                WITH clock AS (SELECT COALESCE(CAST(:now AS timestamptz), now()) AS t),
                picked AS (
                    SELECT id FROM outbox_jobs
                    WHERE (status = 'pending' AND available_at <= (SELECT t FROM clock))
                       OR (status = 'running' AND lease_expires_at < (SELECT t FROM clock))
                    ORDER BY available_at, created_at, id
                    LIMIT :limit
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE outbox_jobs AS j
                SET status = 'running', lease_owner = :owner,
                    lease_expires_at = (SELECT t FROM clock)
                        + make_interval(secs => :lease_seconds),
                    attempt_count = j.attempt_count + 1, updated_at = (SELECT t FROM clock)
                FROM picked
                WHERE j.id = picked.id
                RETURNING j.id, j.job_type, j.workspace_id, j.dedupe_key, j.payload,
                          j.attempt_count, j.lease_owner, j.available_at, j.created_at
                """
            ),
            {
                "now": now,
                "limit": limit,
                "owner": owner,
                "lease_seconds": lease.total_seconds(),
            },
        )
        rows = oldest_first(claimed)
        return [
            Job(
                r.id,
                r.job_type,
                r.workspace_id,
                r.dedupe_key,
                r.payload,
                r.attempt_count,
                r.lease_owner,
            )
            for r in rows
        ]

    async def extend_lease(self, job: Job, lease: timedelta, now: datetime | None = None) -> bool:
        """Heartbeat: push the lease out while the handler still runs. False = already lost."""
        result = await self._conn.execute(
            update(t.outbox_jobs)
            .where(
                t.outbox_jobs.c.id == job.id,
                t.outbox_jobs.c.status == "running",
                t.outbox_jobs.c.lease_owner == job.lease_owner,
            )
            .values(lease_expires_at=_at(now) + lease, updated_at=_at(now))
        )
        return result.rowcount == 1

    async def complete(self, job: Job, now: datetime | None = None) -> bool:
        """Mark done, but only if this worker still holds the lease. False means it was lost."""
        result = await self._conn.execute(
            update(t.outbox_jobs)
            .where(
                t.outbox_jobs.c.id == job.id,
                t.outbox_jobs.c.status == "running",
                t.outbox_jobs.c.lease_owner == job.lease_owner,
            )
            .values(status="done", lease_owner=None, lease_expires_at=None, updated_at=_at(now))
        )
        return result.rowcount == 1

    async def fail(
        self, job: Job, *, error: str, now: datetime | None, max_attempts: int, retry_in: float
    ) -> str:
        """Record a failed attempt: retry later, or dead-letter. Returns the new status."""
        guard = (
            t.outbox_jobs.c.id == job.id,
            t.outbox_jobs.c.status == "running",
            t.outbox_jobs.c.lease_owner == job.lease_owner,
        )
        if job.attempt_count >= max_attempts:
            await self._conn.execute(
                update(t.outbox_jobs)
                .where(*guard)
                .values(
                    status="dead_letter",
                    last_error=_truncate(error),
                    lease_owner=None,
                    lease_expires_at=None,
                    updated_at=_at(now),
                )
            )
            return "dead_letter"
        retry = (
            update(t.outbox_jobs)
            .where(*guard)
            .values(
                status="pending",
                available_at=_at(now) + timedelta(seconds=retry_in),
                last_error=_truncate(error),
                lease_owner=None,
                lease_expires_at=None,
                updated_at=_at(now),
            )
        )
        try:
            async with self._conn.begin_nested():
                await self._conn.execute(retry)
            return "pending"
        except IntegrityError:
            # Newer events already queued a pending job for the same work; it will redo this
            # attempt's work in full, so this job is superseded rather than retried.
            await self._conn.execute(
                update(t.outbox_jobs)
                .where(*guard)
                .values(
                    status="done",
                    last_error=_truncate(f"superseded after failure: {error}"),
                    lease_owner=None,
                    lease_expires_at=None,
                    updated_at=_at(now),
                )
            )
            return "superseded"

    async def dead_letter(self, job: Job, *, error: str, now: datetime | None = None) -> None:
        await self._conn.execute(
            update(t.outbox_jobs)
            .where(t.outbox_jobs.c.id == job.id, t.outbox_jobs.c.lease_owner == job.lease_owner)
            .values(
                status="dead_letter",
                last_error=_truncate(error),
                lease_owner=None,
                lease_expires_at=None,
                updated_at=_at(now),
            )
        )

    async def requeue_dead_letters(
        self, now: datetime | None = None, job_id: uuid.UUID | None = None
    ) -> int:
        """Operator action after a fix: give dead-lettered jobs a fresh set of attempts."""
        statement = (
            update(t.outbox_jobs)
            .where(t.outbox_jobs.c.status == "dead_letter")
            .values(
                status="pending",
                attempt_count=0,
                available_at=_at(now),
                last_error=None,
                updated_at=_at(now),
            )
        )
        if job_id is not None:
            statement = statement.where(t.outbox_jobs.c.id == job_id)
        try:
            async with self._conn.begin_nested():
                result = await self._conn.execute(statement)
        except IntegrityError:
            return 0  # a pending job for that work already exists; nothing to revive
        return int(result.rowcount)

    async def list_jobs(self, status: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._conn.execute(
            select(
                t.outbox_jobs.c.id,
                t.outbox_jobs.c.job_type,
                t.outbox_jobs.c.dedupe_key,
                t.outbox_jobs.c.attempt_count,
                t.outbox_jobs.c.last_error,
                t.outbox_jobs.c.updated_at,
            )
            .where(t.outbox_jobs.c.status == status)
            .order_by(t.outbox_jobs.c.updated_at.desc())
            .limit(limit)
        )
        return [dict(r._mapping) for r in rows]

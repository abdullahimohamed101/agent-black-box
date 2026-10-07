"""Transactional outbox (spec §72): enqueue in the same transaction as the data, claim with leases.

Delivery is at-least-once, so handlers must be idempotent. A job moves
`pending -> running -> done`, back to `pending` with a backoff after a failure, and to
`dead_letter` once its attempts are used up. A `running` job whose lease expired (a crashed
worker) is claimable again.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.db import tables as t
from abb_api.tenancy import TenantContext

SUMMARIZE_RUN = "summarize_run"
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


def _truncate(error: str) -> str:
    return error if len(error) <= MAX_ERROR_LENGTH else error[:MAX_ERROR_LENGTH] + "…"


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
        available_at: datetime | None = None,
    ) -> bool:
        """Add a pending job unless an identical one is already pending (coalescing).

        Returns True if a new job was created. Written in the caller's transaction, so a job
        exists if and only if the data that needs processing committed.
        """
        values: dict[str, Any] = {
            "id": uuid.uuid4(),
            "job_type": job_type,
            "workspace_id": self._tenant.workspace_id,
            "dedupe_key": dedupe_key,
            "payload": payload,
        }
        if available_at is not None:
            values["available_at"] = available_at
        result = await self._conn.execute(
            insert(t.outbox_jobs)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=["job_type", "dedupe_key"], index_where=text("status = 'pending'")
            )
            .returning(t.outbox_jobs.c.id)
        )
        return result.first() is not None


class JobQueue:
    """Consumer side: workers serve every tenant, so this is deliberately not tenant-scoped."""

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def claim(self, *, owner: str, limit: int, lease: timedelta, now: datetime) -> list[Job]:
        """Take up to `limit` runnable jobs. Concurrent claimers never receive the same job."""
        claimed = await self._conn.execute(
            text(
                """
                WITH picked AS (
                    SELECT id FROM outbox_jobs
                    WHERE (status = 'pending' AND available_at <= :now)
                       OR (status = 'running' AND lease_expires_at < :now)
                    ORDER BY available_at, created_at, id
                    LIMIT :limit
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE outbox_jobs AS j
                SET status = 'running', lease_owner = :owner, lease_expires_at = :expires,
                    attempt_count = j.attempt_count + 1, updated_at = :now
                FROM picked
                WHERE j.id = picked.id
                RETURNING j.id, j.job_type, j.workspace_id, j.dedupe_key, j.payload,
                          j.attempt_count, j.lease_owner
                """
            ),
            {"now": now, "limit": limit, "owner": owner, "expires": now + lease},
        )
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
            for r in claimed
        ]

    async def complete(self, job: Job, now: datetime) -> bool:
        """Mark done, but only if this worker still holds the lease. False means it was lost."""
        result = await self._conn.execute(
            update(t.outbox_jobs)
            .where(
                t.outbox_jobs.c.id == job.id,
                t.outbox_jobs.c.status == "running",
                t.outbox_jobs.c.lease_owner == job.lease_owner,
            )
            .values(status="done", lease_owner=None, lease_expires_at=None, updated_at=now)
        )
        return result.rowcount == 1

    async def fail(
        self, job: Job, *, error: str, now: datetime, max_attempts: int, retry_in: float
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
                    updated_at=now,
                )
            )
            return "dead_letter"
        retry = (
            update(t.outbox_jobs)
            .where(*guard)
            .values(
                status="pending",
                available_at=now + timedelta(seconds=retry_in),
                last_error=_truncate(error),
                lease_owner=None,
                lease_expires_at=None,
                updated_at=now,
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
                    updated_at=now,
                )
            )
            return "superseded"

    async def dead_letter(self, job: Job, *, error: str, now: datetime) -> None:
        await self._conn.execute(
            update(t.outbox_jobs)
            .where(t.outbox_jobs.c.id == job.id, t.outbox_jobs.c.lease_owner == job.lease_owner)
            .values(
                status="dead_letter",
                last_error=_truncate(error),
                lease_owner=None,
                lease_expires_at=None,
                updated_at=now,
            )
        )

    async def requeue_dead_letters(self, now: datetime, job_id: uuid.UUID | None = None) -> int:
        """Operator action after a fix: give dead-lettered jobs a fresh set of attempts."""
        statement = (
            update(t.outbox_jobs)
            .where(t.outbox_jobs.c.status == "dead_letter")
            .values(
                status="pending", attempt_count=0, available_at=now, last_error=None, updated_at=now
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

"""Clean-ups that are not about a run's tiers: the audit log and the operational tables (ADR-160).

* The audit log tier deletes entries older than the workspace's cutoff, a batch at a time, and each
  batch writes ONE summary entry (counts and the age range of what it removed) in the same
  transaction: an audit entry is never removed without a record of the removal, and a summary is
  subject to the same cutoff, by its own age, like any entry (KI-072).
* Operational tables have fixed windows (finished jobs, expired counters, old usage rows, dead
  sessions, people who never joined), documented in `docs/OPERATIONS.md`. The people clean-up
  re-checks every reference in its own DELETE (KI-194).

Counts only, never content. A table or predicate here is a constant of this package.
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.audit.repository import clean_details
from abb_api.db import tables as t
from abb_api.limits.scopes import GLOBAL_WORKSPACE
from abb_api.retention import model as m
from abb_api.retention.batches import Batcher
from abb_api.retention.measure import stranger_predicate

logger = logging.getLogger(__name__)

AUDIT_ACTOR = "retention"
# One fixed action name per kind of batch (no data in names).
ACTION_PREFIX = "retention.delete."
ACTIONS = {
    "artifacts": ACTION_PREFIX + "artifacts",
    "trace": ACTION_PREFIX + "trace",
    "run": ACTION_PREFIX + "run",
    "audit": ACTION_PREFIX + "audit",
    "operational": ACTION_PREFIX + "operational",
}


async def insert_audit(
    conn: AsyncConnection, workspace_id: uuid.UUID, tier: str, details: dict[str, Any]
) -> None:
    await conn.execute(
        t.audit_log.insert().values(
            workspace_id=workspace_id,
            actor_kind="system",
            actor_id=AUDIT_ACTOR,
            action=ACTIONS[tier],
            resource_kind="workspace",
            resource_id=str(workspace_id),
            outcome="allowed",
            details=clean_details({"tier": tier, "mode": "enforce", **details}),
        )
    )


class Pruner(Batcher):
    async def audit_entry(
        self, workspace_id: uuid.UUID, tier: str, details: dict[str, Any]
    ) -> None:
        async with self._tx() as conn:
            await insert_audit(conn, workspace_id, tier, details)

    async def purge_audit(self, workspace_id: uuid.UUID, cutoff: datetime) -> int:
        """Entries of this workspace older than `cutoff`; one summary entry per batch."""
        total = 0
        sql = text(
            "WITH d AS (DELETE FROM audit_log WHERE ctid = ANY(ARRAY("
            "SELECT ctid FROM audit_log WHERE workspace_id = :w AND occurred_at < :cutoff "
            "ORDER BY occurred_at LIMIT :n)) RETURNING occurred_at) "
            "SELECT count(*), min(occurred_at), max(occurred_at) FROM d"
        )
        while True:
            await self.control.checkpoint()
            async with self._tx() as conn:
                n, oldest, newest = (
                    await conn.execute(sql, {"w": workspace_id, "cutoff": cutoff, "n": self._batch})
                ).one()
                if n:
                    await insert_audit(
                        conn,
                        workspace_id,
                        "audit",
                        {
                            "rows": int(n),
                            "oldest": oldest.isoformat(),
                            "newest": newest.isoformat(),
                        },
                    )
            total += int(n)
            self.control.spend(int(n))
            if n < self._batch:
                return total

    async def operational(self, workspace_id: uuid.UUID, now: datetime) -> dict[str, int]:
        """A workspace's finished jobs, expired counters and old usage rows (fixed windows)."""
        params: dict[str, Any] = {"w": workspace_id}
        done = now - timedelta(days=m.FINISHED_JOB_DAYS)
        dead = now - timedelta(days=m.DEAD_LETTER_JOB_DAYS)
        usage_day = (now - timedelta(days=m.USAGE_DAILY_DAYS)).date()
        counts = {
            "jobs": await self._drain(
                "outbox_jobs",
                "workspace_id = :w AND ((status = 'done' AND updated_at < :done) "
                "OR (status = 'dead_letter' AND updated_at < :dead))",
                {**params, "done": done, "dead": dead},
                point=None,
            ),
            "counters": await self._drain(
                "rate_limit_counters",
                "workspace_id = :w AND expires_at < :c",
                {**params, "c": now - timedelta(hours=m.COUNTER_GRACE_HOURS)},
                point=None,
            ),
            "usage": await self._drain(
                "project_usage_daily",
                "workspace_id = :w AND day < :d",
                {**params, "d": usage_day},
                point=None,
            ),
            "dirty_days": await self._drain(
                "analytics_dirty",
                "workspace_id = :w AND day < :d AND version = refreshed_version",
                {**params, "d": usage_day},
                point=None,
            ),
        }
        return counts

    async def platform(self, now: datetime) -> dict[str, int]:
        """Dead sessions, expired global counters and people who never joined (fixed windows)."""
        grace = now - timedelta(days=m.SESSION_GRACE_DAYS)
        counts = {
            "sessions": await self._drain(
                "sessions",
                "expires_at < :g OR idle_expires_at < :g OR revoked_at < :g",
                {"g": grace},
                point=None,
                key="id",
            ),
            "counters": await self._drain(
                "rate_limit_counters",
                "workspace_id = :w AND expires_at < :c",
                {"w": GLOBAL_WORKSPACE, "c": now - timedelta(hours=m.COUNTER_GRACE_HOURS)},
                point=None,
            ),
        }
        counts["people"] = await self._strangers(now)
        return counts

    async def _strangers(self, now: datetime) -> int:
        """Remove people who never joined a workspace, one at a time, each re-verified in its own
        DELETE (a reference that appeared after the selection keeps the person)."""
        u, s = t.users, t.sessions
        await self.control.checkpoint()
        async with self._engine.connect() as conn:
            ids = [
                r[0]
                for r in await conn.execute(
                    select(u.c.id)
                    .where(stranger_predicate(now))
                    .order_by(u.c.created_at, u.c.id)
                    .limit(max(self.control.max_rows - self.control.rows, 0))
                )
            ]
        removed = 0
        for start in range(0, len(ids), self._batch):
            await self.control.checkpoint()
            async with self._tx() as conn:
                for user_id in ids[start : start + self._batch]:
                    try:
                        async with conn.begin_nested():
                            await conn.execute(
                                delete(s).where(
                                    s.c.user_id == user_id,
                                    or_(
                                        s.c.expires_at <= now,
                                        s.c.idle_expires_at <= now,
                                        s.c.revoked_at.is_not(None),
                                    ),
                                )
                            )
                            gone = await conn.execute(
                                delete(u).where(u.c.id == user_id, stranger_predicate(now))
                            )
                            if gone.rowcount != 1:
                                raise _Kept
                            removed += 1
                    except (_Kept, IntegrityError):
                        # Referenced by something the predicate does not know (or changed since):
                        # the savepoint also restores the sessions removed above.
                        logger.info("retention kept a person it could not remove")
        self.control.spend(removed)
        return removed


class _Kept(Exception):
    pass

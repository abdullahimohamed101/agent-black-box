"""What retention would remove: counts per workspace and tier, never content (ADR-159).

Read-only, shared by the preview endpoint (the application's role) and the dry-run tick (the
retention role). Every query carries the workspace (INV-3) except the install-level `platform`
tier, which describes rows that belong to no workspace. Bounded: at most `max_runs` runs and
`max_rows` other rows are considered per tier, and `truncated` says when the bound was reached.

A run's *end* is its `completed_at`, or its last update while it has none, clamped between the
server's own first sight of the run and its last update. Client-claimed times are hostile input
(an end claimed in the distant past must not expire a run on the spot, nor one in the future keep it
for ever); the clamp bounds both by times the server wrote.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import (
    Select,
    and_,
    any_,
    bindparam,
    exists,
    func,
    literal,
    not_,
    or_,
    select,
)
from sqlalchemy import Text as SaText
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.db import tables as t
from abb_api.limits.scopes import GLOBAL_WORKSPACE
from abb_api.retention import inventory as inv
from abb_api.retention import model as m

RUN_ENDED_AT = func.least(
    func.greatest(func.coalesce(t.runs.c.completed_at, t.runs.c.updated_at), t.runs.c.created_at),
    t.runs.c.updated_at,
)


def _exists(*conditions: Any) -> Any:
    """EXISTS over a constant: `SELECT *` would need a privilege on every column (column grants)."""
    return exists(select(literal(1)).where(*conditions))


_UUIDS = PG_ARRAY(UUID(as_uuid=True))
_RUN_BOUND = (t.spans, t.cost_calculations, t.findings, t.run_findings, t.evaluations, t.run_search)


@dataclass(frozen=True)
class Limits:
    max_runs: int
    max_rows: int


@dataclass(frozen=True)
class TierCount:
    tier: m.ReportTier
    runs: int = 0
    events: int = 0
    artifact_bytes: int = 0
    rows: int = 0
    truncated: bool = False
    cutoff: datetime | None = None


async def _count(conn: AsyncConnection, query: Select[Any]) -> int:
    return int((await conn.execute(query)).scalar_one())


async def _capped(conn: AsyncConnection, where: Select[Any], cap: int) -> tuple[int, bool]:
    """How many rows `where` selects, looking at no more than `cap + 1` of them."""
    probe = where.limit(cap + 1).subquery()
    n = await _count(conn, select(func.count()).select_from(probe))
    return min(n, cap), n > cap


async def expired_runs(
    conn: AsyncConnection, workspace_id: uuid.UUID, cutoff: datetime, cap: int
) -> tuple[list[uuid.UUID], bool]:
    """The oldest runs that ended before `cutoff` (at most `cap`) and whether more exist."""
    rows = await conn.execute(
        select(t.runs.c.id)
        .where(t.runs.c.workspace_id == workspace_id, RUN_ENDED_AT < cutoff)
        .order_by(RUN_ENDED_AT, t.runs.c.id)
        .limit(cap + 1)
    )
    ids = [r[0] for r in rows]
    return ids[:cap], len(ids) > cap


def _in_runs(column: Any, ids: list[uuid.UUID]) -> Any:
    return column == any_(bindparam("run_ids", ids, type_=_UUIDS))


async def _artifacts(
    conn: AsyncConnection, workspace_id: uuid.UUID, cutoff: datetime, limits: Limits
) -> TierCount:
    ids, truncated = await expired_runs(conn, workspace_id, cutoff, limits.max_runs)
    a = t.artifacts
    owned = (
        await conn.execute(
            select(
                func.count(),
                func.coalesce(func.sum(a.c.size_bytes), 0),
                func.count(func.distinct(a.c.run_id)),
            ).where(a.c.workspace_id == workspace_id, _in_runs(a.c.run_id, ids))
        )
    ).one()
    # An artifact whose run does not exist (it never arrived, or was removed: KI-040) ages by its
    # own upload time.
    orphan = (
        await conn.execute(
            select(func.count(), func.coalesce(func.sum(a.c.size_bytes), 0)).where(
                a.c.workspace_id == workspace_id,
                a.c.created_at < cutoff,
                not_(_exists(t.runs.c.workspace_id == a.c.workspace_id, t.runs.c.id == a.c.run_id)),
            )
        )
    ).one()
    return TierCount(
        m.ReportTier.ARTIFACTS,
        runs=int(owned[2]),
        artifact_bytes=int(owned[1]) + int(orphan[1]),
        rows=int(owned[0]) + int(orphan[0]),
        truncated=truncated,
        cutoff=cutoff,
    )


async def _trace(
    conn: AsyncConnection, workspace_id: uuid.UUID, cutoff: datetime, limits: Limits
) -> TierCount:
    ids, truncated = await expired_runs(conn, workspace_id, cutoff, limits.max_runs)
    events = await _count(
        conn,
        select(func.count()).where(
            t.events.c.workspace_id == workspace_id, _in_runs(t.events.c.run_id, ids)
        ),
    )
    rows = 0
    for table in _RUN_BOUND:
        rows += await _count(
            conn,
            select(func.count()).where(
                table.c.workspace_id == workspace_id, _in_runs(table.c.run_id, ids)
            ),
        )
    # Run-bound background jobs are keyed `<workspace>:<run>` (summaries, findings, evaluations);
    # the same predicate the deletion uses, so a dry run counts what enforcement removes.
    rows += await _count(
        conn,
        select(func.count()).where(
            t.outbox_jobs.c.workspace_id == workspace_id,
            t.outbox_jobs.c.job_type
            == any_(bindparam("job_types", list(inv.RUN_BOUND_JOB_TYPES), type_=PG_ARRAY(SaText))),
            t.outbox_jobs.c.dedupe_key
            == any_(
                bindparam("job_keys", [f"{workspace_id}:{i}" for i in ids], type_=PG_ARRAY(SaText))
            ),
        ),
    )
    return TierCount(
        m.ReportTier.TRACE,
        runs=len(ids),
        events=events,
        rows=rows,
        truncated=truncated,
        cutoff=cutoff,
    )


async def _run(
    conn: AsyncConnection, workspace_id: uuid.UUID, cutoff: datetime, limits: Limits
) -> TierCount:
    ids, truncated = await expired_runs(conn, workspace_id, cutoff, limits.max_runs)
    return TierCount(
        m.ReportTier.RUN, runs=len(ids), rows=len(ids), truncated=truncated, cutoff=cutoff
    )


async def _audit(
    conn: AsyncConnection, workspace_id: uuid.UUID, cutoff: datetime, limits: Limits
) -> TierCount:
    n, truncated = await _capped(
        conn,
        select(literal(1)).where(
            t.audit_log.c.workspace_id == workspace_id, t.audit_log.c.occurred_at < cutoff
        ),
        limits.max_rows,
    )
    return TierCount(m.ReportTier.AUDIT, rows=n, truncated=truncated, cutoff=cutoff)


_MEASURES = {
    m.Tier.ARTIFACTS: _artifacts,
    m.Tier.TRACE: _trace,
    m.Tier.RUN: _run,
    m.Tier.AUDIT: _audit,
}


async def measure_tier(
    conn: AsyncConnection,
    workspace_id: uuid.UUID,
    tier: m.Tier,
    cutoff: datetime,
    limits: Limits,
) -> TierCount:
    """What a tier of one workspace would lose if everything older than `cutoff` went."""
    return await _MEASURES[tier](conn, workspace_id, cutoff, limits)


async def measure_operational(
    conn: AsyncConnection, workspace_id: uuid.UUID, now: datetime, limits: Limits
) -> TierCount:
    """Finished jobs, expired counters and old usage rows of one workspace (fixed windows)."""
    j, c, u = t.outbox_jobs, t.rate_limit_counters, t.project_usage_daily
    finished = now - timedelta(days=m.FINISHED_JOB_DAYS)
    dead = now - timedelta(days=m.DEAD_LETTER_JOB_DAYS)
    queries = [
        select(literal(1)).where(
            j.c.workspace_id == workspace_id,
            or_(
                and_(j.c.status == "done", j.c.updated_at < finished),
                and_(j.c.status == "dead_letter", j.c.updated_at < dead),
            ),
        ),
        select(literal(1)).where(
            c.c.workspace_id == workspace_id,
            c.c.expires_at < now - timedelta(hours=m.COUNTER_GRACE_HOURS),
        ),
        select(literal(1)).where(
            u.c.workspace_id == workspace_id,
            u.c.day < (now - timedelta(days=m.USAGE_DAILY_DAYS)).date(),
        ),
    ]
    total, truncated = 0, False
    for query in queries:
        n, cut = await _capped(conn, query, limits.max_rows)
        total, truncated = total + n, truncated or cut
    return TierCount(m.ReportTier.OPERATIONAL, rows=total, truncated=truncated)


def stranger_predicate(now: datetime) -> Any:
    """People who signed in on an invitation and never joined: no membership, no open invitation,
    no live session, nothing that references them. One predicate for the count and the delete, so
    a reference that appears after the count is honoured when the row is removed (KI-194)."""
    s, u = t.sessions, t.users
    members, invites, members_by = t.workspace_members, t.invitations, t.workspace_members.alias()
    return and_(
        u.c.created_at < now - timedelta(days=m.STRANGER_DAYS),
        not_(_exists(members.c.user_id == u.c.id)),
        not_(_exists(members_by.c.invited_by == u.c.id)),
        not_(
            _exists(
                invites.c.email == func.lower(u.c.email),
                invites.c.accepted_at.is_(None),
                invites.c.revoked_at.is_(None),
                invites.c.expires_at > now,
            )
        ),
        not_(_exists(or_(invites.c.invited_by == u.c.id, invites.c.accepted_by == u.c.id))),
        not_(
            _exists(
                s.c.user_id == u.c.id,
                s.c.revoked_at.is_(None),
                s.c.expires_at > now,
                s.c.idle_expires_at > now,
            )
        ),
        not_(_exists(t.evaluations.c.requested_by == u.c.id)),
        not_(
            _exists(
                or_(
                    t.approvals.c.requested_by_user_id == u.c.id,
                    t.approvals.c.decided_by_user_id == u.c.id,
                )
            )
        ),
    )


async def measure_platform(conn: AsyncConnection, now: datetime, limits: Limits) -> TierCount:
    """Dead sessions, people who never joined a workspace and expired global counters."""
    s, c = t.sessions, t.rate_limit_counters
    grace = now - timedelta(days=m.SESSION_GRACE_DAYS)
    stranger = stranger_predicate(now)
    queries = [
        select(literal(1)).where(
            or_(s.c.expires_at < grace, s.c.idle_expires_at < grace, s.c.revoked_at < grace)
        ),
        select(literal(1)).where(stranger),
        select(literal(1)).where(
            c.c.workspace_id == GLOBAL_WORKSPACE,
            c.c.expires_at < now - timedelta(hours=m.COUNTER_GRACE_HOURS),
        ),
    ]
    total, truncated = 0, False
    for query in queries:
        n, cut = await _capped(conn, query, limits.max_rows)
        total, truncated = total + n, truncated or cut
    return TierCount(m.ReportTier.PLATFORM, rows=total, truncated=truncated)


__all__ = ["Limits", "TierCount", "measure_operational", "measure_platform", "measure_tier"]

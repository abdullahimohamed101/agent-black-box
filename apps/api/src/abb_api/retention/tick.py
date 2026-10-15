"""One retention tick: for each workspace and tier, what the policy would delete (ADR-159).

Step 1 implements dry run only: the tick measures and writes `retention_reports` rows (counts, never
content) and deletes NOTHING. `enforce` is a valid mode that this release refuses to act on, and
`off` does nothing. The connection is the retention role's; this module is imported by the retention
runner only, never by the API process.

Bounded: a time budget per tick (checked before each workspace; the one due longest ago goes
first, so a tick that runs out of time resumes with the rest next time; a workspace that was
started is finished, bounded by its statement timeouts), at most
`retention_max_runs_per_tick` runs and `retention_max_rows_per_tick` rows per tier and workspace,
a statement timeout on every query, and at most `retention_reports_keep` reports per workspace and
tier.
"""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from abb_api.core.config import RetentionSettings
from abb_api.db import tables as t
from abb_api.retention import model as m
from abb_api.retention.measure import (
    Limits,
    TierCount,
    measure_operational,
    measure_platform,
    measure_tier,
)
from abb_api.retention.repository import policy_of

logger = logging.getLogger(__name__)

# Two ticks never run at once (two runners, or a manual run-once beside the loop).
TICK_LOCK_KEY = 0x4142425F52544E  # "ABB_RTN"

_enforce_refusal_logged = False


@dataclass
class TickResult:
    tick_id: uuid.UUID
    mode: str
    reports: int = 0
    workspaces: int = 0
    truncated: int = 0  # reports whose bound was reached
    errors: int = 0  # tiers that could not be measured (timeout, invalid policy, ...)
    budget_exhausted: bool = False
    locked_out: bool = False  # another tick holds the lock
    refused: bool = False  # enforce is not available in this release


async def _workspace_order(conn: AsyncConnection) -> list[uuid.UUID]:
    """Workspaces never reported on first, then the one reported on longest ago."""
    last = (
        select(
            t.retention_reports.c.workspace_id,
            func.max(t.retention_reports.c.finished_at).label("at"),
        )
        .group_by(t.retention_reports.c.workspace_id)
        .subquery()
    )
    rows = await conn.execute(
        select(t.workspaces.c.id)
        .outerjoin(last, last.c.workspace_id == t.workspaces.c.id)
        .order_by(last.c.at.asc().nulls_first(), t.workspaces.c.id)
    )
    return [r[0] for r in rows]


async def _write_report(
    conn: AsyncConnection,
    *,
    tick_id: uuid.UUID,
    workspace_id: uuid.UUID | None,
    count: TierCount,
    started: datetime,
    keep: int,
) -> None:
    r = t.retention_reports
    await conn.execute(
        insert(r).values(
            tick_id=tick_id,
            workspace_id=workspace_id,
            tier=count.tier.value,
            mode="dry_run",
            runs=count.runs,
            events=count.events,
            artifact_bytes=count.artifact_bytes,
            row_count=count.rows,
            truncated=count.truncated,
            cutoff_at=count.cutoff,
            started_at=started,
            finished_at=func.clock_timestamp(),
        )
    )
    scope = r.c.workspace_id == workspace_id if workspace_id else r.c.workspace_id.is_(None)
    newest = (
        select(r.c.id)
        .where(scope, r.c.tier == count.tier.value)
        .order_by(r.c.started_at.desc(), r.c.id.desc())
        .limit(keep)
    )
    await conn.execute(delete(r).where(scope, r.c.tier == count.tier.value, r.c.id.not_in(newest)))


async def run_tick(
    engine: AsyncEngine,
    settings: RetentionSettings,
    *,
    budget_seconds: float,
    keep_reports: int,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    monotonic: Callable[[], float] = time.monotonic,
) -> TickResult:
    """Run one tick in the configured mode. Never raises for a single tier's failure."""
    global _enforce_refusal_logged
    result = TickResult(tick_id=uuid.uuid4(), mode=settings.retention_mode)
    if settings.retention_mode == "off":
        return result
    if settings.retention_mode == "enforce":
        result.refused = True
        if not _enforce_refusal_logged:
            _enforce_refusal_logged = True
            logger.error(
                "ABB_RETENTION_MODE=enforce is not available yet: this release only measures "
                "(dry_run); nothing was deleted"
            )
        return result

    deadline = monotonic() + budget_seconds
    limits = Limits(settings.retention_max_runs_per_tick, settings.retention_max_rows_per_tick)
    timeout_ms = max(int(settings.retention_statement_timeout_seconds * 1000), 1)
    defaults = m.defaults_of(settings)

    # One connection holds the lock for the whole tick; the work uses its own short transactions.
    async with engine.connect() as lock_conn:
        got = (
            await lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": TICK_LOCK_KEY})
        ).scalar_one()
        if not got:
            result.locked_out = True
            return result
        try:
            async with engine.connect() as conn:
                workspaces = await _workspace_order(conn)
            for workspace_id in workspaces:
                if monotonic() >= deadline:
                    result.budget_exhausted = True
                    break
                await _workspace(
                    engine, workspace_id, defaults, limits, timeout_ms, keep_reports,
                    result, now,
                )  # fmt: skip
            if not result.budget_exhausted:
                await _platform(engine, limits, timeout_ms, keep_reports, result, now)
        finally:
            await lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": TICK_LOCK_KEY})
    return result


async def _measure_and_report(
    engine: AsyncEngine,
    result: TickResult,
    *,
    workspace_id: uuid.UUID | None,
    label: str,
    timeout_ms: int,
    keep: int,
    started: datetime,
    measure: Callable[[AsyncConnection], Awaitable[TierCount]],
) -> None:
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))
            count = await measure(conn)
            await _write_report(
                conn,
                tick_id=result.tick_id,
                workspace_id=workspace_id,
                count=count,
                started=started,
                keep=keep,
            )
    except Exception as error:  # one tier's failure must not stop the others (or leak content)
        result.errors += 1
        logger.warning(
            "retention measurement failed",
            extra={
                "tier": label,
                "workspace_id": str(workspace_id) if workspace_id else None,
                "error_type": type(error).__name__,
            },
        )
        return
    result.reports += 1
    result.truncated += 1 if count.truncated else 0


async def _workspace(
    engine: AsyncEngine,
    workspace_id: uuid.UUID,
    defaults: dict[m.Tier, int | None],
    limits: Limits,
    timeout_ms: int,
    keep: int,
    result: TickResult,
    now: Callable[[], datetime],
) -> None:
    result.workspaces += 1
    async with engine.connect() as conn:
        stored = (await policy_of(conn, workspace_id)).settings
    try:
        days = m.validate(defaults, stored)
    except m.RetentionInvalid as error:
        # A saved policy that the current defaults make invalid: measure nothing for it.
        result.errors += 1
        logger.warning(
            "retention policy invalid for a workspace",
            extra={"workspace_id": str(workspace_id), "tier": error.tier.value},
        )
        return
    for tier in m.TIERS:
        cutoff = m.cutoff(now(), days[tier])
        if cutoff is None:
            continue  # kept forever: nothing to measure, no report

        async def measure(
            c: AsyncConnection, tier: m.Tier = tier, cutoff: datetime = cutoff
        ) -> TierCount:
            return await measure_tier(c, workspace_id, tier, cutoff, limits)

        await _measure_and_report(
            engine, result, workspace_id=workspace_id, label=tier.value, timeout_ms=timeout_ms,
            keep=keep, started=now(), measure=measure,
        )  # fmt: skip
    moment = now()

    async def operational(c: AsyncConnection) -> TierCount:
        return await measure_operational(c, workspace_id, moment, limits)

    await _measure_and_report(
        engine, result, workspace_id=workspace_id, label="operational", timeout_ms=timeout_ms,
        keep=keep, started=moment, measure=operational,
    )  # fmt: skip


async def _platform(
    engine: AsyncEngine,
    limits: Limits,
    timeout_ms: int,
    keep: int,
    result: TickResult,
    now: Callable[[], datetime],
) -> None:
    moment = now()

    async def platform(c: AsyncConnection) -> TierCount:
        return await measure_platform(c, moment, limits)

    await _measure_and_report(
        engine, result, workspace_id=None, label="platform", timeout_ms=timeout_ms, keep=keep,
        started=moment, measure=platform,
    )  # fmt: skip

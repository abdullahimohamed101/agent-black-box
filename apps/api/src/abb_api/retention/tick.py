"""One retention tick: for each workspace and tier, what the policy deletes (enforce) or would
delete (dry run), ADR-159 and ADR-160.

`dry_run` measures and writes `retention_reports` rows (counts, never content) and deletes NOTHING.
`enforce` runs the same selection for real through `enforce.py` and `prune.py`, one workspace at a
time under an advisory lock, and reports what it actually removed. `off` does nothing. The
connection is the retention role's; this module is imported by the retention runner only, never by
the API process.

Bounded: a time budget per tick (checked before each workspace, and before every batch when
enforcing; the workspace due longest ago goes first, so a tick that runs out of time resumes with
the rest), at most `retention_max_runs_per_tick` runs and `retention_max_rows_per_tick` rows per
tier and workspace, a statement timeout on every query, and at most `retention_reports_keep`
reports per workspace and tier. Switching the mode away from `enforce` (or stopping the process)
ends enforcement at the next batch boundary, not at the next tick.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from abb_api.artifacts.store import ArtifactStore
from abb_api.core.config import RetentionSettings
from abb_api.core.request_context import set_operation
from abb_api.db import tables as t
from abb_api.retention import model as m
from abb_api.retention.batches import Control, Halt, Hook, no_hook
from abb_api.retention.enforce import Counts, Enforcer
from abb_api.retention.measure import (
    Limits,
    TierCount,
    measure_operational,
    measure_platform,
    measure_tier,
)
from abb_api.retention.prune import Pruner
from abb_api.retention.repository import StoredPolicy, policy_of

logger = logging.getLogger(__name__)

# Two ticks never run at once (two runners, or a manual run-once beside the loop).
TICK_LOCK_KEY = 0x4142425F52544E  # "ABB_RTN"

_enforce_refusal_logged = False
ModeProbe = Callable[[], Awaitable[str]]


@dataclass
class TierTally:
    """What one tick removed (enforce) or would remove (dry run) in one tier, all workspaces."""

    runs: int = 0
    events: int = 0
    rows: int = 0
    artifact_bytes: int = 0


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
    refused: bool = False  # enforce needs an artifact store, and was given none
    halted: str | None = None  # `mode`, `stop` or `budget`: enforcement ended early on purpose
    deleted_runs: int = 0  # run records removed (the run tier, enforce)
    expired_traces: int = 0  # runs whose trace detail was removed (the trace tier)
    deleted_events: int = 0
    failed: int = 0  # runs or days whose step failed (see `retention_run_progress`)
    skipped_locked: int = 0  # workspaces another process holds the lock of
    tiers: dict[str, TierTally] = field(default_factory=dict)  # for the metrics, by tier name

    def tally(self, tier: str, *, runs: int, events: int, rows: int, artifact_bytes: int) -> None:
        total = self.tiers.setdefault(tier, TierTally())
        total.runs += runs
        total.events += events
        total.rows += rows
        total.artifact_bytes += artifact_bytes


def _enforced(
    defaults: dict[m.Tier, int | None], policy: StoredPolicy, now: datetime, audit_floor: int
) -> dict[m.Tier, int | None]:
    """The periods the job applies now: the saved choices must be a valid policy, and a shortening
    that waits for its effective time is not applied yet (the longer period still is)."""
    m.validate(defaults, policy.settings)  # the stored floor; the audit minimum is applied below
    return m.enforced(defaults, policy.settings, policy.pending, now, audit_floor)


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
    mode: str = "dry_run",
) -> None:
    r = t.retention_reports
    await conn.execute(
        insert(r).values(
            tick_id=tick_id,
            workspace_id=workspace_id,
            tier=count.tier.value,
            mode=mode,
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
    store: ArtifactStore | None = None,
    mode_probe: ModeProbe | None = None,
    stop: asyncio.Event | None = None,
    hook: Hook = no_hook,
) -> TickResult:
    """Run one tick in the configured mode. Never raises for a single tier's failure.

    `store` is required to enforce (artifact files are deleted through the contract, INV-7).
    `mode_probe` is asked before every batch whether enforcement may go on (the kill switch);
    `stop` ends it the same way; `hook` is the crash-injection seam of the tests.
    """
    global _enforce_refusal_logged
    set_operation("retention.tick")
    result = TickResult(tick_id=uuid.uuid4(), mode=settings.retention_mode)
    if settings.retention_mode == "off":
        return result
    enforcing = settings.retention_mode == "enforce"
    if enforcing and store is None:
        result.refused = True
        if not _enforce_refusal_logged:
            _enforce_refusal_logged = True
            logger.error("ABB_RETENTION_MODE=enforce needs the artifact store; nothing was deleted")
        return result

    deadline = monotonic() + budget_seconds
    limits = Limits(settings.retention_max_runs_per_tick, settings.retention_max_rows_per_tick)
    timeout_ms = max(int(settings.retention_statement_timeout_seconds * 1000), 1)
    defaults = m.defaults_of(settings)
    audit_floor = settings.retention_audit_floor_days

    async def static_mode() -> str:
        return settings.retention_mode

    runner: _Enforcement | None = None
    if enforcing:
        assert store is not None
        control = Control(
            mode_probe or static_mode,
            deadline,
            monotonic,
            settings.retention_max_rows_per_tick,
            stop,
        )
        runner = _Enforcement(
            engine,
            Enforcer(
                engine,
                store,
                control,
                batch_size=settings.retention_batch_size,
                max_runs=settings.retention_max_runs_per_tick,
                max_attempts=settings.retention_max_attempts,
                timeout_ms=timeout_ms,
                hook=hook,
            ),
            Pruner(
                engine,
                control,
                batch_size=settings.retention_batch_size,
                timeout_ms=timeout_ms,
                hook=hook,
            ),
        )

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
                if runner is not None:
                    await runner.workspace(
                        lock_conn, workspace_id, defaults, audit_floor, timeout_ms, keep_reports,
                        result, now
                    )  # fmt: skip
                    if runner.enforcer.control.halted:
                        break
                    continue
                await _workspace(
                    engine, workspace_id, defaults, audit_floor, limits, timeout_ms, keep_reports,
                    result, now,
                )  # fmt: skip
            if runner is not None:
                result.halted = runner.enforcer.control.halted
                result.budget_exhausted = result.budget_exhausted or result.halted == "budget"
                if not result.halted and not result.budget_exhausted:
                    await runner.platform(engine, timeout_ms, keep_reports, result, now)
            elif not result.budget_exhausted:
                await _platform(engine, limits, timeout_ms, keep_reports, result, now)
        finally:
            await lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": TICK_LOCK_KEY})
    return result


class _Enforcement:
    """The enforcing half of a tick: per workspace, tiers in order, then the clean-ups."""

    def __init__(self, engine: AsyncEngine, enforcer: Enforcer, pruner: Pruner) -> None:
        self.engine = engine
        self.enforcer = enforcer
        self.pruner = pruner

    async def workspace(
        self,
        lock_conn: AsyncConnection,
        workspace_id: uuid.UUID,
        defaults: dict[m.Tier, int | None],
        audit_floor: int,
        timeout_ms: int,
        keep: int,
        result: TickResult,
        now: Callable[[], datetime],
    ) -> None:
        engine = self.engine
        key = f"abb_retention:{workspace_id}"
        got = (
            await lock_conn.execute(
                text("SELECT pg_try_advisory_lock(hashtextextended(:k, 0))"), {"k": key}
            )
        ).scalar_one()
        if not got:
            result.skipped_locked += 1  # another process (a purge command) is working here
            return
        try:
            result.workspaces += 1
            async with engine.connect() as conn:
                policy = await policy_of(conn, workspace_id)
            try:
                days = _enforced(defaults, policy, now(), audit_floor)
            except m.RetentionInvalid as error:
                result.errors += 1
                logger.warning(
                    "retention policy invalid for a workspace",
                    extra={"workspace_id": str(workspace_id), "tier": error.tier.value},
                )
                return
            await self._tiers(engine, workspace_id, days, keep, result, now)
        finally:
            await lock_conn.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:k, 0))"), {"k": key}
            )

    async def _tiers(
        self,
        engine: AsyncEngine,
        workspace_id: uuid.UUID,
        days: dict[m.Tier, int | None],
        keep: int,
        result: TickResult,
        now: Callable[[], datetime],
    ) -> None:
        control = self.enforcer.control
        try:
            for tier in (m.Tier.ARTIFACTS, m.Tier.TRACE, m.Tier.RUN):
                cutoff = m.cutoff(now(), days[tier])
                if cutoff is None:
                    continue
                started = now()
                counts = await self.enforcer.enforce_tier(workspace_id, tier, cutoff)
                await self._record(
                    engine, result, workspace_id, tier.value, counts, cutoff, started, keep
                )
                if control.halted:
                    return
            # The rollup refresh has its own row allowance, and a halt of one pass never skips the
            # later passes: only a real stop (mode, stop, budget) ends the workspace.
            failed = Counts()
            control.begin_pass()
            try:
                await self.enforcer.refresh_rollups(workspace_id, failed)
            except Halt as halt:
                if halt.reason != "rows":
                    control.halted = halt.reason
                    return
                result.truncated += 1  # days are left for the next tick
            finally:
                result.failed += failed.failed
            audit_cutoff = m.cutoff(now(), days[m.Tier.AUDIT])
            if audit_cutoff is not None:
                started = now()
                control.begin_pass()
                audit = Counts()
                try:
                    audit.rows = await self.pruner.purge_audit(workspace_id, audit_cutoff, now())
                except Halt as halt:
                    audit.halted = halt.reason
                    if halt.reason != "rows":
                        control.halted = halt.reason
                await self._report_only(
                    engine, result, workspace_id, "audit", audit, audit_cutoff, started, keep
                )
                if control.halted:
                    return
            started = now()
            control.begin_pass()
            operational = Counts()
            try:
                done = await self.pruner.operational(workspace_id, now())
                operational.rows = sum(done.values())
            except Halt as halt:
                operational.halted = halt.reason
                if halt.reason != "rows":
                    control.halted = halt.reason
            if operational.rows:
                await self.pruner.audit_entry(
                    workspace_id, "operational", {"rows": operational.rows}
                )
            await self._report_only(
                engine, result, workspace_id, "operational", operational, None, started, keep
            )
        except Halt as halt:
            if halt.reason != "rows":
                control.halted = halt.reason

    async def _record(
        self,
        engine: AsyncEngine,
        result: TickResult,
        workspace_id: uuid.UUID,
        tier: str,
        counts: Counts,
        cutoff: datetime,
        started: datetime,
        keep: int,
    ) -> None:
        if tier == m.Tier.RUN.value:
            result.deleted_runs += counts.runs
        elif tier == m.Tier.TRACE.value:
            result.expired_traces += counts.runs
        result.deleted_events += counts.events
        result.failed += counts.failed
        if counts.runs or counts.rows or counts.failed or counts.artifact_bytes:
            await self.pruner.audit_entry(
                workspace_id,
                tier,
                {
                    "runs": counts.runs,
                    "events": counts.events,
                    "rows": counts.rows,
                    "artifact_bytes": counts.artifact_bytes,
                    "failed": counts.failed,
                    "truncated": counts.halted is not None,
                },
            )
        await self._report_only(engine, result, workspace_id, tier, counts, cutoff, started, keep)

    async def _report_only(
        self,
        engine: AsyncEngine,
        result: TickResult,
        workspace_id: uuid.UUID | None,
        tier: str,
        counts: Counts,
        cutoff: datetime | None,
        started: datetime,
        keep: int,
    ) -> None:
        count = counts.as_tier_count(m.ReportTier(tier), cutoff)
        async with engine.begin() as conn:
            await _write_report(
                conn,
                tick_id=result.tick_id,
                workspace_id=workspace_id,
                count=count,
                started=started,
                keep=keep,
                mode="enforce",
            )
        result.reports += 1
        result.truncated += 1 if count.truncated else 0
        result.tally(
            tier,
            runs=counts.runs,
            events=counts.events,
            rows=counts.rows,
            artifact_bytes=counts.artifact_bytes,
        )

    async def platform(
        self,
        engine: AsyncEngine,
        timeout_ms: int,
        keep: int,
        result: TickResult,
        now: Callable[[], datetime],
    ) -> None:
        control = self.enforcer.control
        started = now()
        control.begin_pass()
        counts = Counts()
        try:
            done = await self.pruner.platform(now())
            counts.rows = sum(done.values())
        except Halt as halt:
            counts.halted = halt.reason
            if halt.reason != "rows":
                control.halted = halt.reason
                result.halted = halt.reason
                result.budget_exhausted = halt.reason == "budget"
        await self._report_only(engine, result, None, "platform", counts, None, started, keep)


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
    result.tally(
        count.tier.value,
        runs=count.runs,
        events=count.events,
        rows=count.rows,
        artifact_bytes=count.artifact_bytes,
    )


async def _workspace(
    engine: AsyncEngine,
    workspace_id: uuid.UUID,
    defaults: dict[m.Tier, int | None],
    audit_floor: int,
    limits: Limits,
    timeout_ms: int,
    keep: int,
    result: TickResult,
    now: Callable[[], datetime],
) -> None:
    result.workspaces += 1
    async with engine.connect() as conn:
        policy = await policy_of(conn, workspace_id)
    try:
        days = _enforced(defaults, policy, now(), audit_floor)
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
            return await measure_tier(c, workspace_id, tier, cutoff, limits, now())

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

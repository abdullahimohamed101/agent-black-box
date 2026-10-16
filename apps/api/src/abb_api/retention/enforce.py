"""Enforcement: remove the expired data of whole runs, resumably and idempotently (ADR-160).

One *procedure* per (run, tier). Its steps run in a fixed order and each step records its own
completion in `retention_run_progress`, so a crash at any point leaves the flags that were reached
and the next tick resumes right there (a step that finished is skipped; a step that did not is
repeated, and every step is idempotent):

    artifacts tier   artifact files (ArtifactStore), then their rows and the quota counter
    trace tier       mark the run `deleting`; search entry; events, spans and every other
                     run-derived row in batches; then, in ONE transaction, the last rows, the
                     `expired` mark and the tombstone; then the analytics rollups of the day
    run tier         the trace steps if they are not done, then the run record and its tombstone
                     in one transaction, then the analytics rollups of the day

A reader therefore sees `trace_state` go available -> deleting -> expired, never a half deleted run
presented as whole. The rollups are rebuilt AFTER the data they aggregate is gone (rebuilding first
would keep counting it). Failures are per run: a step that raises records a fixed failure code and
a bounded attempt count and the pass moves on to the next run.

Everything here is bounded: a batch is one short transaction with a statement timeout, and between
batches `Control.checkpoint` stops the pass when the mode was switched away from `enforce`, the
process was told to stop, the tick's time budget is spent or the pass deleted its row allowance.

Nothing in this module prints or logs content; counts, ids and fixed codes only.
"""

import logging
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Final

from sqlalchemy import and_, delete, exists, func, literal, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from abb_api.analytics.rollup import refresh_day
from abb_api.artifacts.store import ArtifactStore, ArtifactStoreError, valid_key
from abb_api.db import tables as t
from abb_api.retention import inventory as inv
from abb_api.retention import model as m
from abb_api.retention.batches import Batcher, Control, Halt, Hook, no_hook
from abb_api.retention.measure import RUN_ENDED_AT, TierCount
from abb_api.tenancy import TenantContext

logger = logging.getLogger(__name__)

P = t.retention_run_progress
A = t.artifacts

# Failure codes written to `retention_run_progress.failure_code` (a closed set, never a message).
FAIL_ARTIFACT_STORE: Final = "artifact_store"
FAIL_DATABASE: Final = "database"
FAIL_TIMEOUT: Final = "timeout"
FAIL_INTERNAL: Final = "internal"


class StepFailure(Exception):
    """A step failed in a way that is worth a fixed code; the message never carries content."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class Counts:
    """What one pass over one workspace and tier removed (counts only)."""

    runs: int = 0
    events: int = 0
    artifact_bytes: int = 0
    rows: int = 0
    failed: int = 0
    halted: str | None = None

    def as_tier_count(self, tier: m.ReportTier, cutoff: datetime | None) -> TierCount:
        return TierCount(
            tier,
            runs=self.runs,
            events=self.events,
            artifact_bytes=self.artifact_bytes,
            rows=self.rows,
            truncated=self.halted in ("rows", "budget", "stop", "mode"),
            cutoff=cutoff,
        )


@dataclass(frozen=True)
class Candidate:
    run_id: uuid.UUID
    resumed: bool


@dataclass
class Progress:
    run_id: uuid.UUID
    tier: m.Tier
    project_id: uuid.UUID | None
    day: date | None
    artifacts_done: bool
    search_done: bool
    detail_done: bool
    record_done: bool
    analytics_done: bool
    attempts: int = 0
    tombstoned_artifacts: bool = field(default=False)

    @property
    def complete(self) -> bool:
        return (
            self.artifacts_done
            and self.search_done
            and self.detail_done
            and self.record_done
            and self.analytics_done
        )


def failure_code(error: BaseException) -> str:
    """A fixed code for an exception; the exception text is never kept (INV-5)."""
    if isinstance(error, StepFailure):
        return error.code
    if isinstance(error, ArtifactStoreError):
        return FAIL_ARTIFACT_STORE
    if isinstance(error, TimeoutError):
        return FAIL_TIMEOUT
    if isinstance(error, DBAPIError):
        origin = getattr(error.orig, "sqlstate", None) or getattr(error.orig, "pgcode", None)
        return FAIL_TIMEOUT if origin == "57014" else FAIL_DATABASE
    if isinstance(error, SQLAlchemyError):
        return FAIL_DATABASE
    return FAIL_INTERNAL


class Enforcer(Batcher):
    """Deletes the expired data of one workspace. One instance per tick."""

    def __init__(
        self,
        engine: AsyncEngine,
        store: ArtifactStore,
        control: Control,
        *,
        batch_size: int,
        max_runs: int,
        max_attempts: int,
        timeout_ms: int,
        hook: Hook = no_hook,
    ) -> None:
        super().__init__(engine, control, batch_size=batch_size, timeout_ms=timeout_ms, hook=hook)
        self._store = store
        self._max_runs = max_runs
        self._max_attempts = max_attempts

    # ------------------------------------------------------------------ candidates

    async def candidates(
        self, workspace_id: uuid.UUID, tier: m.Tier, cutoff: datetime
    ) -> tuple[list[Candidate], bool]:
        """Runs to work on (unfinished first, then the oldest expired) and whether more exist."""
        async with self._engine.connect() as conn:
            resumed = (
                await conn.execute(
                    select(P.c.run_id)
                    .where(
                        P.c.workspace_id == workspace_id,
                        P.c.tier == tier.value,
                        P.c.attempts < self._max_attempts,
                        # A run whose record is gone and only awaits its rollup is not "work on the
                        # run": the analytics sweep handles it.
                        ~and_(
                            P.c.artifacts_done,
                            P.c.search_done,
                            P.c.detail_done,
                            P.c.record_done,
                        ),
                    )
                    .order_by(P.c.created_at, P.c.run_id)
                    .limit(self._max_runs)
                )
            ).all()
            found = [Candidate(r[0], True) for r in resumed]
            room = self._max_runs - len(found)
            more = False
            if room > 0:
                r = t.runs
                query = select(r.c.id).where(
                    r.c.workspace_id == workspace_id,
                    RUN_ENDED_AT < cutoff,
                    ~exists(
                        select(literal(1)).where(
                            P.c.workspace_id == r.c.workspace_id,
                            P.c.run_id == r.c.id,
                            P.c.tier == tier.value,
                        )
                    ),
                )
                if tier is m.Tier.ARTIFACTS:
                    query = query.where(
                        exists(
                            select(literal(1)).where(
                                A.c.workspace_id == r.c.workspace_id, A.c.run_id == r.c.id
                            )
                        )
                    )
                elif tier is m.Tier.TRACE:
                    query = query.where(r.c.trace_state != "expired")
                fresh = (
                    await conn.execute(query.order_by(RUN_ENDED_AT, r.c.id).limit(room + 1))
                ).all()
                more = len(fresh) > room
                found += [Candidate(row[0], False) for row in fresh[:room]]
            else:
                more = True
        return found, more

    # ------------------------------------------------------------------ a pass

    async def enforce_tier(self, workspace_id: uuid.UUID, tier: m.Tier, cutoff: datetime) -> Counts:
        """One bounded pass over one workspace and one run tier."""
        counts = Counts()
        self.control.begin_pass()
        try:
            await self.control.checkpoint()
            found, more = await self.candidates(workspace_id, tier, cutoff)
            if more:
                counts.halted = "rows"  # reported as truncated: more runs are waiting
            for candidate in found:
                await self.control.checkpoint()
                await self._run(workspace_id, tier, cutoff, candidate, counts)
            if tier is m.Tier.ARTIFACTS:
                await self._orphans(workspace_id, cutoff, counts)
        except Halt as halt:
            counts.halted = halt.reason
            if halt.reason != "rows":
                self.control.halted = (
                    halt.reason
                )  # the whole tick stops; "rows" ends this pass only
        return counts

    async def _run(
        self,
        workspace_id: uuid.UUID,
        tier: m.Tier,
        cutoff: datetime,
        candidate: Candidate,
        counts: Counts,
    ) -> None:
        try:
            progress = await self._begin(workspace_id, tier, cutoff, candidate.run_id)
            if progress is None:
                return
            await self._hook("marked")
            await self._steps(workspace_id, progress, counts)
        except Halt:
            raise
        except Exception as error:  # one run's failure never blocks the others
            code = failure_code(error)
            counts.failed += 1
            logger.warning(
                "retention step failed",
                extra={
                    "workspace_id": str(workspace_id),
                    "run_id": str(candidate.run_id),
                    "tier": tier.value,
                    "failure_code": code,
                    "error_type": type(error).__name__,
                },
            )
            await self._record_failure(workspace_id, tier, candidate.run_id, code)

    async def _record_failure(
        self, workspace_id: uuid.UUID, tier: m.Tier, run_id: uuid.UUID, code: str
    ) -> None:
        try:
            async with self._tx() as conn:
                await conn.execute(
                    update(P)
                    .where(
                        P.c.workspace_id == workspace_id,
                        P.c.run_id == run_id,
                        P.c.tier == tier.value,
                    )
                    .values(attempts=P.c.attempts + 1, failure_code=code, updated_at=func.now())
                )
        except Exception:  # the next tick finds the run again; never raise from bookkeeping
            logger.warning("could not record a retention failure", extra={"failure_code": code})

    # ------------------------------------------------------------------ begin (mark)

    async def _begin(
        self, workspace_id: uuid.UUID, tier: m.Tier, cutoff: datetime, run_id: uuid.UUID
    ) -> Progress | None:
        """The run's progress row, created (and the run marked `deleting`) in one transaction.

        A run that is no longer expired (it was updated after it was selected) is left alone; one
        whose progress row exists is resumed whatever the policy says now (half deleted data is
        not worth keeping).
        """
        r = t.runs
        async with self._tx() as conn:
            row = (
                (
                    await conn.execute(
                        select(P).where(
                            P.c.workspace_id == workspace_id,
                            P.c.run_id == run_id,
                            P.c.tier == tier.value,
                        )
                    )
                )
                .mappings()
                .first()
            )
            if row is not None:
                return _progress(row)
            run = (
                await conn.execute(
                    select(r.c.project_id, r.c.started_at, r.c.trace_state, RUN_ENDED_AT < cutoff)
                    .where(r.c.workspace_id == workspace_id, r.c.id == run_id)
                    .with_for_update()
                )
            ).first()
            if run is None or not run[3]:
                return None
            day = run[1].astimezone(UTC).date()
            presets = {
                m.Tier.ARTIFACTS: dict(
                    search_done=True, detail_done=True, record_done=True, analytics_done=True
                ),
                m.Tier.TRACE: dict(record_done=True),
                m.Tier.RUN: dict(),
            }[tier]
            await conn.execute(
                insert(P)
                .values(
                    workspace_id=workspace_id,
                    run_id=run_id,
                    tier=tier.value,
                    project_id=run[0],
                    started_day=day,
                    **presets,
                )
                .on_conflict_do_nothing()
            )
            if tier is not m.Tier.ARTIFACTS and run[2] == "available":
                await conn.execute(
                    update(r)
                    .where(r.c.workspace_id == workspace_id, r.c.id == run_id)
                    .values(trace_state="deleting")
                )
            fresh = (
                (
                    await conn.execute(
                        select(P).where(
                            P.c.workspace_id == workspace_id,
                            P.c.run_id == run_id,
                            P.c.tier == tier.value,
                        )
                    )
                )
                .mappings()
                .one()
            )
            return _progress(fresh)

    # ------------------------------------------------------------------ steps

    async def _steps(self, workspace_id: uuid.UUID, progress: Progress, counts: Counts) -> None:
        tier = progress.tier
        if not progress.artifacts_done:
            await self._artifacts_of_run(workspace_id, progress, counts)
            await self._flag(workspace_id, progress, "artifacts_done")
            await self._hook("artifacts")
            if tier is m.Tier.ARTIFACTS:
                counts.runs += 1
        if not progress.search_done:
            counts.rows += await self._search(workspace_id, progress.run_id)
            await self._flag(workspace_id, progress, "search_done")
            await self._hook("search")
        if not progress.detail_done:
            await self._detail(workspace_id, progress, counts)
            await self._hook("detail")
            if tier is m.Tier.TRACE:
                counts.runs += 1
        if not progress.record_done:
            await self._record(workspace_id, progress, counts)
            await self._hook("record")
            counts.runs += 1
        await self.sweep_done(workspace_id)

    async def _flag(self, workspace_id: uuid.UUID, progress: Progress, name: str) -> None:
        """Mark a step done (idempotent; the data steps that must commit the mark together with
        their last delete do it inside that transaction via `_set_flag`)."""
        async with self._tx() as conn:
            await _set_flag(conn, workspace_id, progress, name)

    # ---- artifacts

    async def _artifacts_of_run(
        self, workspace_id: uuid.UUID, progress: Progress, counts: Counts
    ) -> None:
        run_id = progress.run_id
        while True:
            await self.control.checkpoint()
            async with self._engine.connect() as conn:
                batch = (
                    await conn.execute(
                        select(A.c.id, A.c.storage_uri)
                        .where(A.c.workspace_id == workspace_id, A.c.run_id == run_id)
                        .order_by(A.c.id)
                        .limit(self._batch)
                    )
                ).all()
            if not batch:
                return

            async def finalize(conn: AsyncConnection) -> None:
                left = (
                    await conn.execute(
                        select(literal(1))
                        .where(A.c.workspace_id == workspace_id, A.c.run_id == run_id)
                        .limit(1)
                    )
                ).first()
                if left is None:  # the tombstone and the mark commit with the last delete
                    await _tombstone(conn, workspace_id, run_id, "artifacts")
                    await _set_flag(conn, workspace_id, progress, "artifacts_done")

            removed, size = await self._remove_artifacts(workspace_id, batch, finalize)
            counts.rows += removed
            counts.artifact_bytes += size
            self.control.spend(removed)
            await self._hook("artifact_batch")

    async def _orphans(self, workspace_id: uuid.UUID, cutoff: datetime, counts: Counts) -> None:
        """Artifacts whose run does not exist, expired by their own upload time (KI-040).

        A run that never arrives would otherwise keep its files for ever. No tombstone: there is
        no run to name; the batch's audit row counts them.
        """
        while True:
            await self.control.checkpoint()
            async with self._engine.connect() as conn:
                batch = (
                    await conn.execute(
                        select(A.c.id, A.c.storage_uri)
                        .where(
                            A.c.workspace_id == workspace_id,
                            A.c.created_at < cutoff,
                            ~exists(
                                select(literal(1)).where(
                                    t.runs.c.workspace_id == A.c.workspace_id,
                                    t.runs.c.id == A.c.run_id,
                                )
                            ),
                        )
                        .order_by(A.c.created_at, A.c.id)
                        .limit(self._batch)
                    )
                ).all()
            if not batch:
                return
            removed, size = await self._remove_artifacts(workspace_id, batch, None)
            counts.rows += removed
            counts.artifact_bytes += size
            self.control.spend(removed)
            await self._hook("artifact_batch")

    async def _remove_artifacts(
        self,
        workspace_id: uuid.UUID,
        batch: Sequence[Any],
        finalize: Callable[[AsyncConnection], Awaitable[None]] | None,
    ) -> tuple[int, int]:
        """Files first (through the store contract), then rows and the quota counter together.

        A crash between the two leaves rows whose file is already gone; the rerun's delete of a
        missing file is a no-op and the rows follow. The counter is decremented by exactly the
        bytes of the rows this transaction deleted, so a row can never be subtracted twice.
        """
        for row in batch:
            if not valid_key(row.storage_uri):
                continue  # not a key of ours: there is no file to remove
            try:
                await self._store.delete(row.storage_uri)
            except ArtifactStoreError as error:
                raise StepFailure(FAIL_ARTIFACT_STORE) from error
        ids = [row.id for row in batch]
        async with self._tx() as conn:
            gone = (
                await conn.execute(
                    delete(A)
                    .where(A.c.workspace_id == workspace_id, A.c.id.in_(ids))
                    .returning(A.c.project_id, A.c.size_bytes)
                )
            ).all()
            per_project: dict[uuid.UUID, int] = defaultdict(int)
            for project_id, size in gone:
                per_project[project_id] += int(size or 0)
            usage = t.project_artifact_usage
            for project_id, total in per_project.items():
                if total:
                    await conn.execute(
                        update(usage)
                        .where(
                            usage.c.workspace_id == workspace_id, usage.c.project_id == project_id
                        )
                        .values(bytes_stored=func.greatest(usage.c.bytes_stored - total, 0))
                    )
            if finalize is not None:
                await finalize(conn)
        return len(gone), sum(per_project.values())

    # ---- search and detail

    async def _search(self, workspace_id: uuid.UUID, run_id: uuid.UUID) -> int:
        await self.control.checkpoint()
        async with self._tx() as conn:
            result = await conn.execute(
                text("DELETE FROM run_search WHERE workspace_id = :w AND run_id = :r"),
                {"w": workspace_id, "r": run_id},
            )
            return int(result.rowcount)

    def _job_key(self, workspace_id: uuid.UUID, run_id: uuid.UUID) -> str:
        return f"{workspace_id}:{run_id}"

    async def _detail(self, workspace_id: uuid.UUID, progress: Progress, counts: Counts) -> None:
        run_id = progress.run_id
        params = {"w": workspace_id, "r": run_id}
        for table in inv.DETAIL_BATCHED:
            n = await self._drain(table, "workspace_id = :w AND run_id = :r", params)
            if table == "events":
                counts.events += n
            else:
                counts.rows += n
        jobs = await self._drain(
            "outbox_jobs",
            "workspace_id = :w AND dedupe_key = :k AND job_type = ANY(:types)",
            {"w": workspace_id, "k": self._job_key(workspace_id, run_id),
             "types": list(inv.RUN_BOUND_JOB_TYPES)},
        )  # fmt: skip
        counts.rows += jobs
        # The last rows, the mark and the tombstone commit together: the run is `deleting` until
        # this transaction and `expired` after it, with nothing in between.
        await self.control.checkpoint()
        async with self._tx() as conn:
            for table in inv.DETAIL_BATCHED:
                final = await conn.execute(
                    text(
                        f"DELETE FROM {table} WHERE workspace_id = :w AND run_id = :r"  # noqa: S608
                    ),
                    params,
                )
                if table == "events":
                    counts.events += int(final.rowcount)
                else:
                    counts.rows += int(final.rowcount)
            await conn.execute(
                text(
                    "DELETE FROM outbox_jobs WHERE workspace_id = :w AND dedupe_key = :k "
                    "AND job_type = ANY(:types)"
                ),
                {"w": workspace_id, "k": self._job_key(workspace_id, run_id),
                 "types": list(inv.RUN_BOUND_JOB_TYPES)},
            )  # fmt: skip
            await conn.execute(
                update(t.runs)
                .where(t.runs.c.workspace_id == workspace_id, t.runs.c.id == run_id)
                .values(trace_state="expired")
            )
            await _tombstone(conn, workspace_id, run_id, "trace")
            await _set_flag(conn, workspace_id, progress, "detail_done")

    async def _record(self, workspace_id: uuid.UUID, progress: Progress, counts: Counts) -> None:
        run_id = progress.run_id
        await self.control.checkpoint()
        async with self._tx() as conn:
            # RESTRICT guards the events: a late event that slipped in fails this step loudly (and
            # is retried after the next trace pass) rather than being cascaded away.
            gone = await conn.execute(
                delete(t.runs).where(t.runs.c.workspace_id == workspace_id, t.runs.c.id == run_id)
            )
            counts.rows += int(gone.rowcount)
            await _tombstone(conn, workspace_id, run_id, "run")
            await _set_flag(conn, workspace_id, progress, "record_done")

    # ------------------------------------------------------------------ analytics and cleanup

    async def sweep_done(self, workspace_id: uuid.UUID) -> None:
        """Forget the progress rows whose every backend is done (the tombstones stay)."""
        async with self._tx() as conn:
            await conn.execute(
                delete(P).where(
                    P.c.workspace_id == workspace_id,
                    P.c.artifacts_done,
                    P.c.search_done,
                    P.c.detail_done,
                    P.c.record_done,
                    P.c.analytics_done,
                )
            )

    async def refresh_rollups(self, workspace_id: uuid.UUID, counts: Counts) -> None:
        """Rebuild the rollups of every day a finished run belonged to, once per day.

        Only runs whose data is gone are waited for here; a day with a run that is still half
        deleted is rebuilt after it finishes (its row is not eligible yet).
        """
        eligible = and_(
            P.c.workspace_id == workspace_id,
            P.c.tier != "artifacts",
            ~P.c.analytics_done,
            P.c.artifacts_done,
            P.c.search_done,
            P.c.detail_done,
            P.c.record_done,
            P.c.attempts < self._max_attempts,
        )
        async with self._engine.connect() as conn:
            days = [
                r[0]
                for r in await conn.execute(
                    select(P.c.started_day).where(eligible).distinct().order_by(P.c.started_day)
                )
            ]
        for day in days:
            await self.control.checkpoint()
            try:
                async with self._tx() as conn:
                    await refresh_day(conn, TenantContext(workspace_id), day)
                    await conn.execute(
                        update(P)
                        .where(eligible, P.c.started_day == day)
                        .values(analytics_done=True, updated_at=func.now())
                    )
                await self._hook("analytics")
            except Halt:
                raise
            except Exception as error:
                code = failure_code(error)
                counts.failed += 1
                logger.warning(
                    "retention rollup refresh failed",
                    extra={
                        "workspace_id": str(workspace_id),
                        "day": day.isoformat(),
                        "failure_code": code,
                        "error_type": type(error).__name__,
                    },
                )
                async with self._tx() as conn:
                    await conn.execute(
                        update(P)
                        .where(eligible, P.c.started_day == day)
                        .values(attempts=P.c.attempts + 1, failure_code=code, updated_at=func.now())
                    )
        await self.sweep_done(workspace_id)


# ---------------------------------------------------------------------- helpers


def _progress(row: Any) -> Progress:
    return Progress(
        run_id=row["run_id"],
        tier=m.Tier(row["tier"]),
        project_id=row["project_id"],
        day=row["started_day"],
        artifacts_done=row["artifacts_done"],
        search_done=row["search_done"],
        detail_done=row["detail_done"],
        record_done=row["record_done"],
        analytics_done=row["analytics_done"],
        attempts=row["attempts"],
    )


async def _set_flag(
    conn: AsyncConnection, workspace_id: uuid.UUID, progress: Progress, name: str
) -> None:
    setattr(progress, name, True)
    await conn.execute(
        update(P)
        .where(
            P.c.workspace_id == workspace_id,
            P.c.run_id == progress.run_id,
            P.c.tier == progress.tier.value,
        )
        .values({name: True, "updated_at": func.now()})
    )


async def _tombstone(
    conn: AsyncConnection, workspace_id: uuid.UUID, run_id: uuid.UUID, tier: str
) -> None:
    await conn.execute(
        insert(t.retention_tombstones)
        .values(workspace_id=workspace_id, run_id=run_id, tier=tier, mode="enforce")
        .on_conflict_do_nothing()
    )

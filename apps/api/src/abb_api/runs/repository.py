"""Run persistence. Runs are derived (INV-2): ingestion seeds them, the summarizer owns them."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from abb_event_schema.ids import to_uuid
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.db import tables as t
from abb_api.runs.summary import SUMMARY_VERSION, RunDerivation
from abb_api.tenancy import TenantContext

SPAN_CHUNK = 1000


@dataclass(frozen=True)
class RunSeed:
    """What ingestion knows about a run the first time one of its events arrives."""

    run_id: uuid.UUID
    project_id: uuid.UUID
    trace_id: uuid.UUID
    agent_slug: str
    started_at: datetime


class RunRepository:
    def __init__(self, conn: AsyncConnection, tenant: TenantContext) -> None:
        self._conn = conn
        self._tenant = tenant

    async def ensure_runs(self, seeds: Sequence[RunSeed]) -> dict[uuid.UUID, uuid.UUID]:
        """Create any missing runs; return {run_id: owning project_id} for all of them.

        Arrival order must not matter, so the first event of an unseen run creates it. Rows are
        written in id order so concurrent batches touching the same runs cannot deadlock.
        """
        if not seeds:
            return {}
        ordered = sorted(seeds, key=lambda s: s.run_id.bytes)
        await self._conn.execute(
            insert(t.runs)
            .values(
                [
                    {
                        "workspace_id": self._tenant.workspace_id,
                        "id": s.run_id,
                        "project_id": s.project_id,
                        "agent_slug": s.agent_slug,
                        "trace_id": s.trace_id,
                        "status": "RUNNING",
                        "started_at": s.started_at,
                    }
                    for s in ordered
                ]
            )
            .on_conflict_do_nothing(index_elements=["workspace_id", "id"])
        )
        rows = await self._conn.execute(
            select(t.runs.c.id, t.runs.c.project_id).where(
                t.runs.c.workspace_id == self._tenant.workspace_id,
                t.runs.c.id.in_([s.run_id for s in ordered]),
            )
        )
        return {r.id: r.project_id for r in rows}

    async def run_ids(
        self, *, project_id: uuid.UUID | None, since: datetime | None, limit: int
    ) -> list[uuid.UUID]:
        """Newest runs of the tenant (optionally one project, optionally since a time)."""
        statement = select(t.runs.c.id).where(t.runs.c.workspace_id == self._tenant.workspace_id)
        if project_id is not None:
            statement = statement.where(t.runs.c.project_id == project_id)
        if since is not None:
            statement = statement.where(t.runs.c.started_at >= since)
        rows = await self._conn.execute(
            statement.order_by(t.runs.c.started_at.desc(), t.runs.c.id).limit(limit)
        )
        return [r.id for r in rows]

    async def lock(self, run_id: uuid.UUID) -> bool:
        """Take the run's row lock; False if the run does not exist.

        Summarizer jobs for one run must not interleave: a job that read an older snapshot could
        otherwise commit after a newer one and leave stale state with no job left to fix it. The
        lock is held until the transaction ends, and the caller must read events *after* taking it.

        FOR NO KEY UPDATE, not FOR UPDATE: inserting an event takes a key-share lock on its run
        (the foreign key), which FOR UPDATE would block for as long as a summarization runs.
        """
        row = (
            await self._conn.execute(
                select(t.runs.c.id)
                .where(t.runs.c.workspace_id == self._tenant.workspace_id, t.runs.c.id == run_id)
                .with_for_update(key_share=True)
            )
        ).first()
        return row is not None

    async def apply_derivation(self, run_id: uuid.UUID, derived: RunDerivation) -> int:
        """Write the derived run row and its spans. Returns spans skipped as belonging elsewhere."""
        await self._conn.execute(
            update(t.runs)
            .where(t.runs.c.workspace_id == self._tenant.workspace_id, t.runs.c.id == run_id)
            .values(
                status=derived.status.value,
                ordering_mode=derived.ordering_mode,
                started_at=derived.started_at,
                completed_at=derived.completed_at,
                duration_ms=derived.duration_ms,
                trace_id=to_uuid(derived.trace_id),
                agent_slug=derived.agent_slug,
                # an explicit name (from POST /v1/runs) is kept unless the events provide one
                name=func.coalesce(derived.name, t.runs.c.name),
                summary=derived.summary,
                summary_version=SUMMARY_VERSION,
                updated_at=func.now(),
            )
        )
        if not derived.spans:
            return 0
        rows = [
            {
                "workspace_id": self._tenant.workspace_id,
                "id": to_uuid(s.span_id),
                "run_id": run_id,
                "trace_id": to_uuid(s.trace_id),
                "parent_span_id": to_uuid(s.parent_span_id) if s.parent_span_id else None,
                "name": s.name,
                "kind": s.kind.value if s.kind else None,
                "agent_slug": s.agent_id,
                "status": s.status.value if s.status else None,
                "started_at": s.started_at,
                "ended_at": s.ended_at,
                "duration_ms": s.duration_ms,
                "event_count": s.event_count,
            }
            for s in sorted(derived.spans.values(), key=lambda s: to_uuid(s.span_id).bytes)
        ]
        skipped = 0
        # Chunked: one statement may carry at most 32767 bind parameters (13 per span).
        for start in range(0, len(rows), SPAN_CHUNK):
            chunk = rows[start : start + SPAN_CHUNK]
            statement = insert(t.spans).values(chunk)
            excluded = statement.excluded
            written = await self._conn.execute(
                statement.on_conflict_do_update(
                    index_elements=["workspace_id", "id"],
                    set_={
                        "trace_id": excluded.trace_id,
                        "parent_span_id": excluded.parent_span_id,
                        "name": excluded.name,
                        "kind": excluded.kind,
                        "agent_slug": excluded.agent_slug,
                        "status": excluded.status,
                        "started_at": excluded.started_at,
                        "ended_at": excluded.ended_at,
                        "duration_ms": excluded.duration_ms,
                        "event_count": excluded.event_count,
                    },
                    # A span id that already belongs to another run is never taken over.
                    where=t.spans.c.run_id == excluded.run_id,
                ).returning(t.spans.c.id)
            )
            skipped += len(chunk) - len(written.all())
        return skipped

    async def create_queued(
        self,
        *,
        run_id: uuid.UUID,
        project_id: uuid.UUID,
        trace_id: uuid.UUID,
        name: str | None,
        agent_slug: str | None,
        metadata: dict[str, object],
        now: datetime,
    ) -> tuple[bool, uuid.UUID]:
        """Create a QUEUED run; returns (created, owning project). Idempotent on the run id."""
        created = (
            await self._conn.execute(
                insert(t.runs)
                .values(
                    workspace_id=self._tenant.workspace_id,
                    id=run_id,
                    project_id=project_id,
                    trace_id=trace_id,
                    name=name,
                    agent_slug=agent_slug,
                    status="QUEUED",
                    started_at=now,
                    metadata=metadata,
                )
                .on_conflict_do_nothing(index_elements=["workspace_id", "id"])
                .returning(t.runs.c.id)
            )
        ).first()
        if created is not None:
            return True, project_id
        owner = (
            await self._conn.execute(
                select(t.runs.c.project_id).where(
                    t.runs.c.workspace_id == self._tenant.workspace_id, t.runs.c.id == run_id
                )
            )
        ).one()
        return False, owner.project_id

"""Run persistence. Runs are derived (INV-2): ingestion seeds them, the summarizer owns them."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.db import tables as t
from abb_api.tenancy import TenantContext


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

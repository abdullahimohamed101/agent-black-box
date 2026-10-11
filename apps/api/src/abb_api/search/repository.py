"""Persistence of the derived search document (tenant-scoped, INV-3, INV-7)."""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import cast, func, or_, select
from sqlalchemy.dialects.postgresql import TSVECTOR, insert
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.db import tables as t
from abb_api.search.document import SearchDocument
from abb_api.tenancy import TenantContext


@dataclass(frozen=True)
class RunFacts:
    """What the document needs from the run row (after the derivation has been applied)."""

    name: str | None
    metadata: dict[str, Any]


class RunSearchRepository:
    def __init__(self, conn: AsyncConnection, tenant: TenantContext) -> None:
        self._conn = conn
        self._tenant = tenant

    async def run_facts(self, run_id: uuid.UUID) -> RunFacts | None:
        row = (
            await self._conn.execute(
                select(t.runs.c.name, t.runs.c.metadata).where(
                    t.runs.c.workspace_id == self._tenant.workspace_id, t.runs.c.id == run_id
                )
            )
        ).first()
        return RunFacts(row.name, dict(row.metadata)) if row else None

    async def upsert_if_changed(
        self,
        *,
        run_id: uuid.UUID,
        project_id: uuid.UUID,
        started_at: datetime,
        version: int,
        document: SearchDocument,
    ) -> bool:
        """Write the document unless an identical one is stored. True if the row was written.

        The stored hash covers everything the row holds (document, project, start time, version),
        so an unchanged re-summarisation of an active run touches no row and no GIN index.
        """
        digest = hashlib.sha256(
            b"|".join(
                [
                    document.hash,
                    str(project_id).encode(),
                    started_at.isoformat().encode(),
                    str(version).encode(),
                ]
            )
        ).digest()
        values = {
            "workspace_id": self._tenant.workspace_id,
            "run_id": run_id,
            "project_id": project_id,
            "started_at": started_at,
            "summary_version": version,
            "document_hash": digest,
            "error_count": document.error_count,
            "security_events": document.security_events,
            "event_types": list(document.event_types),
            "tools": list(document.tools),
            "models": list(document.models),
            "providers": list(document.providers),
            "agents": list(document.agents),
            "tags": list(document.tags),
            "metadata_doc": cast(document.metadata_doc, TSVECTOR),
            "metadata_truncated": document.metadata_truncated,
            "file_paths": list(document.file_paths),
            "commands": list(document.commands),
            "content_doc": cast(document.content_doc, TSVECTOR),
            "content_truncated": document.content_truncated,
        }
        statement = insert(t.run_search).values(values)
        excluded = statement.excluded
        changed = {
            name: getattr(excluded, name)
            for name in values
            if name not in ("workspace_id", "run_id")
        }
        written = await self._conn.execute(
            statement.on_conflict_do_update(
                index_elements=["workspace_id", "run_id"],
                set_={**changed, "updated_at": func.now()},
                where=t.run_search.c.document_hash != excluded.document_hash,
            ).returning(t.run_search.c.run_id)
        )
        return written.first() is not None

    async def unindexed_run_ids(
        self,
        *,
        project_id: uuid.UUID | None,
        since: datetime | None,
        version: int,
        limit: int,
    ) -> list[uuid.UUID]:
        """Newest runs that were summarised but have no document of the current version."""
        r, s = t.runs.c, t.run_search.c
        statement = (
            select(r.id)
            .select_from(
                t.runs.outerjoin(
                    t.run_search,
                    (t.run_search.c.workspace_id == r.workspace_id)
                    & (t.run_search.c.run_id == r.id),
                )
            )
            .where(
                r.workspace_id == self._tenant.workspace_id,
                r.summary_version > 0,  # not yet summarised: the summarizer will index it
                or_(s.run_id.is_(None), s.summary_version < version),
            )
        )
        if project_id is not None:
            statement = statement.where(r.project_id == project_id)
        if since is not None:
            statement = statement.where(r.started_at >= since)
        rows = await self._conn.execute(statement.order_by(r.started_at.desc(), r.id).limit(limit))
        return [row.id for row in rows]

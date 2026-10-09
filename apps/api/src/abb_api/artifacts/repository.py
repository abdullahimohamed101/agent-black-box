"""Artifact metadata persistence. Every statement carries the tenant (INV-3)."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.db import tables as t
from abb_api.tenancy import TenantContext


@dataclass(frozen=True)
class ArtifactRecord:
    id: uuid.UUID
    run_id: uuid.UUID
    project_id: uuid.UUID
    kind: str
    name: str | None
    media_type: str
    size_bytes: int
    sha256: bytes
    storage_key: str
    created_at: datetime


def _record(r: Row[Any]) -> ArtifactRecord:
    return ArtifactRecord(
        id=r.id,
        run_id=r.run_id,
        project_id=r.project_id,
        kind=r.artifact_type,
        name=r.name,
        media_type=r.media_type,
        size_bytes=r.size_bytes,
        sha256=bytes(r.content_hash),
        storage_key=r.storage_uri,
        created_at=r.created_at,
    )


class ArtifactRepository:
    def __init__(self, conn: AsyncConnection, tenant: TenantContext) -> None:
        self._conn = conn
        self._tenant = tenant

    async def get(
        self, artifact_id: uuid.UUID, *, project_id: uuid.UUID | None
    ) -> ArtifactRecord | None:
        """The artifact if it is in this workspace (and project, for a project-bound key)."""
        stmt = select(t.artifacts).where(
            t.artifacts.c.workspace_id == self._tenant.workspace_id,
            t.artifacts.c.id == artifact_id,
        )
        if project_id is not None:
            stmt = stmt.where(t.artifacts.c.project_id == project_id)
        row = (await self._conn.execute(stmt)).first()
        return _record(row) if row is not None else None

    async def insert(self, record: ArtifactRecord) -> bool:
        """Insert; False if the id already exists (the caller then compares hashes)."""
        result = await self._conn.execute(
            insert(t.artifacts)
            .values(
                workspace_id=self._tenant.workspace_id,
                id=record.id,
                run_id=record.run_id,
                project_id=record.project_id,
                artifact_type=record.kind,
                name=record.name,
                media_type=record.media_type,
                storage_uri=record.storage_key,
                size_bytes=record.size_bytes,
                content_hash=record.sha256,
                created_at=record.created_at,
            )
            .on_conflict_do_nothing(index_elements=["workspace_id", "id"])
            .returning(t.artifacts.c.id)
        )
        return result.first() is not None

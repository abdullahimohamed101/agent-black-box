"""Workspace provisioning. Deliberately NOT tenant-scoped: creating a workspace has no tenant yet.

Used by the CLI and (later) sign-up flows, never by request handlers acting for a tenant.
"""

import uuid
from dataclasses import dataclass

from abb_event_schema.ids import IdKind
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.core.domain import AlreadyExistsError
from abb_api.db import tables as t
from abb_api.ids import new_uuid
from abb_api.slugs import validate_slug


@dataclass(frozen=True)
class Workspace:
    id: uuid.UUID
    name: str
    slug: str


class WorkspaceProvisioning:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def create(self, *, name: str, slug: str) -> Workspace:
        validate_slug(slug, "workspace slug")
        statement = (
            insert(t.workspaces)
            .values(id=new_uuid(IdKind.WORKSPACE), name=name, slug=slug)
            .on_conflict_do_nothing(index_elements=["slug"])
            .returning(t.workspaces.c.id, t.workspaces.c.name, t.workspaces.c.slug)
        )
        row = (await self._conn.execute(statement)).first()
        if row is None:
            raise AlreadyExistsError(f"workspace '{slug}' already exists")
        return Workspace(row.id, row.name, row.slug)

    async def get_by_slug(self, slug: str) -> Workspace | None:
        row = (
            await self._conn.execute(
                select(t.workspaces.c.id, t.workspaces.c.name, t.workspaces.c.slug).where(
                    t.workspaces.c.slug == slug
                )
            )
        ).first()
        return Workspace(row.id, row.name, row.slug) if row else None

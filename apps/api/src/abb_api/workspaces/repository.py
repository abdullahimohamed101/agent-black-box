"""Workspace membership. The role of a user in a workspace is read here and nowhere else."""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.db import tables as t
from abb_api.tenancy import TenantContext


class MembershipRepository:
    """Tenant-scoped: every query names the workspace (INV-3)."""

    def __init__(self, conn: AsyncConnection, tenant: TenantContext) -> None:
        self._conn = conn
        self._tenant = tenant

    async def role_of(self, user_id: uuid.UUID) -> str | None:
        """The user's role in this workspace, or None if they are not a member."""
        row = (
            await self._conn.execute(
                select(t.workspace_members.c.role).where(
                    t.workspace_members.c.workspace_id == self._tenant.workspace_id,
                    t.workspace_members.c.user_id == user_id,
                )
            )
        ).first()
        return str(row.role) if row else None


@dataclass(frozen=True)
class MembershipOfUser:
    workspace_id: uuid.UUID
    slug: str
    name: str
    role: str


async def memberships_of(conn: AsyncConnection, user_id: uuid.UUID) -> list[MembershipOfUser]:
    """Every workspace a user belongs to.

    The one deliberate cross-workspace read (ADR-060): it returns only the caller's own
    memberships, to build `/v1/me` and the workspace switcher, so it is not a tenant repository.
    """
    rows = await conn.execute(
        select(
            t.workspace_members.c.workspace_id,
            t.workspaces.c.slug,
            t.workspaces.c.name,
            t.workspace_members.c.role,
        )
        .join(t.workspaces, t.workspaces.c.id == t.workspace_members.c.workspace_id)
        .where(t.workspace_members.c.user_id == user_id)
        .order_by(t.workspaces.c.slug)
    )
    return [MembershipOfUser(r.workspace_id, r.slug, r.name, r.role) for r in rows]

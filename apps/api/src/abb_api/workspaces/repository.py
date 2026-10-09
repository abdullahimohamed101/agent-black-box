"""Workspace membership. The role of a user in a workspace is read here and nowhere else."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.core.domain import AlreadyExistsError
from abb_api.db import tables as t
from abb_api.tenancy import TenantContext


@dataclass(frozen=True)
class Member:
    user_id: uuid.UUID
    email: str
    name: str | None
    role: str
    joined_at: datetime


_MEMBER_COLUMNS = (
    t.workspace_members.c.user_id,
    t.users.c.email,
    t.users.c.name,
    t.workspace_members.c.role,
    t.workspace_members.c.created_at.label("joined_at"),
)


def _member(row: Any) -> Member:
    return Member(row.user_id, row.email, row.name, row.role, row.joined_at)


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

    def _scoped(self) -> Any:
        return (
            select(*_MEMBER_COLUMNS)
            .join(t.users, t.users.c.id == t.workspace_members.c.user_id)
            .where(t.workspace_members.c.workspace_id == self._tenant.workspace_id)
        )

    async def list_members(self, limit: int) -> list[Member]:
        rows = await self._conn.execute(
            self._scoped().order_by(t.workspace_members.c.created_at, t.users.c.email).limit(limit)
        )
        return [_member(r) for r in rows]

    async def get(self, user_id: uuid.UUID, *, for_update: bool = False) -> Member | None:
        statement = self._scoped().where(t.workspace_members.c.user_id == user_id)
        if for_update:
            statement = statement.with_for_update(of=t.workspace_members)
        row = (await self._conn.execute(statement)).first()
        return _member(row) if row else None

    async def count(self) -> int:
        return int(
            (
                await self._conn.execute(
                    select(func.count())
                    .select_from(t.workspace_members)
                    .where(t.workspace_members.c.workspace_id == self._tenant.workspace_id)
                )
            ).scalar_one()
        )

    async def lock_owners(self) -> list[uuid.UUID]:
        """Lock every OWNER row of the workspace, in a fixed order, and return their users.

        The last-owner rule reads and writes under this lock: two requests that would each remove
        "the other" owner queue here, and the second sees the first one's result (D6, B-4).
        """
        rows = await self._conn.execute(
            select(t.workspace_members.c.user_id)
            .where(
                t.workspace_members.c.workspace_id == self._tenant.workspace_id,
                t.workspace_members.c.role == "OWNER",
            )
            .order_by(t.workspace_members.c.user_id)
            .with_for_update()
        )
        return [r.user_id for r in rows]

    async def lock_workspace(self) -> None:
        """Serialise changes that are bounded per workspace (members, open invitations).

        `FOR NO KEY UPDATE` does not block the key-share locks that foreign keys take, so audit and
        project inserts into the same workspace are not held up.
        """
        await self._conn.execute(
            select(t.workspaces.c.id)
            .where(t.workspaces.c.id == self._tenant.workspace_id)
            .with_for_update(key_share=True)
        )

    async def add(self, user_id: uuid.UUID, role: str, *, invited_by: uuid.UUID | None) -> None:
        try:
            await self._conn.execute(
                insert(t.workspace_members).values(
                    workspace_id=self._tenant.workspace_id,
                    user_id=user_id,
                    role=role,
                    invited_by=invited_by,
                )
            )
        except IntegrityError:
            raise AlreadyExistsError("already a member") from None

    async def set_role(self, user_id: uuid.UUID, role: str, now: datetime) -> bool:
        result = await self._conn.execute(
            update(t.workspace_members)
            .where(
                t.workspace_members.c.workspace_id == self._tenant.workspace_id,
                t.workspace_members.c.user_id == user_id,
            )
            .values(role=role, updated_at=now)
        )
        return result.rowcount == 1

    async def remove(self, user_id: uuid.UUID) -> bool:
        result = await self._conn.execute(
            t.workspace_members.delete().where(
                t.workspace_members.c.workspace_id == self._tenant.workspace_id,
                t.workspace_members.c.user_id == user_id,
            )
        )
        return result.rowcount == 1


@dataclass(frozen=True)
class Invitation:
    workspace_id: uuid.UUID
    id: uuid.UUID
    email: str
    role: str
    invited_by: uuid.UUID | None
    accepted_by: uuid.UUID | None
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None

    def is_open(self) -> bool:
        return self.accepted_at is None and self.revoked_at is None


_INVITATION_COLUMNS = (
    t.invitations.c.workspace_id,
    t.invitations.c.id,
    t.invitations.c.email,
    t.invitations.c.role,
    t.invitations.c.invited_by,
    t.invitations.c.accepted_by,
    t.invitations.c.created_at,
    t.invitations.c.expires_at,
    t.invitations.c.accepted_at,
    t.invitations.c.revoked_at,
)


def _invitation(row: Any) -> Invitation:
    return Invitation(**{c.name: getattr(row, c.name) for c in _INVITATION_COLUMNS})


async def find_invitation_by_hash(conn: AsyncConnection, token_hash: bytes) -> Invitation | None:
    """The one unscoped lookup of invitations: the invitee presents the token before any workspace
    is chosen. The token is 256 random bits and only its SHA-256 is stored (ADR-060)."""
    row = (
        await conn.execute(
            select(*_INVITATION_COLUMNS).where(t.invitations.c.token_hash == token_hash)
        )
    ).first()
    return _invitation(row) if row else None


class InvitationRepository:
    def __init__(self, conn: AsyncConnection, tenant: TenantContext) -> None:
        self._conn = conn
        self._tenant = tenant

    def _scoped(self) -> Any:
        return select(*_INVITATION_COLUMNS).where(
            t.invitations.c.workspace_id == self._tenant.workspace_id
        )

    def _open(self, now: datetime) -> Any:
        return self._scoped().where(
            t.invitations.c.accepted_at.is_(None),
            t.invitations.c.revoked_at.is_(None),
            t.invitations.c.expires_at > now,
        )

    async def create(
        self,
        invitation_id: uuid.UUID,
        *,
        email: str,
        role: str,
        token_hash: bytes,
        invited_by: uuid.UUID | None,
        expires_at: datetime,
    ) -> Invitation:
        try:
            await self._conn.execute(
                insert(t.invitations).values(
                    workspace_id=self._tenant.workspace_id,
                    id=invitation_id,
                    email=email,
                    role=role,
                    token_hash=token_hash,
                    invited_by=invited_by,
                    expires_at=expires_at,
                )
            )
        except IntegrityError:
            raise AlreadyExistsError("an open invitation for that email exists") from None
        created = await self.get(invitation_id)
        assert created is not None
        return created

    async def get(self, invitation_id: uuid.UUID) -> Invitation | None:
        row = (
            await self._conn.execute(self._scoped().where(t.invitations.c.id == invitation_id))
        ).first()
        return _invitation(row) if row else None

    async def list_open(self, now: datetime, limit: int) -> list[Invitation]:
        rows = await self._conn.execute(
            self._open(now).order_by(t.invitations.c.created_at, t.invitations.c.id).limit(limit)
        )
        return [_invitation(r) for r in rows]

    async def count_open(self, now: datetime) -> int:
        counted = self._open(now).with_only_columns(func.count()).order_by(None)
        return int((await self._conn.execute(counted)).scalar_one())

    async def has_open_for(self, email: str, now: datetime) -> bool:
        found = await self._conn.execute(self._open(now).where(t.invitations.c.email == email))
        return found.first() is not None

    async def retire_expired(self, now: datetime) -> None:
        """Retire lapsed invitations: they free the one-open-per-email slot and the bound."""
        await self._conn.execute(
            update(t.invitations)
            .where(
                t.invitations.c.workspace_id == self._tenant.workspace_id,
                t.invitations.c.accepted_at.is_(None),
                t.invitations.c.revoked_at.is_(None),
                t.invitations.c.expires_at <= now,
            )
            .values(revoked_at=t.invitations.c.expires_at)
        )

    async def revoke(self, invitation_id: uuid.UUID, now: datetime) -> bool:
        result = await self._conn.execute(
            update(t.invitations)
            .where(
                t.invitations.c.workspace_id == self._tenant.workspace_id,
                t.invitations.c.id == invitation_id,
                t.invitations.c.accepted_at.is_(None),
                t.invitations.c.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        return result.rowcount == 1

    async def mark_accepted(
        self, invitation_id: uuid.UUID, user_id: uuid.UUID, now: datetime
    ) -> bool:
        result = await self._conn.execute(
            update(t.invitations)
            .where(
                t.invitations.c.workspace_id == self._tenant.workspace_id,
                t.invitations.c.id == invitation_id,
                t.invitations.c.accepted_at.is_(None),
                t.invitations.c.revoked_at.is_(None),
            )
            .values(accepted_at=now, accepted_by=user_id)
        )
        return result.rowcount == 1


@dataclass(frozen=True)
class MembershipOfUser:
    workspace_id: uuid.UUID
    slug: str
    name: str
    role: str


async def workspace_summary(
    conn: AsyncConnection, workspace_id: uuid.UUID
) -> tuple[str, str] | None:
    """(slug, name) of a workspace the caller has just been admitted to."""
    row = (
        await conn.execute(
            select(t.workspaces.c.slug, t.workspaces.c.name).where(
                t.workspaces.c.id == workspace_id
            )
        )
    ).first()
    return (row.slug, row.name) if row else None


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

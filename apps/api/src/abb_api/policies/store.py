"""The versioned policy store (INV-3, INV-7, ADR-140).

`PolicyStore` is the contract; `PgPolicyStore` implements it on `policy_versions`, an append-only
table (the runtime role cannot UPDATE or DELETE, migration 0092). A scope is a workspace-wide
base (`project_id=None`) or one project; the current policy of a scope is its highest version.
Every statement carries the workspace, and a project that is not in the workspace is
`NotFoundError` (the composite foreign key also refuses it).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from abb_event_schema.ids import IdKind
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.core.domain import NotFoundError
from abb_api.db import tables as t
from abb_api.ids import new_uuid
from abb_api.tenancy import TenantContext

Source = Literal["project", "workspace", "default"]


@dataclass(frozen=True)
class PolicyVersion:
    """A published version without its text (history lists)."""

    project_id: uuid.UUID | None
    version: int
    document_sha256: str
    rule_count: int
    created_at: datetime
    created_by: str
    comment: str | None


@dataclass(frozen=True)
class StoredPolicy(PolicyVersion):
    document_text: str


class PolicyStore(Protocol):
    async def lock_scope(self, project_id: uuid.UUID | None) -> None: ...

    async def latest(self, project_id: uuid.UUID | None) -> StoredPolicy | None: ...

    async def get(self, project_id: uuid.UUID | None, version: int) -> StoredPolicy | None: ...

    async def history(
        self, project_id: uuid.UUID | None, *, limit: int, before_version: int | None = None
    ) -> list[PolicyVersion]: ...

    async def append(
        self,
        project_id: uuid.UUID | None,
        *,
        document_text: str,
        document_sha256: str,
        rule_count: int,
        created_by: str,
        comment: str | None,
    ) -> StoredPolicy: ...

    async def effective(
        self, project_id: uuid.UUID | None
    ) -> tuple[Source, StoredPolicy | None]: ...

    async def for_decision(self, project_id: uuid.UUID) -> tuple[int, str] | None: ...


_META = (
    t.policy_versions.c.project_id,
    t.policy_versions.c.version,
    t.policy_versions.c.document_sha256,
    t.policy_versions.c.rule_count,
    t.policy_versions.c.created_at,
    t.policy_versions.c.created_by,
    t.policy_versions.c.comment,
)


class PgPolicyStore:
    def __init__(self, conn: AsyncConnection, tenant: TenantContext) -> None:
        self._conn = conn
        self._tenant = tenant

    def _scope(self, project_id: uuid.UUID | None) -> list[ColumnElement[bool]]:
        c = t.policy_versions.c
        return [
            c.workspace_id == self._tenant.workspace_id,
            c.project_id.is_(None) if project_id is None else c.project_id == project_id,
        ]

    async def _require_project(self, project_id: uuid.UUID | None) -> None:
        if project_id is None:
            return
        found = await self._conn.execute(
            select(t.projects.c.id).where(
                t.projects.c.workspace_id == self._tenant.workspace_id,
                t.projects.c.id == project_id,
            )
        )
        if found.first() is None:
            raise NotFoundError("project not found")

    async def lock_scope(self, project_id: uuid.UUID | None) -> None:
        """Serialise publishers of one scope for the rest of the transaction."""
        await self._require_project(project_id)
        key = f"ws:{self._tenant.workspace_id}:policy:{project_id or 'base'}"
        await self._conn.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0))))

    async def latest(self, project_id: uuid.UUID | None) -> StoredPolicy | None:
        await self._require_project(project_id)
        row = (
            await self._conn.execute(
                select(*_META, t.policy_versions.c.document_text)
                .where(*self._scope(project_id))
                .order_by(t.policy_versions.c.version.desc())
                .limit(1)
            )
        ).first()
        return StoredPolicy(**row._asdict()) if row else None

    async def get(self, project_id: uuid.UUID | None, version: int) -> StoredPolicy | None:
        await self._require_project(project_id)
        row = (
            await self._conn.execute(
                select(*_META, t.policy_versions.c.document_text).where(
                    *self._scope(project_id), t.policy_versions.c.version == version
                )
            )
        ).first()
        return StoredPolicy(**row._asdict()) if row else None

    async def history(
        self, project_id: uuid.UUID | None, *, limit: int, before_version: int | None = None
    ) -> list[PolicyVersion]:
        await self._require_project(project_id)
        statement = select(*_META).where(*self._scope(project_id))
        if before_version is not None:
            statement = statement.where(t.policy_versions.c.version < before_version)
        rows = await self._conn.execute(
            statement.order_by(t.policy_versions.c.version.desc()).limit(limit)
        )
        return [PolicyVersion(**r._asdict()) for r in rows]

    async def append(
        self,
        project_id: uuid.UUID | None,
        *,
        document_text: str,
        document_sha256: str,
        rule_count: int,
        created_by: str,
        comment: str | None,
    ) -> StoredPolicy:
        """Insert the next version. The caller holds `lock_scope`, so the number is race free;
        the unique index is the backstop."""
        await self._require_project(project_id)
        c = t.policy_versions.c
        newest = await self._conn.scalar(
            select(func.max(c.version)).where(*self._scope(project_id))
        )
        row = (
            await self._conn.execute(
                t.policy_versions.insert()
                .values(
                    workspace_id=self._tenant.workspace_id,
                    id=new_uuid(IdKind.POLICY),
                    project_id=project_id,
                    version=(newest or 0) + 1,
                    document_text=document_text,
                    document_sha256=document_sha256,
                    rule_count=rule_count,
                    created_by=created_by,
                    comment=comment,
                )
                .returning(*_META, c.document_text)
            )
        ).one()
        return StoredPolicy(**row._asdict())

    async def effective(self, project_id: uuid.UUID | None) -> tuple[Source, StoredPolicy | None]:
        """The policy that governs a project: its own, else the workspace base, else none (the
        built-in empty policy). With no project, the workspace base."""
        if project_id is not None:
            own = await self.latest(project_id)
            if own is not None:
                return "project", own
        base = await self.latest(None)
        if base is not None:
            return "workspace", base
        return "default", None

    async def for_decision(self, project_id: uuid.UUID) -> tuple[int, str] | None:
        """`(version, document_text)` of the policy that governs a project, or None (nothing
        published). The decision endpoint's read: no project check (the caller's key already
        bound the project), no metadata, at most two indexed single-row statements."""
        c = t.policy_versions.c
        for scope in (c.project_id == project_id, c.project_id.is_(None)):
            row = (
                await self._conn.execute(
                    select(c.version, c.document_text)
                    .where(c.workspace_id == self._tenant.workspace_id, scope)
                    .order_by(c.version.desc())
                    .limit(1)
                )
            ).first()
            if row is not None:
                return int(row.version), str(row.document_text)
        return None

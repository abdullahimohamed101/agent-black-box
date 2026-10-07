"""API key persistence.

`ApiKeyLookup.find` is the one unscoped query in the system: a request presents a key before its
tenant is known. Everything else here is tenant-scoped.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.auth.keys import GeneratedKey, generate_key
from abb_api.auth.scopes import ALL_SCOPES
from abb_api.core.domain import NotFoundError
from abb_api.db import tables as t
from abb_api.projects.repository import ProjectRepository
from abb_api.tenancy import TenantContext

LAST_USED_RESOLUTION = timedelta(minutes=1)


@dataclass(frozen=True)
class StoredApiKey:
    id: uuid.UUID
    key_id: str
    workspace_id: uuid.UUID
    project_id: uuid.UUID | None
    secret_hash: bytes
    scopes: frozenset[str]
    name: str | None
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True)
class CreatedApiKey:
    stored: StoredApiKey
    token: str  # the only time the plaintext exists


_COLUMNS = (
    t.api_keys.c.id,
    t.api_keys.c.key_id,
    t.api_keys.c.workspace_id,
    t.api_keys.c.project_id,
    t.api_keys.c.secret_hash,
    t.api_keys.c.scopes,
    t.api_keys.c.name,
    t.api_keys.c.created_at,
    t.api_keys.c.last_used_at,
    t.api_keys.c.expires_at,
    t.api_keys.c.revoked_at,
)


def _stored(row: Any) -> StoredApiKey:
    return StoredApiKey(
        id=row.id,
        key_id=row.key_id,
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        secret_hash=bytes(row.secret_hash),
        scopes=frozenset(row.scopes),
        name=row.name,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
    )


class ApiKeyLookup:
    """Pre-authentication lookup by public key id. Not tenant-scoped, on purpose."""

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def find(self, key_id: str) -> StoredApiKey | None:
        row = (
            await self._conn.execute(select(*_COLUMNS).where(t.api_keys.c.key_id == key_id))
        ).first()
        return _stored(row) if row else None

    async def touch_last_used(self, key: StoredApiKey, now: datetime) -> None:
        """Record use at most once a minute, so ingestion does not write on every request."""
        await self._conn.execute(
            update(t.api_keys)
            .where(
                t.api_keys.c.id == key.id,
                (t.api_keys.c.last_used_at.is_(None))
                | (t.api_keys.c.last_used_at < now - LAST_USED_RESOLUTION),
            )
            .values(last_used_at=now)
        )


class ApiKeyRepository:
    def __init__(self, conn: AsyncConnection, tenant: TenantContext) -> None:
        self._conn = conn
        self._tenant = tenant

    async def create(
        self,
        *,
        scopes: frozenset[str],
        project_id: uuid.UUID | None = None,
        name: str | None = None,
        expires_at: datetime | None = None,
        created_by: uuid.UUID | None = None,
    ) -> CreatedApiKey:
        unknown = scopes - ALL_SCOPES
        if unknown:
            raise ValueError(f"unknown scopes: {', '.join(sorted(unknown))}")
        if (
            project_id is not None
            and await ProjectRepository(self._conn, self._tenant).get(project_id) is None
        ):
            raise NotFoundError("project not found in this workspace")
        generated: GeneratedKey = generate_key()
        row_id = uuid.uuid4()
        await self._conn.execute(
            t.api_keys.insert().values(
                id=row_id,
                key_id=generated.key_id,
                workspace_id=self._tenant.workspace_id,
                project_id=project_id,
                secret_hash=generated.secret_hash,
                scopes=sorted(scopes),
                name=name,
                created_by=created_by,
                expires_at=expires_at,
            )
        )
        stored = await ApiKeyLookup(self._conn).find(generated.key_id)
        assert stored is not None
        return CreatedApiKey(stored=stored, token=generated.token)

    async def list(self) -> list[StoredApiKey]:
        rows = await self._conn.execute(
            select(*_COLUMNS)
            .where(t.api_keys.c.workspace_id == self._tenant.workspace_id)
            .order_by(t.api_keys.c.created_at, t.api_keys.c.key_id)
        )
        return [_stored(r) for r in rows]

    async def revoke(self, key_id: str, now: datetime) -> bool:
        """Revoke a key of this tenant. False if there is no such active key here."""
        result = await self._conn.execute(
            update(t.api_keys)
            .where(
                t.api_keys.c.workspace_id == self._tenant.workspace_id,
                t.api_keys.c.key_id == key_id,
                t.api_keys.c.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        return result.rowcount == 1

"""API key persistence.

`ApiKeyLookup.find` is the one unscoped query in the system: a request presents a key before its
tenant is known. Everything else here is tenant-scoped.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from abb_event_schema.ids import IdKind
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.auth.keys import GeneratedKey, generate_key
from abb_api.auth.scopes import ALL_SCOPES
from abb_api.core.domain import AlreadyExistsError, NotFoundError
from abb_api.db import tables as t
from abb_api.ids import new_uuid
from abb_api.projects.repository import ProjectRepository
from abb_api.tenancy import TenantContext
from abb_api.workspaces.repository import lock_workspace

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
    created_by: uuid.UUID | None
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None

    def is_active(self, now: datetime) -> bool:
        return self.revoked_at is None and (self.expires_at is None or self.expires_at > now)


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
    t.api_keys.c.created_by,
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
        created_by=row.created_by,
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

    async def list_current(self, limit: int) -> Sequence[StoredApiKey]:
        """Keys that are not revoked (active or expired), oldest first."""
        rows = await self._conn.execute(
            select(*_COLUMNS)
            .where(
                t.api_keys.c.workspace_id == self._tenant.workspace_id,
                t.api_keys.c.revoked_at.is_(None),
            )
            .order_by(t.api_keys.c.created_at, t.api_keys.c.key_id)
            .limit(limit)
        )
        return [_stored(r) for r in rows]

    async def get(self, key_id: str) -> StoredApiKey | None:
        """A key of this tenant by its public id (revoked ones included)."""
        row = (
            await self._conn.execute(
                select(*_COLUMNS).where(
                    t.api_keys.c.workspace_id == self._tenant.workspace_id,
                    t.api_keys.c.key_id == key_id,
                )
            )
        ).first()
        return _stored(row) if row else None

    async def count_active(self, now: datetime) -> int:
        return int(
            (
                await self._conn.execute(
                    select(func.count())
                    .select_from(t.api_keys)
                    .where(
                        t.api_keys.c.workspace_id == self._tenant.workspace_id,
                        t.api_keys.c.revoked_at.is_(None),
                        (t.api_keys.c.expires_at.is_(None)) | (t.api_keys.c.expires_at > now),
                    )
                )
            ).scalar_one()
        )

    async def lock_for_create(self) -> None:
        """Serialise key creation per workspace, so the bound cannot be raced past."""
        await lock_workspace(self._conn, self._tenant)

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


# ------------------------------------------------ people: users, sessions, login state
#
# Unscoped by design, like `ApiKeyLookup`: a person (and the session they present) exists before any
# workspace is chosen, and one person may belong to several. None of these tables holds tenant data.


@dataclass(frozen=True)
class UserRecord:
    id: uuid.UUID
    email: str
    name: str | None
    provider: str | None
    provider_subject: str | None
    email_verified_at: datetime | None
    last_login_at: datetime | None


_USER_COLUMNS = (
    t.users.c.id,
    t.users.c.email,
    t.users.c.name,
    t.users.c.provider,
    t.users.c.provider_subject,
    t.users.c.email_verified_at,
    t.users.c.last_login_at,
)


def _user(row: Any) -> UserRecord:
    return UserRecord(**{c.name: getattr(row, c.name) for c in _USER_COLUMNS})


class UserRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def get(self, user_id: uuid.UUID) -> UserRecord | None:
        row = (
            await self._conn.execute(select(*_USER_COLUMNS).where(t.users.c.id == user_id))
        ).first()
        return _user(row) if row else None

    async def find_by_identity(self, provider: str, subject: str) -> UserRecord | None:
        row = (
            await self._conn.execute(
                select(*_USER_COLUMNS).where(
                    t.users.c.provider == provider, t.users.c.provider_subject == subject
                )
            )
        ).first()
        return _user(row) if row else None

    async def find_by_email(self, email: str) -> UserRecord | None:
        row = (
            await self._conn.execute(
                select(*_USER_COLUMNS).where(func.lower(t.users.c.email) == email.lower())
            )
        ).first()
        return _user(row) if row else None

    async def create(self, *, email: str, name: str | None = None) -> UserRecord:
        """A user with no identity yet (pre-provisioned by the CLI or an invitation)."""
        user_id = new_uuid(IdKind.USER)
        try:
            await self._conn.execute(
                insert(t.users).values(id=user_id, email=email.lower(), name=name)
            )
        except IntegrityError:
            raise AlreadyExistsError("a user with that email already exists") from None
        created = await self.get(user_id)
        assert created is not None
        return created

    async def link_identity(
        self, user_id: uuid.UUID, *, provider: str, subject: str, verified_at: datetime
    ) -> bool:
        """Record the identity of a user that has none. False if the user is already linked.

        `provider_subject IS NULL` is part of the statement, so two concurrent logins cannot both
        link, and a user bound to one subject can never be re-bound by an email match (ADR-060).
        """
        result = await self._conn.execute(
            update(t.users)
            .where(t.users.c.id == user_id, t.users.c.provider_subject.is_(None))
            .values(provider=provider, provider_subject=subject, email_verified_at=verified_at)
        )
        return result.rowcount == 1

    async def record_login(self, user_id: uuid.UUID, now: datetime) -> None:
        await self._conn.execute(
            update(t.users).where(t.users.c.id == user_id).values(last_login_at=now)
        )

    async def clear_identity(self, user_id: uuid.UUID) -> bool:
        """The audited re-link path after an issuer change (`relink-user`)."""
        result = await self._conn.execute(
            update(t.users)
            .where(t.users.c.id == user_id)
            .values(provider=None, provider_subject=None, email_verified_at=None)
        )
        return result.rowcount == 1


@dataclass(frozen=True)
class SessionRecord:
    id: uuid.UUID
    user_id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    idle_expires_at: datetime
    revoked_at: datetime | None

    def usable(self, now: datetime) -> bool:
        return self.revoked_at is None and now < self.expires_at and now < self.idle_expires_at


_SESSION_COLUMNS = (
    t.sessions.c.id,
    t.sessions.c.user_id,
    t.sessions.c.created_at,
    t.sessions.c.last_seen_at,
    t.sessions.c.expires_at,
    t.sessions.c.idle_expires_at,
    t.sessions.c.revoked_at,
)

PURGE_BATCH = 100  # rows deleted per login: bounded work, and the table cannot grow without limit
SESSION_SLIDE_RESOLUTION = timedelta(minutes=5)


class SessionRepository:
    """Sessions are found by the SHA-256 of the cookie value; the cookie itself is never stored."""

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def create(
        self,
        user_id: uuid.UUID,
        token_hash: bytes,
        *,
        now: datetime,
        absolute: timedelta,
        idle: timedelta,
    ) -> SessionRecord:
        session_id = uuid.uuid4()
        await self._conn.execute(
            insert(t.sessions).values(
                id=session_id,
                user_id=user_id,
                token_hash=token_hash,
                created_at=now,
                last_seen_at=now,
                expires_at=now + absolute,
                idle_expires_at=min(now + idle, now + absolute),
            )
        )
        row = (
            await self._conn.execute(select(*_SESSION_COLUMNS).where(t.sessions.c.id == session_id))
        ).one()
        return SessionRecord(**{c.name: getattr(row, c.name) for c in _SESSION_COLUMNS})

    async def find_by_hash(self, token_hash: bytes) -> SessionRecord | None:
        row = (
            await self._conn.execute(
                select(*_SESSION_COLUMNS).where(t.sessions.c.token_hash == token_hash)
            )
        ).first()
        return (
            SessionRecord(**{c.name: getattr(row, c.name) for c in _SESSION_COLUMNS})
            if row
            else None
        )

    async def slide(self, session: SessionRecord, *, now: datetime, idle: timedelta) -> None:
        """Extend the idle deadline at most once per resolution window; never past expiry."""
        if now - session.last_seen_at < SESSION_SLIDE_RESOLUTION:
            return
        await self._conn.execute(
            update(t.sessions)
            .where(t.sessions.c.id == session.id, t.sessions.c.revoked_at.is_(None))
            .values(last_seen_at=now, idle_expires_at=min(now + idle, session.expires_at))
        )

    async def revoke(self, session_id: uuid.UUID, now: datetime) -> bool:
        result = await self._conn.execute(
            update(t.sessions)
            .where(t.sessions.c.id == session_id, t.sessions.c.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        return result.rowcount == 1

    async def revoke_all(self, user_id: uuid.UUID, now: datetime) -> int:
        result = await self._conn.execute(
            update(t.sessions)
            .where(t.sessions.c.user_id == user_id, t.sessions.c.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        return int(result.rowcount)

    async def purge(self, user_id: uuid.UUID, now: datetime) -> int:
        """Delete this user's dead sessions and a bounded batch of anyone's expired ones."""
        dead = (
            (t.sessions.c.revoked_at.is_not(None))
            | (t.sessions.c.expires_at <= now)
            | (t.sessions.c.idle_expires_at <= now)
        )
        mine = await self._conn.execute(
            delete(t.sessions).where(t.sessions.c.user_id == user_id, dead)
        )
        stale = (
            select(t.sessions.c.id)
            .where(t.sessions.c.expires_at <= now)
            .limit(PURGE_BATCH)
            .scalar_subquery()
        )
        others = await self._conn.execute(delete(t.sessions).where(t.sessions.c.id.in_(stale)))
        return int(mine.rowcount) + int(others.rowcount)


@dataclass(frozen=True)
class LoginState:
    nonce: str
    code_verifier: str
    return_to: str


class LoginStateRepository:
    """One row per started login, keyed by the SHA-256 of `state`; single use, short-lived."""

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def create(
        self,
        state_hash: bytes,
        *,
        nonce: str,
        code_verifier: str,
        return_to: str,
        now: datetime,
        ttl: timedelta,
    ) -> None:
        stale = (
            select(t.login_states.c.state_hash)
            .where(t.login_states.c.expires_at <= now)
            .limit(PURGE_BATCH)
            .scalar_subquery()
        )
        await self._conn.execute(
            delete(t.login_states).where(t.login_states.c.state_hash.in_(stale))
        )
        await self._conn.execute(
            insert(t.login_states).values(
                state_hash=state_hash,
                nonce=nonce,
                code_verifier=code_verifier,
                return_to=return_to,
                created_at=now,
                expires_at=now + ttl,
            )
        )

    async def take(self, state_hash: bytes, now: datetime) -> LoginState | None:
        """Consume the row (replay finds nothing). An expired row is deleted and not returned."""
        row = (
            await self._conn.execute(
                delete(t.login_states)
                .where(t.login_states.c.state_hash == state_hash)
                .returning(
                    t.login_states.c.nonce,
                    t.login_states.c.code_verifier,
                    t.login_states.c.return_to,
                    t.login_states.c.expires_at,
                )
            )
        ).first()
        if row is None or row.expires_at <= now:
            return None
        return LoginState(row.nonce, row.code_verifier, row.return_to)

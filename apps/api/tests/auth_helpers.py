"""Seed people for tests: users, memberships and ready-made sessions (no identity provider)."""

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import insert, update
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.auth.repository import SessionRepository, UserRecord, UserRepository
from abb_api.auth.service import hash_session_token
from abb_api.db import tables as t


async def add_member(
    engine: AsyncEngine,
    workspace_id: uuid.UUID,
    email: str,
    role: str | None,
    *,
    verified: bool = False,
) -> UserRecord:
    """The user (created on first use) as a member of the workspace; `role=None` adds no row.

    `verified=True` records a verified email (what a completed OIDC login does), which accepting
    an invitation requires.
    """
    async with engine.begin() as conn:
        users = UserRepository(conn)
        user = await users.find_by_email(email) or await users.create(email=email)
        if verified:
            await conn.execute(
                update(t.users)
                .where(t.users.c.id == user.id)
                .values(email_verified_at=datetime.now(UTC))
            )
        if role is not None:
            await conn.execute(
                insert(t.workspace_members).values(
                    workspace_id=workspace_id, user_id=user.id, role=role
                )
            )
    return user


async def set_role(
    engine: AsyncEngine, workspace_id: uuid.UUID, user_id: uuid.UUID, role: str
) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            update(t.workspace_members)
            .where(
                t.workspace_members.c.workspace_id == workspace_id,
                t.workspace_members.c.user_id == user_id,
            )
            .values(role=role)
        )


async def remove_member(engine: AsyncEngine, workspace_id: uuid.UUID, user_id: uuid.UUID) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            t.workspace_members.delete().where(
                t.workspace_members.c.workspace_id == workspace_id,
                t.workspace_members.c.user_id == user_id,
            )
        )


async def mint_session(
    engine: AsyncEngine,
    user_id: uuid.UUID,
    now: datetime,
    *,
    absolute: timedelta = timedelta(hours=168),
    idle: timedelta = timedelta(hours=24),
) -> str:
    """A session for `user_id`; returns the cookie value."""
    token = secrets.token_urlsafe(32)
    async with engine.begin() as conn:
        await SessionRepository(conn).create(
            user_id, hash_session_token(token), now=now, absolute=absolute, idle=idle
        )
    return token

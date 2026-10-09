"""Users, sessions and login state against a real database (migration 0045, ADR-060)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.auth.keys import hash_secret
from abb_api.auth.repository import (
    SESSION_SLIDE_RESOLUTION,
    LoginStateRepository,
    SessionRepository,
    UserRepository,
)
from abb_api.core.domain import AlreadyExistsError

T0 = datetime(2026, 10, 9, 12, 0, 0, tzinfo=UTC)
H = timedelta(hours=1)
TEN_MINUTES = timedelta(minutes=10)


def digest(label: str) -> bytes:
    return hash_secret(label)  # 32 bytes, like a session or state hash


async def test_a_user_is_created_lower_cased_and_unique_by_email(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        users = UserRepository(conn)
        user = await users.create(email="Ada@Example.COM", name="Ada")
        assert user.email == "ada@example.com" and user.provider is None
        assert (await users.find_by_email("ADA@example.com")) == user
        with pytest.raises(AlreadyExistsError):
            await UserRepository(conn).create(email="ada@example.com")


async def test_an_identity_links_once_and_never_by_email_after_that(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        users = UserRepository(conn)
        user = await users.create(email="a@example.com")
        idp = "https://idp"
        assert await users.link_identity(user.id, provider=idp, subject="s1", verified_at=T0)
        # A second link attempt (another subject, or a race) changes nothing.
        assert not await users.link_identity(user.id, provider=idp, subject="s2", verified_at=T0)
        found = await users.find_by_identity(idp, "s1")
        assert found is not None and found.id == user.id and found.provider_subject == "s1"
        assert await users.find_by_identity(idp, "s2") is None
        assert await users.clear_identity(user.id)
        assert await users.link_identity(user.id, provider=idp, subject="s2", verified_at=T0)


async def test_the_database_rejects_half_an_identity_and_duplicate_subjects(
    engine: AsyncEngine,
) -> None:
    async with engine.begin() as conn:
        await UserRepository(conn).create(email="a@example.com")
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE users SET provider = 'https://idp'"))
    async with engine.begin() as conn:
        users = UserRepository(conn)
        b = await users.create(email="b@example.com")
        c = await users.create(email="c@example.com")
        await users.link_identity(b.id, provider="https://idp", subject="s", verified_at=T0)
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await UserRepository(conn).link_identity(
                c.id, provider="https://idp", subject="s", verified_at=T0
            )


async def test_sessions_validate_slide_expire_and_revoke(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        user = await UserRepository(conn).create(email="a@example.com")
        sessions = SessionRepository(conn)
        created = await sessions.create(
            user.id, digest("tok"), now=T0, absolute=timedelta(hours=10), idle=H
        )
        assert created.usable(T0) and created.idle_expires_at == T0 + H
        found = await sessions.find_by_hash(digest("tok"))
        assert found == created and await sessions.find_by_hash(digest("other")) is None

        # Within the resolution nothing is written; afterwards the idle deadline moves.
        await sessions.slide(found, now=T0 + SESSION_SLIDE_RESOLUTION / 2, idle=H)
        assert (await sessions.find_by_hash(digest("tok"))) == created
        later = T0 + SESSION_SLIDE_RESOLUTION + timedelta(seconds=1)
        await sessions.slide(created, now=later, idle=H)
        slid = await sessions.find_by_hash(digest("tok"))
        assert slid is not None and slid.idle_expires_at == later + H
        # The idle deadline never passes the absolute one.
        late = T0 + timedelta(hours=9, minutes=50)
        await sessions.slide(slid, now=late, idle=H)
        capped = await sessions.find_by_hash(digest("tok"))
        assert capped is not None and capped.idle_expires_at == created.expires_at

        assert capped.usable(late) and not capped.usable(created.expires_at)
        assert await sessions.revoke(created.id, late)
        assert not await sessions.revoke(created.id, late)  # already revoked
        revoked = await sessions.find_by_hash(digest("tok"))
        assert revoked is not None and not revoked.usable(late)


async def test_revoke_all_and_purge_are_per_user_and_expiry_driven(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        users, sessions = UserRepository(conn), SessionRepository(conn)
        a = await users.create(email="a@example.com")
        b = await users.create(email="b@example.com")
        for i in range(3):
            await sessions.create(a.id, digest(f"a{i}"), now=T0, absolute=H, idle=H)
        await sessions.create(b.id, digest("b0"), now=T0, absolute=H, idle=H)
        assert await sessions.revoke_all(a.id, T0) == 3
        # Purge removes a's dead sessions, never b's live one.
        assert await sessions.purge(a.id, T0) == 3
        assert await sessions.find_by_hash(digest("b0")) is not None
        # Expired sessions of other users go too (a bounded batch per call).
        assert await sessions.purge(a.id, T0 + 2 * H) == 1
        assert await sessions.find_by_hash(digest("b0")) is None


async def test_login_state_is_single_use_and_expires(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        states = LoginStateRepository(conn)
        await states.create(
            digest("s"), nonce="n", code_verifier="v", return_to="/w/a", now=T0, ttl=TEN_MINUTES
        )
        taken = await states.take(digest("s"), T0 + timedelta(minutes=1))
        assert taken is not None
        assert (taken.nonce, taken.code_verifier, taken.return_to) == ("n", "v", "/w/a")
        assert await states.take(digest("s"), T0) is None  # replay finds nothing
        await states.create(
            digest("old"), nonce="n", code_verifier="v", return_to="/", now=T0, ttl=TEN_MINUTES
        )
        assert await states.take(digest("old"), T0 + timedelta(minutes=11)) is None  # expired


async def test_creating_login_state_purges_expired_rows(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        states = LoginStateRepository(conn)
        for i in range(5):
            await states.create(
                digest(f"x{i}"),
                nonce="n",
                code_verifier="v",
                return_to="/",
                now=T0,
                ttl=TEN_MINUTES,
            )
        await states.create(
            digest("new"), nonce="n", code_verifier="v", return_to="/", now=T0 + H, ttl=TEN_MINUTES
        )
        count = (await conn.execute(text("SELECT count(*) FROM login_states"))).scalar_one()
    assert count == 1

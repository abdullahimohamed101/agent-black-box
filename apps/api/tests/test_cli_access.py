"""CLI access management: members, identity re-link, dev sessions, the dev owner (D14, D16)."""

import getpass
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.audit.repository import AuditRepository
from abb_api.auth.repository import SessionRepository, UserRepository
from abb_api.auth.service import authenticate_session, hash_session_token
from abb_api.core.config import Settings
from abb_api.core.errors import AppError
from abb_api.main import create_app
from abb_api.tenancy import TenantContext
from abb_api.workspaces import WorkspaceProvisioning
from tests.conftest import make_settings
from tests.test_auth_service import T0, Tick
from tests.test_cli import invoke

OPERATOR = f"cli:{getpass.getuser()}"[:128]


async def workspace_id(engine: AsyncEngine, slug: str = "acme"):  # type: ignore[no-untyped-def]
    async with engine.connect() as conn:
        found = await WorkspaceProvisioning(conn).get_by_slug(slug)
    assert found is not None
    return found.id


@pytest.fixture
async def acme(database_url: str, engine: AsyncEngine) -> Settings:
    settings = make_settings(database_url)
    assert (await invoke(settings, "create-workspace", "--name", "Acme", "--slug", "acme"))[0] == 0
    return settings


async def audit_actions(engine: AsyncEngine, slug: str = "acme") -> list[tuple[str, str]]:
    async with engine.connect() as conn:
        rows = await AuditRepository(conn, TenantContext(await workspace_id(engine, slug))).recent(
            100
        )
    return [(r.action, r.actor_id) for r in reversed(rows)]


# ---------------------------------------------------------------- members


async def test_add_list_and_remove_members_with_audit(acme: Settings, engine: AsyncEngine) -> None:
    code, _, err = await invoke(
        acme,
        "add-member",
        "--workspace",
        "acme",
        "--email",
        " Boss@Example.COM ",
        "--role",
        "OWNER",
    )
    assert code == 0, err
    second = ("add-member", "--workspace", "acme", "--email", "dev@example.com")
    assert (await invoke(acme, *second, "--role", "DEVELOPER"))[0] == 0
    code, out, _ = await invoke(acme, "list-members", "--workspace", "acme")
    lines = sorted(out.strip().splitlines())
    assert code == 0 and [ln.split()[:2] for ln in lines] == [
        ["boss@example.com", "OWNER"], ["dev@example.com", "DEVELOPER"],
    ]  # fmt: skip
    assert all(ln.split()[2].startswith("usr_") for ln in lines)

    code, _, _ = await invoke(
        acme, "remove-member", "--workspace", "acme", "--email", "dev@example.com"
    )
    assert code == 0
    _, out, _ = await invoke(acme, "list-members", "--workspace", "acme")
    assert "dev@example.com" not in out
    got = await audit_actions(engine)
    assert [a for a, _ in got if a.startswith("member.")] == [
        "member.add", "member.add", "member.remove",
    ]  # fmt: skip
    assert {actor for a, actor in got if a.startswith("member.")} == {OPERATOR}
    async with engine.connect() as conn:
        details = (await conn.execute(text("SELECT details::text FROM audit_log"))).scalars().all()
    assert not [d for d in details if "@" in d]  # no email addresses in the audit log


async def test_add_member_refuses_duplicates_bad_input_and_unknown_workspaces(
    acme: Settings,
) -> None:
    args = ("add-member", "--workspace", "acme", "--email", "a@example.com", "--role", "VIEWER")
    assert (await invoke(acme, *args))[0] == 0
    code, _, err = await invoke(acme, *args)
    assert code == 2 and "already a member" in err and "Traceback" not in err
    code, _, err = await invoke(
        acme, "add-member", "--workspace", "acme", "--email", "nope", "--role", "VIEWER"
    )
    assert code == 2 and "valid email" in err
    code, _, err = await invoke(
        acme, "add-member", "--workspace", "ghost", "--email", "a@example.com", "--role", "VIEWER"
    )
    assert code == 1 and "not found" in err
    with pytest.raises(SystemExit):  # argparse: not one of the six roles
        await invoke(
            acme, "add-member", "--workspace", "acme", "--email", "a@b.co", "--role", "ROOT"
        )


async def test_the_last_owner_cannot_be_removed(acme: Settings, engine: AsyncEngine) -> None:
    for email, role in (("o1@example.com", "OWNER"), ("v@example.com", "VIEWER")):
        await invoke(acme, "add-member", "--workspace", "acme", "--email", email, "--role", role)
    code, _, err = await invoke(
        acme, "remove-member", "--workspace", "acme", "--email", "o1@example.com"
    )
    assert code == 1 and "at least one owner" in err
    await invoke(
        acme, "add-member", "--workspace", "acme", "--email", "o2@example.com", "--role", "OWNER"
    )
    assert (
        await invoke(acme, "remove-member", "--workspace", "acme", "--email", "o1@example.com")
    )[0] == 0
    code, _, err = await invoke(
        acme, "remove-member", "--workspace", "acme", "--email", "o1@example.com"
    )
    assert code == 1 and "not a member" in err
    code, _, err = await invoke(
        acme, "remove-member", "--workspace", "acme", "--email", "who@example.com"
    )
    assert code == 1 and "no user" in err


async def test_a_user_in_two_workspaces_keeps_the_other_membership(
    acme: Settings, engine: AsyncEngine
) -> None:
    await invoke(acme, "create-workspace", "--name", "Globex", "--slug", "globex")
    for slug in ("acme", "globex"):
        await invoke(
            acme,
            "add-member",
            "--workspace",
            slug,
            "--email",
            "both@example.com",
            "--role",
            "OWNER",
        )
        await invoke(
            acme,
            "add-member",
            "--workspace",
            slug,
            "--email",
            "second@example.com",
            "--role",
            "OWNER",
        )
    await invoke(acme, "remove-member", "--workspace", "acme", "--email", "both@example.com")
    _, out, _ = await invoke(acme, "list-members", "--workspace", "globex")
    assert "both@example.com" in out
    assert ("member.remove", OPERATOR) not in await audit_actions(engine, "globex")


# ---------------------------------------------------------------- relink


async def test_relink_clears_the_identity_ends_sessions_and_is_audited(
    acme: Settings, engine: AsyncEngine
) -> None:
    await invoke(
        acme, "add-member", "--workspace", "acme", "--email", "r@example.com", "--role", "OWNER"
    )
    async with engine.begin() as conn:
        users = UserRepository(conn)
        user = await users.find_by_email("r@example.com")
        assert user is not None
        assert await users.link_identity(
            user.id, provider="https://old", subject="s-1", verified_at=T0
        )
        await SessionRepository(conn).create(
            user.id, hash_session_token("x" * 43), now=T0,
            absolute=timedelta(hours=1),
            idle=timedelta(hours=1),
        )  # fmt: skip
    with pytest.raises(SystemExit):  # the flag is required: nothing happens implicitly
        await invoke(acme, "relink-user", "--email", "r@example.com")
    code, _, err = await invoke(acme, "relink-user", "--email", "r@example.com", "--clear-subject")
    assert code == 0 and "revoked 1 session" in err
    async with engine.connect() as conn:
        cleared = await UserRepository(conn).find_by_email("r@example.com")
        assert cleared and cleared.provider is None and cleared.provider_subject is None
        with pytest.raises(AppError):
            await authenticate_session(conn, "x" * 43, Tick(), timedelta(hours=1))
    assert ("user.relink", OPERATOR) in await audit_actions(engine)
    code, _, err = await invoke(
        acme, "relink-user", "--email", "ghost@example.com", "--clear-subject"
    )
    assert code == 1


# ---------------------------------------------------------------- dev sessions


def dev(database_url: str, **overrides: object) -> Settings:
    return make_settings(database_url, allow_dev_sessions=True, **overrides)


async def test_create_session_mints_a_working_cookie_once(
    acme: Settings, database_url: str, engine: AsyncEngine
) -> None:
    await invoke(
        acme, "add-member", "--workspace", "acme", "--email", "s@example.com", "--role", "VIEWER"
    )
    code, out, err = await invoke(
        dev(database_url), "create-session", "--email", "s@example.com", "--hours", "2"
    )
    assert code == 0
    token = out.strip()
    assert len(token) == 43 and token not in err  # the secret goes to stdout only
    async with engine.connect() as conn:
        context = await authenticate_session(conn, token, Tick(), timedelta(hours=24))
        assert context.user.email == "s@example.com"
        assert (context.session.expires_at - context.session.created_at).total_seconds() == 7200
        stored = (
            await conn.execute(text("SELECT count(*) FROM sessions WHERE token_hash <> ''::bytea"))
        ).scalar_one()
    assert stored == 1
    assert ("session.create_dev", OPERATOR) in await audit_actions(engine)


@pytest.mark.parametrize(
    "overrides",
    [
        {"allow_dev_sessions": False},  # the flag is required
        {"environment": "production"},
        {"environment": "staging"},
    ],
)
async def test_create_session_is_gated_on_environment_and_flag(
    acme: Settings, database_url: str, engine: AsyncEngine, overrides: dict[str, object]
) -> None:
    await invoke(
        acme, "add-member", "--workspace", "acme", "--email", "s@example.com", "--role", "VIEWER"
    )
    settings = Settings(
        **{
            "environment": "test",
            "allow_dev_sessions": True,
            "database_url": database_url,
            **overrides,
        }  # type: ignore[arg-type]
    )
    code, out, err = await invoke(settings, "create-session", "--email", "s@example.com")
    assert code == 1 and out == "" and "ABB_ALLOW_DEV_SESSIONS" in err
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM sessions"))).scalar_one() == 0


async def test_create_session_validates_hours_and_the_user(
    acme: Settings, database_url: str
) -> None:
    await invoke(
        acme, "add-member", "--workspace", "acme", "--email", "s@example.com", "--role", "VIEWER"
    )
    settings = dev(database_url)
    for hours in ("0", "-1", "25", "nan"):
        code, out, _ = await invoke(
            settings, "create-session", "--email", "s@example.com", "--hours", hours
        )
        assert code == 2 and out == "", hours
    code, out, err = await invoke(settings, "create-session", "--email", "ghost@example.com")
    assert code == 1 and out == "" and "no user" in err


# ---------------------------------------------------------------- the dev owner


async def test_seed_adds_the_dev_owner_only_when_dev_sessions_are_enabled(
    database_url: str, engine: AsyncEngine, tmp_path: Path
) -> None:
    key_file = str(tmp_path / "k")
    assert (await invoke(make_settings(database_url), "seed", "--key-file", key_file))[0] == 0
    _, out, _ = await invoke(make_settings(database_url), "list-members", "--workspace", "local")
    assert out == ""  # no flag, no owner

    settings = dev(database_url)
    for _ in range(2):  # idempotent
        assert (await invoke(settings, "seed", "--key-file", key_file))[0] == 0
    _, out, _ = await invoke(settings, "list-members", "--workspace", "local")
    assert [ln.split()[:2] for ln in out.strip().splitlines()] == [["owner@local.test", "OWNER"]]
    assert [a for a, _ in await audit_actions(engine, "local") if a == "member.add"] == [
        "member.add"
    ]


async def test_seed_does_not_add_the_dev_owner_in_staging(
    database_url: str, engine: AsyncEngine, tmp_path: Path
) -> None:
    settings = Settings(
        environment="staging",
        allow_dev_sessions=True,
        database_url=database_url,
    )
    await invoke(settings, "seed", "--key-file", str(tmp_path / "k"))
    _, out, _ = await invoke(settings, "list-members", "--workspace", "local")
    assert out == ""


def test_the_app_warns_at_startup_when_dev_sessions_are_allowed(
    database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # create_app installs its own JSON log handler, so read what it printed.
    create_app(dev(database_url))
    printed = capsys.readouterr().out
    assert "ABB_ALLOW_DEV_SESSIONS" in printed and "Development convenience" in printed
    production = Settings(
        environment="production", allow_dev_sessions=True, database_url=database_url
    )
    create_app(production)
    assert "ignored" in capsys.readouterr().out

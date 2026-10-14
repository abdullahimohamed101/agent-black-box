"""KI-031: serving processes refuse (production) or warn (development) when their database role
can rewrite events. Roles are real logins, never `SET ROLE`."""

import logging
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from abb_api.core.config import Settings, get_settings
from abb_api.db.role_check import (
    REFUSAL,
    UNVERIFIED_REFUSAL,
    PrivilegedDatabaseRole,
    enforce_role,
    privileges_of_connected_role,
)
from abb_api.jobs import worker as worker_module
from abb_api.main import create_app
from tests.conftest import UNREACHABLE_DATABASE_URL, _admin, runtime_url

PASSWORD = "role_check_secret_pw"


def settings_for(url: str, environment: str, **extra: object) -> Settings:
    return Settings(environment=environment, database_url=url, **extra)  # type: ignore[arg-type]


def engine_for(url: str) -> AsyncEngine:
    return create_async_engine(url, poolclass=NullPool)


@pytest.fixture
async def make_role(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> AsyncIterator[object]:
    """A login role that is a runtime role plus whatever `setup` adds; returns its URL."""
    created: list[tuple[str, list[str]]] = []

    async def make(*, attributes: str = "", grants: tuple[str, ...] = ()) -> str:
        name = f"abb_rc_{uuid.uuid4().hex[:8]}"
        await _admin(
            f"CREATE ROLE {name} LOGIN PASSWORD '{PASSWORD}' IN ROLE abb_runtime {attributes}"
        )
        revokes: list[str] = []
        for grant in grants:
            async with engine.begin() as conn:
                await conn.execute(text(grant.format(role=name)))
            revokes.append(
                grant.format(role=name).replace("GRANT", "REVOKE", 1).replace(" TO ", " FROM ", 1)
            )
        created.append((name, revokes))
        return (
            make_url(database_url)
            .set(username=name, password=PASSWORD)
            .render_as_string(hide_password=False)
        )

    yield make
    for name, revokes in created:
        for revoke in revokes:
            async with engine.begin() as conn:
                await conn.execute(text(revoke))
        await _admin(f"DROP ROLE IF EXISTS {name}")


async def reasons(url: str) -> list[str]:
    engine = engine_for(url)
    try:
        return await privileges_of_connected_role(engine)
    finally:
        await engine.dispose()


# ------------------------------------------------------------------ what counts as privileged


async def test_the_runtime_role_is_clean(database_url: str, runtime_database_url: str) -> None:
    assert await reasons(runtime_url(database_url)) == []


async def test_the_owner_is_privileged(database_url: str) -> None:
    found = await reasons(database_url)
    assert "events_owner" in found and "events_rewritable" in found and "schema_create" in found


@pytest.mark.parametrize(
    ("attributes", "grants", "expected"),
    [
        ("BYPASSRLS", (), ["bypass_rls"]),
        ("", ("GRANT UPDATE ON TABLE events TO {role}",), ["events_rewritable"]),
        ("", ("GRANT DELETE ON TABLE events TO {role}",), ["events_rewritable"]),
        ("", ("GRANT TRUNCATE ON TABLE events TO {role}",), ["events_rewritable"]),
        ("", ("GRANT CREATE ON SCHEMA public TO {role}",), ["schema_create"]),
    ],
)
async def test_each_dangerous_capability_is_detected_on_its_own(
    make_role: object, attributes: str, grants: tuple[str, ...], expected: list[str]
) -> None:
    url = await make_role(attributes=attributes, grants=grants)  # type: ignore[operator]
    assert await reasons(url) == expected


# ------------------------------------------------------------------ the policy


async def test_production_passes_for_the_runtime_role_silently(
    database_url: str, runtime_database_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    engine = engine_for(runtime_url(database_url))
    try:
        with caplog.at_level(logging.INFO):
            await enforce_role(engine, settings_for(database_url, "production"), service="api")
    finally:
        await engine.dispose()
    assert caplog.records == []


async def test_production_refuses_the_owner_with_a_fixed_message_and_no_secrets(
    database_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    engine = engine_for(database_url)
    parsed = make_url(database_url)
    try:
        with caplog.at_level(logging.INFO), pytest.raises(PrivilegedDatabaseRole) as refused:
            await enforce_role(engine, settings_for(database_url, "production"), service="api")
    finally:
        await engine.dispose()
    assert str(refused.value) == REFUSAL
    logged = " ".join(f"{r.getMessage()} {r.__dict__.get('reasons')}" for r in caplog.records)
    for secret in (str(parsed.password), str(parsed.host), str(parsed.port), database_url):
        if secret and secret != "None":
            assert secret not in str(refused.value) and secret not in logged
    assert [r.levelno for r in caplog.records] == [logging.ERROR]


@pytest.mark.parametrize("environment", ["development", "test", "staging"])
async def test_outside_production_the_owner_gets_one_warning_and_starts(
    database_url: str, caplog: pytest.LogCaptureFixture, environment: str
) -> None:
    engine = engine_for(database_url)
    try:
        with caplog.at_level(logging.INFO):
            await enforce_role(engine, settings_for(database_url, environment), service="api")
    finally:
        await engine.dispose()
    assert [r.levelno for r in caplog.records] == [logging.WARNING]


async def test_the_override_lets_production_start_and_says_so_every_time(
    database_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    engine = engine_for(database_url)
    settings = settings_for(database_url, "production", allow_privileged_db_role=True)
    try:
        with caplog.at_level(logging.INFO):
            await enforce_role(engine, settings, service="worker")
            await enforce_role(engine, settings, service="worker")
    finally:
        await engine.dispose()
    lines = [r for r in caplog.records if "ABB_ALLOW_PRIVILEGED_DB_ROLE" in r.getMessage()]
    assert len(lines) == 2 and all(r.levelno == logging.WARNING for r in lines)
    assert lines[0].__dict__["reasons"]  # the audit line names what was accepted


async def test_production_does_not_start_when_the_role_cannot_be_verified() -> None:
    engine = engine_for(UNREACHABLE_DATABASE_URL)
    try:
        with pytest.raises(PrivilegedDatabaseRole) as refused:
            await enforce_role(
                engine, settings_for(UNREACHABLE_DATABASE_URL, "production"), service="api"
            )
        assert str(refused.value) == UNVERIFIED_REFUSAL
        # development starts (readiness reports the database; nothing changes there), and the
        # override accepts the unverified state knowingly
        await enforce_role(
            engine, settings_for(UNREACHABLE_DATABASE_URL, "development"), service="api"
        )
        await enforce_role(
            engine,
            settings_for(UNREACHABLE_DATABASE_URL, "production", allow_privileged_db_role=True),
            service="api",
        )
    finally:
        await engine.dispose()


# ------------------------------------------------------------------ the serving processes


async def test_the_api_refuses_to_start_in_production_as_the_owner_and_starts_as_runtime(
    database_url: str, runtime_database_url: str
) -> None:
    app = create_app(settings_for(database_url, "production"))
    with pytest.raises(PrivilegedDatabaseRole):
        async with app.router.lifespan_context(app):
            pass
    ok = create_app(settings_for(runtime_url(database_url), "production"))
    async with ok.router.lifespan_context(ok):
        pass
    overridden = create_app(settings_for(database_url, "production", allow_privileged_db_role=True))
    async with overridden.router.lifespan_context(overridden):
        pass


def test_the_worker_exits_with_the_fixed_message_in_production_as_the_owner(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ABB_ENVIRONMENT", "production")
    monkeypatch.setenv("ABB_DATABASE_URL", database_url)
    get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit) as exit_:
            worker_module.main()
    finally:
        get_settings.cache_clear()
    assert str(exit_.value) == REFUSAL

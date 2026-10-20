"""KI-183: the role check repeated while running (Phase 19c step 3, ADR-169).

Roles are real logins whose privileges change between checks (GRANT, then REVOKE), as in
tests/test_startup_role_check.py, whose role factory is reused.
"""

import asyncio
import logging

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from abb_api.core.config import Settings
from abb_api.db import role_check
from abb_api.db.role_check import RoleMonitor
from abb_api.health.readiness import Readiness
from abb_api.obs.catalogue import build_observability
from tests.obs.helpers import value
from tests.test_startup_role_check import make_role  # noqa: F401  (fixture)


def settings_for(url: str, environment: str, **extra: object) -> Settings:
    return Settings(environment=environment, database_url=url, **extra)  # type: ignore[arg-type]


async def test_a_role_that_becomes_privileged_fails_readiness_and_recovers_when_revoked(
    make_role: object,  # noqa: F811
    engine: AsyncEngine,
    caplog: pytest.LogCaptureFixture,
    tmp_path: object,
) -> None:
    url = await make_role()  # type: ignore[operator]
    obs = build_observability()
    settings = settings_for(url, "production")
    role_engine = create_async_engine(url, poolclass=NullPool)
    monitor = RoleMonitor(role_engine, settings, service="api", initial=[], metrics=obs.catalogue)
    readiness = Readiness(
        lambda: role_engine, settings, role=monitor, metrics=obs.catalogue, ttl_seconds=0
    )
    try:
        assert (await readiness.evaluate()).checks["role"].status == "ok"
        async with engine.begin() as conn:
            await conn.execute(
                text(f"GRANT UPDATE ON TABLE events TO {url.split('//')[1].split(':')[0]}")
            )
        with caplog.at_level(logging.WARNING, logger="abb_api.db.role_check"):
            assert await monitor.check_once() == "privileged"
            assert await monitor.check_once() == "privileged"  # unchanged: no second line
        errors = [r for r in caplog.records if "became too privileged" in r.getMessage()]
        assert len(errors) == 1
        assert errors[0].__dict__["reasons"] == ["events_rewritable"]
        assert value(obs, "db_role_privileged", reason="events_rewritable") == 1
        report = await readiness.evaluate()
        assert not report.ready
        assert report.checks["role"].reason == "privileged_role"
        assert value(obs, "readiness_check", check="role") == 0
        async with engine.begin() as conn:
            await conn.execute(
                text(f"REVOKE UPDATE ON TABLE events FROM {url.split('//')[1].split(':')[0]}")
            )
        assert await monitor.check_once() == "ok"
        assert value(obs, "db_role_privileged", reason="events_rewritable") == 0
        assert (await readiness.evaluate()).checks["role"].status == "ok"
    finally:
        await role_engine.dispose()


async def test_outside_production_it_warns_once_and_never_fails_readiness(
    make_role: object,  # noqa: F811
    engine: AsyncEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    url = await make_role(attributes="BYPASSRLS")  # type: ignore[operator]
    obs = build_observability()
    settings = settings_for(url, "development")
    role_engine = create_async_engine(url, poolclass=NullPool)
    monitor = RoleMonitor(role_engine, settings, service="api", initial=None, metrics=obs.catalogue)
    readiness = Readiness(
        lambda: role_engine, settings, role=monitor, metrics=obs.catalogue, ttl_seconds=0
    )
    try:
        with caplog.at_level(logging.WARNING, logger="abb_api.db.role_check"):
            for _ in range(3):
                await monitor.check_once()
        assert [r.levelname for r in caplog.records if "rewrite events" in r.getMessage()] == [
            "WARNING"
        ]
        report = await readiness.evaluate()
        assert report.checks["role"].status == "degraded"
        assert report.checks["role"].reason == "privileged_role"
        assert report.ready or report.checks["database"].status == "fail"
    finally:
        await role_engine.dispose()


async def test_the_override_keeps_a_privileged_role_ready(
    make_role: object,  # noqa: F811
) -> None:
    url = await make_role(attributes="BYPASSRLS")  # type: ignore[operator]
    settings = settings_for(url, "production", allow_privileged_db_role=True)
    role_engine = create_async_engine(url, poolclass=NullPool)
    monitor = RoleMonitor(role_engine, settings, service="api", initial=None)
    try:
        assert await monitor.check_once() == "privileged"
        assert not monitor.refuses_traffic
    finally:
        await role_engine.dispose()


async def test_concurrent_checks_share_one_run_and_a_slow_run_is_bounded(
    runtime_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine(runtime_database_url, poolclass=NullPool)
    calls = 0
    release = asyncio.Event()

    async def slow(_: AsyncEngine) -> list[str]:
        nonlocal calls
        calls += 1
        await release.wait()
        return []

    monkeypatch.setattr(role_check, "privileges_of_connected_role", slow)
    monitor = RoleMonitor(engine, settings_for(runtime_database_url, "production"), service="api")
    try:
        first = asyncio.gather(*(monitor.check_once() for _ in range(5)))
        await asyncio.sleep(0)
        release.set()
        assert set(await first) == {"ok"}
        assert calls == 1
        # A run that outlives its bound keeps the previous finding and never raises.
        release.clear()
        monkeypatch.setattr(role_check, "CHECK_TIMEOUT_SECONDS", 0.05)
        assert await monitor.check_once() == "ok"
        assert monitor.runs == 1
    finally:
        await engine.dispose()


async def test_an_unreachable_database_keeps_the_previous_finding(
    runtime_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine(runtime_database_url, poolclass=NullPool)

    async def broken(_: AsyncEngine) -> list[str]:
        raise ConnectionRefusedError("secret-host-name")

    monkeypatch.setattr(role_check, "privileges_of_connected_role", broken)
    monitor = RoleMonitor(
        engine,
        settings_for(runtime_database_url, "production"),
        service="api",
        initial=["bypass_rls"],
    )
    try:
        assert await monitor.check_once() == "privileged"
        assert monitor.reasons == ["bypass_rls"]
    finally:
        await engine.dispose()


async def test_the_background_loop_repeats_at_the_interval_and_stops(
    runtime_database_url: str,
) -> None:
    engine = create_async_engine(runtime_database_url, poolclass=NullPool)
    ticks: asyncio.Queue[None] = asyncio.Queue()
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        await ticks.get()

    settings = settings_for(runtime_database_url, "production", role_check_interval_seconds=7)
    monitor = RoleMonitor(engine, settings, service="api", sleep=sleep)  # type: ignore[arg-type]
    try:
        monitor.start()
        for expected in (1, 2, 3):
            ticks.put_nowait(None)
            for _ in range(500):
                if monitor.runs >= expected:
                    break
                await asyncio.sleep(0.01)
            assert monitor.runs >= expected
        assert set(slept) == {7}
    finally:
        await monitor.stop()
        await engine.dispose()


def test_interval_zero_is_off_and_small_values_are_refused() -> None:
    off = settings_for("postgresql+asyncpg://x/y", "test", role_check_interval_seconds=0)
    assert off.role_check_interval_seconds == 0
    with pytest.raises(ValueError, match="role_check_interval_seconds"):
        settings_for("postgresql+asyncpg://x/y", "test", role_check_interval_seconds=1)

"""The retention process (ADR-159): its own credentials, its own role check, and no way in from
the API process."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from abb_api.core.config import Settings
from abb_api.retention import runner
from abb_api.retention.runner import (
    RetentionConfigError,
    RetentionRunnerSettings,
    enforce_retention_role,
    privileges_of_retention_role,
)
from tests.conftest import runtime_url
from tests.ingest_helpers import make_tenant
from tests.retention.helpers import NOW, seed_run

SRC = Path(runner.__file__).resolve().parents[2]  # apps/api/src
URL = "postgresql+asyncpg://u:p@localhost:5433/db"


def cfg(**kw: object) -> RetentionRunnerSettings:
    return RetentionRunnerSettings.model_validate(kw)


def test_the_explicit_retention_url_always_wins() -> None:
    for env in ("development", "test", "staging", "production"):
        assert cfg(environment=env, retention_database_url=URL).resolved_url() == URL


def test_local_environments_may_reuse_the_owner_url_but_never_production() -> None:
    owner = "postgresql+asyncpg://owner@h/db"
    assert cfg(environment="development", migration_database_url=owner).resolved_url() == owner
    assert cfg(environment="test", database_url=owner).resolved_url() == owner
    for env in ("staging", "production"):
        with pytest.raises(RetentionConfigError):
            cfg(environment=env, migration_database_url=owner, database_url=owner).resolved_url()


def test_without_any_url_the_runner_refuses() -> None:
    with pytest.raises(RetentionConfigError, match="ABB_RETENTION_DATABASE_URL"):
        cfg(environment="development").resolved_url()


def test_the_api_settings_do_not_know_the_retention_credentials() -> None:
    assert "retention_database_url" not in Settings.model_fields
    assert "migration_database_url" not in Settings.model_fields
    leaked = "postgresql+asyncpg://retention:hunter2@h/db"
    api = Settings.model_validate(
        {"database_url": "postgresql+asyncpg://x", "retention_database_url": leaked}
    )
    assert "hunter2" not in repr(api) and "hunter2" not in str(api.model_dump())
    assert not hasattr(api, "retention_database_url")


def test_only_the_runner_and_the_cli_dispatch_name_the_retention_credentials() -> None:
    holders = sorted(
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if "retention_database_url" in p.read_text()
    )
    assert holders == ["abb_api/retention/runner.py"]


def test_the_api_process_never_imports_the_tick_or_the_runner() -> None:
    code = (
        "import sys\n"
        "from abb_api.core.config import Settings\n"
        "from abb_api.main import create_app\n"
        "app = create_app(Settings(database_url='postgresql+asyncpg://x@localhost/db'))\n"
        "bad = [m for m in sys.modules if m in ('abb_api.retention.tick', "
        "'abb_api.retention.runner', 'abb_api.cli')]\n"
        "routes = list(app.openapi()['paths'])\n"
        "assert not bad, bad\n"
        "assert all('run-once' not in p and 'tick' not in p for p in routes), routes\n"
        "assert not [s for s in vars(app.state) if 'tick' in s or 'runner' in s]\n"
        "print('ok')\n"
    )
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        cwd=SRC,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(SRC)},
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ok"


@pytest.fixture
async def runtime_engine(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> AsyncEngine:
    return create_async_engine(runtime_url(database_url), poolclass=NullPool)


async def test_the_role_check_accepts_the_retention_role(retention_engine: AsyncEngine) -> None:
    assert await privileges_of_retention_role(retention_engine) == []
    for env in ("production", "development"):
        await enforce_retention_role(retention_engine, cfg(environment=env))


async def test_the_role_check_refuses_other_roles_in_production(
    engine: AsyncEngine, runtime_engine: AsyncEngine
) -> None:
    owner_reasons = await privileges_of_retention_role(engine)
    assert {"table_owner", "credential_tables"} <= set(owner_reasons)
    assert "credential_tables" in await privileges_of_retention_role(runtime_engine)
    for victim in (engine, runtime_engine):
        with pytest.raises(RetentionConfigError, match="Refusing to start"):
            await enforce_retention_role(victim, cfg(environment="production"))
        await enforce_retention_role(victim, cfg(environment="development"))  # warns only
        await enforce_retention_role(
            victim, cfg(environment="production", allow_privileged_db_role=True)
        )
    await runtime_engine.dispose()


def run_cli(tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    """`abb_api.cli retention ...` as its own process with only the retention environment."""
    return subprocess.run(
        [sys.executable, "-m", "abb_api.cli", "retention", "run-once"],
        capture_output=True,
        text=True,
        cwd=tmp_path,  # not apps/api: no .env is read
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(SRC), **env},
        check=False,
    )


async def test_run_once_from_the_cli_needs_only_the_retention_url(
    tmp_path: Path, engine: AsyncEngine, retention_database_url: str
) -> None:
    acme = await make_tenant(engine, "acme")
    await seed_run(engine, acme, ended=NOW.replace(year=2020), events=2, spans=0, jobs=0)
    done = run_cli(
        tmp_path,
        ABB_ENVIRONMENT="production",
        ABB_RETENTION_DATABASE_URL=retention_database_url,
    )
    assert done.returncode == 0, done.stderr
    assert re.search(r"mode=dry_run .*reports=\d+ .*errors=0", done.stdout), done.stdout
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM events"))).scalar_one() == 2
        assert (
            await conn.execute(text("SELECT count(*) FROM retention_reports"))
        ).scalar_one() >= 1
    assert retention_database_url not in done.stdout + done.stderr


async def test_the_cli_in_production_without_the_retention_url_refuses(tmp_path: Path) -> None:
    done = run_cli(tmp_path, ABB_ENVIRONMENT="production", ABB_DATABASE_URL=URL)
    assert done.returncode == 2
    assert "ABB_RETENTION_DATABASE_URL is required" in done.stderr
    assert URL not in done.stderr


async def test_enforce_exits_2_and_deletes_nothing(
    tmp_path: Path, engine: AsyncEngine, retention_database_url: str
) -> None:
    acme = await make_tenant(engine, "acme")
    await seed_run(engine, acme, ended=NOW.replace(year=2020), events=2, spans=0, jobs=0)
    done = run_cli(
        tmp_path,
        ABB_ENVIRONMENT="production",
        ABB_RETENTION_MODE="enforce",
        ABB_RETENTION_DATABASE_URL=retention_database_url,
    )
    assert done.returncode == 2 and "refused=True" in done.stdout
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM events"))).scalar_one() == 2
        assert (await conn.execute(text("SELECT count(*) FROM runs"))).scalar_one() == 1


async def test_off_does_not_even_connect(tmp_path: Path) -> None:
    done = run_cli(tmp_path, ABB_ENVIRONMENT="production", ABB_RETENTION_MODE="off")
    assert done.returncode == 0 and "mode=off" in done.stdout

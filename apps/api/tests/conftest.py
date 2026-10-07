"""Shared fixtures. Database tests run against a real PostgreSQL (spec §114.2), not mocks.

Isolation (resolves KI-011): the test session creates its own throwaway database, migrates it to
head with the real Alembic revisions, and drops it afterwards. Tests truncate tables between
runs instead of sharing state; migration tests create databases of their own.
"""

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from abb_api.core.config import Settings
from abb_api.db.tables import metadata
from abb_api.main import create_app

API_DIR = Path(__file__).resolve().parents[1]
SERVER_URL = os.environ.get(
    "ABB_TEST_DATABASE_URL",
    "postgresql+asyncpg://abb:abb_dev_password@localhost:5433/abb_test",
)
# A port nothing listens on, to exercise the dependency-down path.
UNREACHABLE_DATABASE_URL = "postgresql+asyncpg://abb:x@127.0.0.1:1/abb_test"


def make_settings(database_url: str, **overrides: object) -> Settings:
    overrides.setdefault("summary_debounce_seconds", 0.0)  # tests summarize immediately
    return Settings(
        environment="test",
        log_level="WARNING",
        database_url=database_url,
        **overrides,  # type: ignore[arg-type]
    )


def alembic(database_url: str, *args: str) -> None:
    subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", "-x", f"url={database_url}", *args],
        cwd=API_DIR,
        check=True,
        capture_output=True,
        text=True,
    )


def _admin_url() -> str:
    return make_url(SERVER_URL).set(database="postgres").render_as_string(hide_password=False)


async def _admin(statement: str) -> None:
    engine = create_async_engine(_admin_url(), isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text(statement))
    finally:
        await engine.dispose()


@contextmanager
def temporary_database(*, migrate: bool) -> Iterator[str]:
    """A fresh database on the test server; optionally migrated to head. Dropped on exit."""
    name = f"abb_test_{uuid.uuid4().hex[:12]}"
    url = make_url(SERVER_URL).set(database=name).render_as_string(hide_password=False)
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    try:
        if migrate:
            alembic(url, "upgrade", "head")
        yield url
    finally:
        asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    with temporary_database(migrate=True) as url:
        yield url


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    """A clean, migrated database for one test. NullPool: each test has its own event loop."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    names = ", ".join(f'"{t.name}"' for t in metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    try:
        yield engine
    finally:
        await engine.dispose()


async def _client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    # Entering the lifespan explicitly so app.state.engine exists (httpx does not run it).
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.fixture
async def client(database_url: str) -> AsyncIterator[httpx.AsyncClient]:
    async for c in _client(create_app(make_settings(database_url))):
        yield c


@pytest.fixture
async def client_db_down() -> AsyncIterator[httpx.AsyncClient]:
    async for c in _client(create_app(make_settings(UNREACHABLE_DATABASE_URL))):
        yield c


# Registered last: api_fixtures imports helpers defined above, so the import cannot be at the top.
from tests.api_fixtures import api  # noqa: E402, F401  (re-exported so pytest finds the fixture)

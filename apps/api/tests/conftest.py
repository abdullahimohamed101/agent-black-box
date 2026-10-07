"""Shared fixtures. Database tests run against a real PostgreSQL (spec §114.2), not mocks."""

import os
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from abb_api.core.config import Settings
from abb_api.main import create_app

TEST_DATABASE_URL = os.environ.get(
    "ABB_TEST_DATABASE_URL",
    "postgresql+asyncpg://abb:abb_dev_password@localhost:5433/abb_test",
)
# A port nothing listens on, to exercise the dependency-down path.
UNREACHABLE_DATABASE_URL = "postgresql+asyncpg://abb:x@127.0.0.1:1/abb_test"


def make_settings(database_url: str) -> Settings:
    return Settings(environment="test", log_level="WARNING", database_url=database_url)


async def _client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    # Entering the lifespan explicitly so app.state.engine exists (httpx does not run it).
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async for c in _client(create_app(make_settings(TEST_DATABASE_URL))):
        yield c


@pytest.fixture
async def client_db_down() -> AsyncIterator[httpx.AsyncClient]:
    async for c in _client(create_app(make_settings(UNREACHABLE_DATABASE_URL))):
        yield c

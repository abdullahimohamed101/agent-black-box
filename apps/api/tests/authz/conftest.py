from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.api_fixtures import build_api
from tests.authz.world import WEB_ORIGIN, World, build_world
from tests.conftest import make_settings


@pytest.fixture
async def world(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> AsyncIterator[World]:
    # web_origin turns on cookie sessions (and their CSRF rule); no identity provider is needed
    # because the fixtures mint sessions straight into the database.
    settings = make_settings(database_url, web_origin=WEB_ORIGIN)
    async for api in build_api(database_url, engine, settings=settings):
        async for instance in build_world(api, runtime_database_url):
            yield instance

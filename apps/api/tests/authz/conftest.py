from collections.abc import AsyncIterator

import pytest

from tests.api_fixtures import Api
from tests.authz.world import World, build_world


@pytest.fixture
async def world(api: Api, runtime_database_url: str) -> AsyncIterator[World]:
    async for instance in build_world(api, runtime_database_url):
        yield instance

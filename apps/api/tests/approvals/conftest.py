"""Every test here runs with cookie sessions switched on: the approvals API has a person's side."""

import pytest

from tests.api_fixtures import Api


@pytest.fixture
async def api(web: Api) -> Api:
    return web

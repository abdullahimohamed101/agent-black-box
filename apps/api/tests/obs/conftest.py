"""Fixtures for tests that drive real behaviour and read the resulting metrics."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.obs.catalogue import Observability, build_observability
from tests.api_fixtures import Api, build_api
from tests.conftest import make_settings
from tests.obs.helpers import TOKEN


@pytest.fixture
async def observed(
    database_url: str, runtime_database_url: str, engine: AsyncEngine, tmp_path: Path
) -> AsyncIterator[tuple[Api, Observability]]:
    """The usual two-tenant application and worker on one isolated registry."""
    obs = build_observability()
    settings = make_settings(
        database_url,
        metrics_token=TOKEN,
        artifact_dir=str(tmp_path / "store"),
        artifact_max_bytes=4096,
        # Real (small) failed-auth budgets, so hostile traffic can reach every throttle scope.
        trust_proxy=True,
        auth_fail_per_address=40,
        auth_fail_per_credential=3,
        auth_fail_global=60,
        limits_block_divisor=1000,
    )
    async for api in build_api(
        database_url, engine, settings=settings, app_options={"observability": obs}
    ):
        yield api, obs

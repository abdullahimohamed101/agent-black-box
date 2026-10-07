"""An application wired to a real database with a seeded tenant and a set of API keys."""

import gzip
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.auth import scopes
from abb_api.auth.repository import ApiKeyRepository
from abb_api.core.config import Settings
from abb_api.ingestion.ratelimit import RateLimiter
from abb_api.main import create_app
from tests.conftest import _client, make_settings
from tests.ingest_helpers import RECEIVED, Tenant, make_tenant

NOW = RECEIVED


class Tick:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@dataclass
class Api:
    client: httpx.AsyncClient
    engine: AsyncEngine
    tenant: Tenant
    other: Tenant
    clock: Tick
    tokens: dict[str, str]  # name -> bearer token

    def headers(self, token: str | None = "writer", **extra: str) -> dict[str, str]:
        headers = {"content-type": "application/json", **extra}
        if token is not None:
            headers["authorization"] = f"Bearer {self.tokens.get(token, token)}"
        return headers

    async def post_batch(
        self,
        events: list[Any],
        *,
        token: str | None = "writer",
        compress: bool = False,
        path: str = "/v1/events/batch",
        **body_fields: Any,
    ) -> httpx.Response:
        body = json.dumps({"events": events, **body_fields}).encode()
        extra: dict[str, str] = {}
        if compress:
            body, extra = gzip.compress(body), {"content-encoding": "gzip"}
        return await self.client.post(path, content=body, headers=self.headers(token, **extra))


MakeApi = Callable[..., "AsyncIterator[Api]"]


async def build_api(
    database_url: str,
    engine: AsyncEngine,
    *,
    settings: Settings | None = None,
    rate_limiter: RateLimiter | None = None,
) -> AsyncIterator[Api]:
    tenant = await make_tenant(engine, "acme", projects=("alpha", "beta"))
    other = await make_tenant(engine, "globex")
    clock = Tick()
    tokens: dict[str, str] = {}
    async with engine.begin() as conn:
        keys = ApiKeyRepository(conn, tenant.context)
        project = tenant.project_uuids["alpha"]
        rw = frozenset({scopes.EVENTS_WRITE, scopes.RUNS_READ})
        tokens["writer"] = (await keys.create(scopes=rw, project_id=project)).token
        tokens["reader"] = (
            await keys.create(scopes=frozenset({scopes.RUNS_READ}), project_id=project)
        ).token
        tokens["wide"] = (await keys.create(scopes=rw)).token  # workspace-wide: cannot ingest
        revoked = await keys.create(scopes=rw, project_id=project)
        await keys.revoke(revoked.stored.key_id, NOW - timedelta(days=1))
        tokens["revoked"] = revoked.token
        tokens["expired"] = (
            await keys.create(scopes=rw, project_id=project, expires_at=NOW - timedelta(seconds=1))
        ).token
        other_keys = ApiKeyRepository(conn, other.context)
        tokens["other"] = (
            await other_keys.create(scopes=rw, project_id=other.project_uuids["p"])
        ).token
    app = create_app(
        settings or make_settings(database_url), clock=clock, rate_limiter=rate_limiter
    )
    async for client in _client(app):
        yield Api(client, engine, tenant, other, clock, tokens)


@pytest.fixture
async def api(database_url: str, engine: AsyncEngine) -> AsyncIterator[Api]:
    async for instance in build_api(database_url, engine):
        yield instance

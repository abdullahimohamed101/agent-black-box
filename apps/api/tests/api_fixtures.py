"""An application wired to a real database with a seeded tenant and a set of API keys."""

import gzip
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from abb_api.auth import scopes
from abb_api.auth.repository import ApiKeyRepository
from abb_api.core.config import Settings
from abb_api.ingestion.ratelimit import RateLimiter
from abb_api.jobs.handlers import HANDLERS
from abb_api.jobs.worker import Worker
from abb_api.main import create_app
from tests.conftest import _client, make_settings, runtime_url
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
    worker: Worker
    app: FastAPI

    def headers(self, token: str | None = "writer", **extra: str) -> dict[str, str]:
        headers = {"content-type": "application/json", **extra}
        if token is not None:
            headers["authorization"] = f"Bearer {self.tokens.get(token, token)}"
        return headers

    async def get(
        self, path: str, *, token: str | None = "reader", **params: Any
    ) -> httpx.Response:
        headers = self.headers(token)
        del headers["content-type"]
        clean = {k: v for k, v in params.items() if v is not None}
        return await self.client.get(path, params=clean, headers=headers)

    async def drain(self) -> None:
        """Run the background worker until it has nothing left to do."""
        for _ in range(100):
            if await self.worker.run_once() == 0:
                return
        raise AssertionError("worker never ran out of jobs")

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
    clock: Tick | None = None,
    app_options: dict[str, Any] | None = None,
) -> AsyncIterator[Api]:
    tenant = await make_tenant(engine, "acme", projects=("alpha", "beta"))
    other = await make_tenant(engine, "globex")
    clock = clock or Tick()
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
        tokens["wide_reader"] = (
            await keys.create(scopes=frozenset({scopes.RUNS_READ}))
        ).token  # reads every project of the workspace
        tokens["ingest_only"] = (
            await keys.create(scopes=frozenset({scopes.EVENTS_WRITE}), project_id=project)
        ).token
        tokens["beta"] = (
            await keys.create(scopes=rw, project_id=tenant.project_uuids["beta"])
        ).token
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
    # The application and the worker run as the least-privilege role; `engine` (the owner) only
    # seeds and truncates, so every API test also proves the runtime role is sufficient (KI-020).
    app = create_app(
        settings.model_copy(update={"database_url": runtime_url(database_url)})
        if settings
        else make_settings(runtime_url(database_url)),
        clock=clock,
        rate_limiter=rate_limiter,
        **(app_options or {}),
    )
    worker_engine = create_async_engine(runtime_url(database_url), poolclass=NullPool)
    worker = Worker(worker_engine, HANDLERS, make_settings(database_url), owner="api-test")
    try:
        async for client in _client(app):
            yield Api(client, engine, tenant, other, clock, tokens, worker, app)
    finally:
        await worker_engine.dispose()


@pytest.fixture
async def api(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> AsyncIterator[Api]:
    async for instance in build_api(database_url, engine):
        yield instance


WEB_ORIGIN = "http://localhost:3000"


@pytest.fixture
async def web(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> AsyncIterator[Api]:
    """The `api` fixture with cookie sessions switched on (a web origin is configured)."""
    async for instance in build_api(
        database_url, engine, settings=make_settings(database_url, web_origin=WEB_ORIGIN)
    ):
        yield instance


async def person(
    api: Api, email: str, role: str | None, workspace: str = "acme", *, verified: bool = False
) -> dict[str, str]:
    """Headers of a signed-in member (cookie, origin, workspace); creates the user if needed."""
    from tests.auth_helpers import add_member, mint_session

    tenant = api.tenant if workspace == "acme" else api.other
    user = await add_member(api.engine, tenant.context.workspace_id, email, role, verified=verified)
    token = await mint_session(api.engine, user.id, api.clock())
    return {
        "cookie": f"abb_session={token}",
        "origin": WEB_ORIGIN,
        "x-abb-workspace": tenant.workspace_id,
    }

"""Shared builders for the metrics tests: an app without a database and an isolated registry."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from abb_api.core.config import Settings
from abb_api.main import create_app
from abb_api.obs.catalogue import Observability, build_observability
from tests.obs.parser import Family, parse

TOKEN = "scrape-token-0123456789-abcdefgh"
PLACEHOLDER_DB = "postgresql+asyncpg://metrics/none"


def make_app(
    *, token: str | None = TOKEN, observability: Observability | None = None, **overrides: object
) -> FastAPI:
    settings = Settings(
        database_url=PLACEHOLDER_DB,
        environment="test",
        metrics_token=token,  # type: ignore[arg-type]
        **overrides,  # type: ignore[arg-type]
    )
    return create_app(settings, observability=observability or build_observability())


@asynccontextmanager
async def client_for(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    # The ASGI transport does not run the lifespan: no database is needed for these routes.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        yield client


async def scrape(client: httpx.AsyncClient, token: str = TOKEN) -> dict[str, Family]:
    response = await client.get("/metrics", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    return parse(response.text)

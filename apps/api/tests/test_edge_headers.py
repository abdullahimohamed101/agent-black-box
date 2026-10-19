"""Secure headers on every API response, and /docs per environment (Phase 19a, ADR-155, KI-023)."""

import httpx
import pytest

from abb_api.core.config import Settings
from abb_api.core.middleware import API_CSP, HSTS
from abb_api.main import create_app
from tests.api_fixtures import Api
from tests.ingest_helpers import make_run_ids, wire_event

PLACEHOLDER_DB = "postgresql+asyncpg://edge/none"


def assert_secure_headers(response: httpx.Response, *, hsts: bool = False) -> None:
    h = response.headers
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "no-referrer"
    assert h["content-security-policy"] == API_CSP
    assert "frame-ancestors 'none'" in API_CSP and "default-src 'none'" in API_CSP
    assert h["cache-control"] == "no-store"
    assert h["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
    assert ("strict-transport-security" in h) is hsts
    if hsts:
        assert h["strict-transport-security"] == HSTS


async def test_every_kind_of_response_carries_the_headers(api: Api) -> None:
    run = make_run_ids()
    responses = [
        await api.client.get("/healthz"),  # liveness
        await api.client.get("/readyz"),  # readiness
        await api.get("/v1/runs"),  # authenticated 200
        await api.get("/v1/runs", token=None),  # 401
        await api.get("/v1/runs", token="reader", limit="not-a-number"),  # 422
        await api.client.get("/v1/nothing-here"),  # 404
        await api.post_batch([wire_event(run, 1)]),  # 202
        await api.client.post("/v1/events/batch", content=b"{", headers=api.headers()),  # 400/422
        await api.client.get("/v1/auth/login"),  # unauthenticated redirect or 404 without OIDC
    ]
    assert {r.status_code for r in responses} >= {200, 202, 401, 404, 422}
    for response in responses:
        assert_secure_headers(response)


async def test_hsts_only_when_the_deployment_says_it_is_served_over_https() -> None:
    for origin, expected in (
        (None, False),
        ("http://localhost:3000", False),
        ("https://abb.example.com", True),
    ):
        extra: dict[str, object] = {"web_origin": origin} if origin else {}
        app = create_app(
            Settings(environment="production", database_url=PLACEHOLDER_DB, **extra)  # type: ignore[arg-type]
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            assert_secure_headers(await client.get("/healthz"), hsts=expected)


# ------------------------------------------------------------------ /docs per environment


async def docs_status(**settings: object) -> dict[str, int]:
    app = create_app(Settings(database_url=PLACEHOLDER_DB, **settings))  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return {
            path: (await c.get(path)).status_code for path in ("/docs", "/redoc", "/openapi.json")
        }


@pytest.mark.parametrize("environment", ["development", "test", "staging"])
async def test_docs_are_served_outside_production_by_default(environment: str) -> None:
    assert await docs_status(environment=environment) == {
        "/docs": 200,
        "/redoc": 200,
        "/openapi.json": 200,
    }


async def test_docs_are_off_in_production_by_default() -> None:
    assert await docs_status(environment="production") == {
        "/docs": 404,
        "/redoc": 404,
        "/openapi.json": 404,
    }


async def test_the_explicit_setting_overrides_the_environment_both_ways() -> None:
    assert set((await docs_status(environment="production", api_docs_enabled=True)).values()) == {
        200
    }
    assert set((await docs_status(environment="staging", api_docs_enabled=False)).values()) == {404}
    assert set((await docs_status(environment="development", api_docs_enabled=False)).values()) == {
        404
    }


async def test_the_schema_is_still_built_offline_when_the_routes_are_off() -> None:
    app = create_app(Settings(environment="production", database_url=PLACEHOLDER_DB))
    document = app.openapi()  # what `python -m abb_api.openapi` and the web generator call
    assert document["info"]["title"] == "Agent BlackBox API" and document["paths"]


async def test_the_docs_pages_are_the_only_ones_without_the_json_csp() -> None:
    app = create_app(Settings(environment="development", database_url=PLACEHOLDER_DB))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        docs, schema = await c.get("/docs"), await c.get("/openapi.json")
    assert "content-security-policy" not in docs.headers
    assert docs.headers["x-frame-options"] == "DENY"  # still never framed
    assert schema.headers["content-security-policy"] == API_CSP


async def test_with_the_docs_routes_off_their_paths_keep_the_strict_csp() -> None:
    """The exemption exists for the Swagger page only: where the page is not served, /docs is an
    ordinary 404 and must not come back without the API policy."""
    off = create_app(Settings(environment="production", database_url=PLACEHOLDER_DB))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=off), base_url="http://t") as c:
        for path in ("/docs", "/redoc", "/docs/oauth2-redirect"):
            response = await c.get(path)
            assert response.status_code == 404, path
            assert response.headers["content-security-policy"] == API_CSP, path

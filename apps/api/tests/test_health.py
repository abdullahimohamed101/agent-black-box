import json
import logging

import httpx
import pytest

from abb_api import __version__
from abb_api.main import create_app
from tests.conftest import TEST_DATABASE_URL, _client, make_settings


async def test_healthz_needs_no_database(client_db_down: httpx.AsyncClient) -> None:
    response = await client_db_down.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


async def test_readyz_ok_with_database(client: httpx.AsyncClient) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["database"] == "ok"


async def test_readyz_503_with_typed_error_when_database_down(
    client_db_down: httpx.AsyncClient,
) -> None:
    response = await client_db_down.get("/readyz")
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "DEPENDENCY_UNAVAILABLE"
    assert error["retryable"] is True
    assert error["request_id"] == response.headers["x-request-id"]
    assert "asyncpg" not in json.dumps(error)  # no internals leak to clients


async def test_request_id_generated_and_echoed(client: httpx.AsyncClient) -> None:
    generated = await client.get("/healthz")
    assert generated.headers["x-request-id"].startswith("req_")
    echoed = await client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    assert echoed.headers["x-request-id"] == "abc-123"


async def test_unsafe_inbound_request_id_is_replaced(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz", headers={"X-Request-ID": "bad id\twith spaces"})
    assert response.headers["x-request-id"].startswith("req_")


async def test_unknown_route_uses_error_envelope(client: httpx.AsyncClient) -> None:
    response = await client.get("/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_request_log_is_structured_with_request_id(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="abb_api.request"):
        response = await client.get("/healthz")
    record = next(r for r in caplog.records if r.name == "abb_api.request")
    assert record.__dict__["status"] == 200
    assert record.__dict__["path"] == "/healthz"
    assert response.headers["x-request-id"]


async def test_unhandled_exception_returns_generic_500_with_request_id_header() -> None:
    app = create_app(make_settings(TEST_DATABASE_URL))

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret-internal-detail")

    async for c in _client(app):
        response = await c.get("/boom")
    assert response.status_code == 500
    assert response.headers["x-request-id"] == response.json()["error"]["request_id"]
    assert "secret-internal-detail" not in response.text


async def test_overlong_request_id_is_replaced(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz", headers={"X-Request-ID": "a" * 65})
    assert response.headers["x-request-id"].startswith("req_")


async def test_method_not_allowed_is_a_client_error_category(client: httpx.AsyncClient) -> None:
    response = await client.post("/healthz")
    assert response.status_code == 405
    error = response.json()["error"]
    assert error["code"] == "HTTP_405"
    assert error["category"] == "VALIDATION"


async def test_cors_allows_configured_origin_only(client: httpx.AsyncClient) -> None:
    preflight = {"Access-Control-Request-Method": "GET"}
    ok = await client.options("/healthz", headers={"Origin": "http://localhost:3000", **preflight})
    assert ok.headers["access-control-allow-origin"] == "http://localhost:3000"
    bad = await client.options("/healthz", headers={"Origin": "http://evil.example", **preflight})
    assert "access-control-allow-origin" not in bad.headers


async def test_readyz_is_bounded_when_pool_is_saturated() -> None:
    app = create_app(make_settings(TEST_DATABASE_URL))
    async for c in _client(app):
        engine = app.state.engine
        held = [await engine.connect() for _ in range(engine.pool.size() + 10)]
        try:
            response = await c.get("/readyz")
        finally:
            for conn in held:
                await conn.close()
    assert response.status_code == 503

import json
import logging

import httpx
import pytest

from abb_api import __version__


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

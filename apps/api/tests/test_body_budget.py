"""The per-process cap on request-body bytes in memory (Phase 19a, ADR-155, KI-026)."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.types import Message

from abb_api.core.body_budget import SMALL_BODY_BYTES, BodyBudget, BodyBudgetMiddleware
from abb_api.core.errors import AppError, install_error_handlers
from abb_api.core.request_context import set_request_id
from abb_api.main import create_app
from tests.api_fixtures import build_api
from tests.conftest import make_settings
from tests.ingest_helpers import make_run_ids, wire_event

MIB = 1024 * 1024


def build(
    cap: int, *, hold: float = 0.0, ceiling: int = 8 * MIB, timeout: float = 30.0
) -> tuple[FastAPI, BodyBudget, list[int]]:
    """A tiny app that buffers each body like the ingestion routes do, then holds it for `hold`."""
    budget = BodyBudget(cap)
    handled: list[int] = []
    app = FastAPI()
    install_error_handlers(app)

    @app.post("/body")
    async def body(request: Request) -> dict[str, int]:
        data = await request.body()
        handled.append(len(data))
        await asyncio.sleep(hold)
        return {"bytes": len(data)}

    @app.get("/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    app.add_middleware(
        BodyBudgetMiddleware, budget=budget, ceiling_bytes=ceiling, read_timeout_seconds=timeout
    )
    return app, budget, handled


def client_for(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
    )


async def test_a_flood_of_large_bodies_stays_within_the_cap_and_sheds_the_rest() -> None:
    app, budget, handled = build(8 * MIB, hold=0.2)
    body = b"x" * (3 * MIB)
    async with client_for(app) as client:
        responses = await asyncio.gather(*(client.post("/body", content=body) for _ in range(40)))
    statuses = [r.status_code for r in responses]
    assert set(statuses) <= {200, 503}
    assert statuses.count(200) >= 1 and statuses.count(503) >= 1
    assert statuses.count(200) == len(handled)
    assert budget.peak <= 8 * MIB  # the accounted bytes never passed the cap
    assert budget.in_flight == 0
    refused = next(r for r in responses if r.status_code == 503)
    assert refused.headers["retry-after"] == "2"
    error = refused.json()["error"]
    assert (error["code"], error["category"], error["retryable"]) == ("BUSY", "DEPENDENCY", True)


async def test_content_length_refuses_before_the_body_is_read() -> None:
    app, budget, handled = build(4 * MIB)
    budget.in_flight = 3 * MIB  # other requests hold most of the budget
    async with client_for(app) as client:
        response = await client.post("/body", content=b"x" * (2 * MIB))
    assert response.status_code == 503 and response.json()["error"]["code"] == "BUSY"
    assert handled == []  # the handler never ran: nothing was read
    assert budget.in_flight == 3 * MIB and budget.refused == 1


async def test_a_streamed_body_without_content_length_is_refused_while_it_streams() -> None:
    app, budget, _ = build(2 * MIB)

    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(40):
            yield b"y" * (100 * 1024)  # 4 MiB in total, no Content-Length

    async with client_for(app) as client:
        response = await client.post("/body", content=chunks())
    assert response.status_code == 503 and response.json()["error"]["code"] == "BUSY"
    assert budget.peak <= 2 * MIB and budget.in_flight == 0


async def test_small_requests_are_unaffected_while_the_budget_is_full() -> None:
    app, budget, _ = build(4 * MIB)
    budget.in_flight = 4 * MIB
    async with client_for(app) as client:
        small = await client.post("/body", content=b"z" * SMALL_BODY_BYTES)
        get = await client.get("/ping")
        large = await client.post("/body", content=b"z" * (SMALL_BODY_BYTES + 1))
    assert small.status_code == 200 and get.status_code == 200
    assert large.status_code == 503
    assert budget.in_flight == 4 * MIB  # the small body was counted and released


async def test_a_body_over_the_ceiling_is_413_whatever_the_route() -> None:
    app, budget, _ = build(64 * MIB, ceiling=2 * MIB)
    async with client_for(app) as client:
        declared = await client.post("/body", content=b"x" * (3 * MIB))

        async def chunks() -> AsyncIterator[bytes]:
            for _ in range(30):
                yield b"y" * (100 * 1024)

        streamed = await client.post("/body", content=chunks())
    for response in (declared, streamed):
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert budget.in_flight == 0


# ------------------------------------------------------- accounting never leaks (raw ASGI)


def scope() -> dict[str, Any]:
    raw = [(b"transfer-encoding", b"chunked")]  # no Content-Length: counted as it streams
    return {"type": "http", "method": "POST", "path": "/body", "headers": raw}


async def run_raw(
    handler: Any,
    messages: list[Message],
    *,
    cap: int = 4 * MIB,
    read_timeout: float = 30.0,
) -> tuple[BodyBudget, BaseException | None]:
    budget = BodyBudget(cap)
    app = BodyBudgetMiddleware(
        handler, budget=budget, ceiling_bytes=8 * MIB, read_timeout_seconds=read_timeout
    )
    queue: asyncio.Queue[Message] = asyncio.Queue()
    for message in messages:
        queue.put_nowait(message)

    async def send(message: Message) -> None:
        return None

    failure: BaseException | None = None
    try:
        await app(scope(), queue.get, send)
    except BaseException as error:
        failure = error
    return budget, failure


CHUNK = {"type": "http.request", "body": b"a" * (200 * 1024), "more_body": True}


async def read_all(scope_: Any, receive: Any, send: Any) -> None:
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise ConnectionError("client went away")
        if not message.get("more_body", False):
            return


async def test_a_client_disconnect_mid_body_releases_the_accounting() -> None:
    budget, failure = await run_raw(read_all, [CHUNK, CHUNK, {"type": "http.disconnect"}])
    assert isinstance(failure, ConnectionError)
    assert budget.peak == 400 * 1024 and budget.in_flight == 0


async def test_an_exception_after_reading_releases_the_accounting() -> None:
    async def explode(scope_: Any, receive: Any, send: Any) -> None:
        await receive()
        raise RuntimeError("boom")

    budget, failure = await run_raw(explode, [CHUNK])
    assert isinstance(failure, RuntimeError)
    assert budget.peak == 200 * 1024 and budget.in_flight == 0


async def test_a_cancelled_request_releases_the_accounting() -> None:
    budget = BodyBudget(4 * MIB)
    queue: asyncio.Queue[Message] = asyncio.Queue()
    queue.put_nowait(CHUNK)  # then the client goes quiet: the handler waits for more

    async def send(message: Message) -> None:
        return None

    app = BodyBudgetMiddleware(
        read_all, budget=budget, ceiling_bytes=8 * MIB, read_timeout_seconds=30
    )
    task = asyncio.create_task(app(scope(), queue.get, send))
    for _ in range(100):
        await asyncio.sleep(0.01)
        if budget.in_flight:
            break
    assert budget.in_flight == 200 * 1024
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert budget.in_flight == 0


async def test_a_body_that_trickles_past_the_read_timeout_is_dropped_and_released() -> None:
    set_request_id("req_test")
    budget, failure = await run_raw(read_all, [CHUNK], read_timeout=0.2)
    assert isinstance(failure, AppError)
    assert (failure.code, failure.status_code) == ("REQUEST_TIMEOUT", 408)
    assert budget.in_flight == 0


async def test_the_streaming_refusal_releases_what_the_request_held() -> None:
    big = {"type": "http.request", "body": b"b" * (3 * MIB), "more_body": True}
    budget, failure = await run_raw(read_all, [big, big], cap=4 * MIB)
    assert isinstance(failure, AppError) and failure.code == "BUSY"
    assert budget.in_flight == 0 and budget.peak == 3 * MIB


def test_unit_rules_of_the_budget() -> None:
    budget = BodyBudget(10 * MIB)
    assert budget.grow(0, 6 * MIB) and budget.grow(0, 4 * MIB)
    assert not budget.grow(0, SMALL_BODY_BYTES + 1) and budget.in_flight == 10 * MIB
    assert budget.grow(0, SMALL_BODY_BYTES)  # small: counted, never refused
    assert budget.in_flight == 10 * MIB + SMALL_BODY_BYTES
    budget.release(10 * MIB + SMALL_BODY_BYTES)
    budget.release(5)  # releasing more than is held never goes negative
    assert budget.in_flight == 0


# --------------------------------------------------------------- the real application


async def test_the_real_application_sheds_large_ingestion_and_keeps_small_traffic(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> None:
    settings = make_settings(runtime_database_url, max_body_bytes_in_flight=8 * MIB)
    async for api in build_api(database_url, engine, settings=settings):
        budget: BodyBudget = api.app.state.body_budget
        run = make_run_ids()
        ok = await api.post_batch([wire_event(run, 1)])
        assert ok.status_code == 202 and budget.in_flight == 0
        budget.in_flight = 8 * MIB  # the process is full of other large bodies
        large = await api.client.post(
            "/v1/events/batch", content=b"{" + b" " * (2 * MIB) + b"}", headers=api.headers()
        )
        assert large.status_code == 503 and large.json()["error"]["code"] == "BUSY"
        assert large.headers["retry-after"] == "2"
        assert large.headers["x-request-id"] and large.headers["x-content-type-options"]
        small = await api.post_batch([wire_event(run, 2)])
        assert small.status_code == 202  # a small batch is never starved
        assert (await api.get("/v1/runs")).status_code == 200
        budget.in_flight = 0


def test_the_budget_must_hold_the_largest_body() -> None:
    with pytest.raises(ValueError, match="max_body_bytes_in_flight"):
        make_settings("postgresql+asyncpg://x/none", max_body_bytes_in_flight=2 * MIB)


def test_the_cap_is_a_setting_with_a_sane_default() -> None:
    settings = make_settings("postgresql+asyncpg://x/none")
    assert settings.max_body_bytes_in_flight == 64 * MIB
    assert settings.body_ceiling_bytes == 8 * MIB
    app = create_app(settings)
    assert app.state.body_budget.cap_bytes == 64 * MIB

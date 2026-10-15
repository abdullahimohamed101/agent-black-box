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


# ------------------------------------------------- reservation at admission (Content-Length)


def declared_scope(length: int) -> dict[str, Any]:
    return {
        "type": "http",
        "method": "POST",
        "path": "/body",
        "headers": [(b"content-length", str(length).encode())],
    }


class Client:
    """One raw request that declares `length` bytes and sends them only when told to."""

    def __init__(self, app: Any, length: int, *, send_bytes: int | None = None) -> None:
        self.reads = 0
        self.go = asyncio.Event()
        self.length = length
        self.send_bytes = length if send_bytes is None else send_bytes
        self.sent: list[Message] = []
        self.app = app

    async def receive(self) -> Message:
        self.reads += 1
        await self.go.wait()
        self.go.clear()
        return {"type": "http.request", "body": b"a" * self.send_bytes, "more_body": False}

    async def send(self, message: Message) -> None:
        self.sent.append(message)

    def status(self) -> int | None:
        started = [m for m in self.sent if m["type"] == "http.response.start"]
        return int(started[0]["status"]) if started else None

    def run(self) -> "asyncio.Task[None]":
        return asyncio.create_task(self.app(declared_scope(self.length), self.receive, self.send))


async def read_body_then_wait(scope_: Any, receive: Any, send: Any) -> None:
    await receive()
    await asyncio.sleep(0.3)
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b""})


async def test_many_declared_large_bodies_are_refused_before_any_byte_is_read() -> None:
    """Admission reserves the declared size: ten requests declaring 3 MiB against a 4 MiB cap used
    to all pass (nothing was counted until bytes arrived) and fail one by one mid-transfer."""
    budget = BodyBudget(4 * MIB)
    app = BodyBudgetMiddleware(
        read_body_then_wait, budget=budget, ceiling_bytes=8 * MIB, read_timeout_seconds=30
    )
    clients = [Client(app, 3 * MIB) for _ in range(10)]
    tasks = [c.run() for c in clients]
    await asyncio.sleep(0.05)
    refused = [c for c in clients if c.status() == 503]
    assert len(refused) == 9 and all(c.reads == 0 for c in refused)  # refused unread
    assert budget.in_flight == 3 * MIB and budget.peak <= 4 * MIB  # the admitted one is reserved
    admitted = next(c for c in clients if c.status() is None)
    admitted.go.set()
    await asyncio.gather(*tasks)
    assert admitted.status() == 200 and budget.in_flight == 0 and budget.refused == 9


async def test_the_unread_part_of_a_reservation_is_released_when_the_body_is_complete() -> None:
    budget = BodyBudget(8 * MIB)
    app = BodyBudgetMiddleware(
        read_body_then_wait, budget=budget, ceiling_bytes=8 * MIB, read_timeout_seconds=30
    )
    client = Client(app, 3 * MIB, send_bytes=1 * MIB)
    task = client.run()
    await asyncio.sleep(0.05)
    assert budget.in_flight == 3 * MIB
    client.go.set()
    await asyncio.sleep(0.1)  # body read, handler still working on it
    assert budget.in_flight == 1 * MIB  # only what really arrived is held
    await task
    assert budget.in_flight == 0


async def test_a_reservation_that_is_never_filled_is_released_by_the_read_timeout() -> None:
    """The anonymous-slow-client bound: declare a big body, never send it, hold budget for at most
    the read timeout, even when the handler never gets round to reading."""
    budget = BodyBudget(4 * MIB)

    async def never_reads(scope_: Any, receive: Any, send: Any) -> None:
        await asyncio.sleep(0.6)

    app = BodyBudgetMiddleware(
        never_reads, budget=budget, ceiling_bytes=8 * MIB, read_timeout_seconds=0.2
    )
    task = Client(app, 3 * MIB).run()
    await asyncio.sleep(0.05)
    assert budget.in_flight == 3 * MIB
    # Nothing else of this size fits meanwhile...
    other = Client(app, 3 * MIB)
    other_task = other.run()
    await asyncio.sleep(0.05)
    assert other.status() == 503
    await asyncio.sleep(0.25)  # ...until the timeout has passed.
    assert budget.in_flight == 0 and not task.done()  # released while the handler still runs
    await asyncio.gather(task, other_task)
    assert budget.in_flight == 0  # and releasing again at the end does not go negative


async def test_a_declared_body_that_times_out_while_being_read_releases_everything() -> None:
    set_request_id("req_test")
    budget = BodyBudget(4 * MIB)

    async def reads(scope_: Any, receive: Any, send: Any) -> None:
        await receive()

    app = BodyBudgetMiddleware(
        reads, budget=budget, ceiling_bytes=8 * MIB, read_timeout_seconds=0.2
    )
    client = Client(app, 3 * MIB)
    with pytest.raises(AppError) as caught:
        await client.run()
    assert caught.value.status_code == 408 and budget.in_flight == 0


async def test_a_declared_body_releases_on_disconnect_cancel_and_exception() -> None:
    budget = BodyBudget(4 * MIB)

    async def explode(scope_: Any, receive: Any, send: Any) -> None:
        raise RuntimeError("boom before reading")

    for handler in (explode, read_body_then_wait):
        app = BodyBudgetMiddleware(
            handler, budget=budget, ceiling_bytes=8 * MIB, read_timeout_seconds=30
        )
        client = Client(app, 3 * MIB)
        task = client.run()
        await asyncio.sleep(0.05)
        if handler is read_body_then_wait:
            assert budget.in_flight == 3 * MIB
            task.cancel()
        with pytest.raises((asyncio.CancelledError, RuntimeError)):
            await task
        assert budget.in_flight == 0


async def test_small_declared_bodies_are_admitted_while_the_budget_is_full() -> None:
    budget = BodyBudget(4 * MIB)
    budget.in_flight = 4 * MIB
    app = BodyBudgetMiddleware(
        read_body_then_wait, budget=budget, ceiling_bytes=8 * MIB, read_timeout_seconds=30
    )
    client = Client(app, SMALL_BODY_BYTES, send_bytes=SMALL_BODY_BYTES)
    task = client.run()
    await asyncio.sleep(0.05)
    client.go.set()
    await task
    assert client.status() == 200 and budget.in_flight == 4 * MIB and budget.refused == 0

"""HTTP instrumentation: route templates, status labels, in-flight accounting, streaming."""

import asyncio
from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from abb_api.obs.catalogue import UNMATCHED, Observability, build_observability
from abb_api.obs.http import MetricsMiddleware
from tests.obs.helpers import client_for, make_app


def instrumented() -> tuple[FastAPI, Observability]:
    obs = build_observability()
    app = FastAPI()
    router = APIRouter(prefix="/v1")

    @router.get("/runs/{run_id}")
    async def run(run_id: str) -> dict[str, str]:
        return {"id": run_id}

    @router.post("/boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    @router.get("/teapot")
    async def teapot() -> None:
        raise HTTPException(status_code=418)

    @router.get("/stream")
    async def stream() -> StreamingResponse:
        async def chunks() -> AsyncIterator[bytes]:
            for i in range(3):
                yield f"data: {i}\n\n".encode()
                await asyncio.sleep(0)

        return StreamingResponse(chunks(), media_type="text/event-stream")

    app.include_router(router)
    app.add_middleware(MetricsMiddleware, catalogue=obs.catalogue)
    return app, obs


async def test_the_route_label_is_the_template_not_the_path() -> None:
    app, obs = instrumented()
    async with client_for(app) as client:
        for run_id in ("run_a", "run_b", "run_c"):
            assert (await client.get(f"/v1/runs/{run_id}")).status_code == 200
    reqs = obs.catalogue.http_requests_total
    assert reqs.value("GET", "/v1/runs/{run_id}", "200") == 3
    assert reqs.series_count() == 1
    dur = obs.catalogue.http_request_duration_seconds
    assert (
        dur.snapshot("GET", "/v1/runs/{run_id}")[0][-1]
        + sum(dur.snapshot("GET", "/v1/runs/{run_id}")[0][:-1])
        == 3
    )


async def test_unknown_paths_collapse_into_unmatched_and_odd_methods_into_other() -> None:
    app, obs = instrumented()
    async with client_for(app) as client:
        for i in range(25):
            await client.get(f"/wp-admin/{i}.php")
        await client.request("PROPFIND", "/v1/runs/x")
        await client.request("BREW", "/")
    reqs = obs.catalogue.http_requests_total
    assert reqs.value("GET", UNMATCHED, "404") == 25
    assert reqs.value("other", "/v1/runs/{run_id}", "405") == 1
    assert reqs.value("other", UNMATCHED, "404") == 1


async def test_status_labels_cover_errors_and_unhandled_exceptions() -> None:
    app, obs = instrumented()
    async with client_for(app) as client:
        assert (await client.post("/v1/boom")).status_code == 500
        assert (await client.get("/v1/teapot")).status_code == 418
        assert (await client.get("/v1/runs/x", headers={"x": "y"})).status_code == 200
        assert (await client.post("/v1/runs/x")).status_code == 405
    reqs = obs.catalogue.http_requests_total
    assert reqs.value("POST", "/v1/boom", "500") == 1
    assert reqs.value("GET", "/v1/teapot", "418") == 1
    assert reqs.value("POST", "/v1/runs/{run_id}", "405") == 1
    assert obs.catalogue.http_requests_in_flight.value() == 0


async def test_a_streaming_response_is_passed_through_untouched() -> None:
    app, obs = instrumented()
    async with client_for(app) as client:
        async with client.stream("GET", "/v1/stream") as response:
            seen = [chunk async for chunk in response.aiter_text()]
    assert "".join(seen) == "data: 0\n\ndata: 1\n\ndata: 2\n\n"
    assert response.headers["content-type"].startswith("text/event-stream")
    assert obs.catalogue.http_requests_total.value("GET", "/v1/stream", "200") == 1
    assert obs.catalogue.http_requests_in_flight.value() == 0


async def test_in_flight_is_visible_while_a_request_runs_and_zero_after_a_cancel() -> None:
    obs = build_observability()
    entered = asyncio.Event()

    async def hang(scope: Scope, receive: Receive, send: Send) -> None:
        entered.set()
        await asyncio.sleep(3600)

    app = MetricsMiddleware(hang, catalogue=obs.catalogue)
    scope: Scope = {"type": "http", "method": "GET", "path": "/slow", "headers": []}

    async def noop_receive() -> dict[str, object]:
        return {"type": "http.request"}

    async def sink(message: object) -> None:
        return None

    task = asyncio.create_task(app(scope, noop_receive, sink))
    await entered.wait()
    assert obs.catalogue.http_requests_in_flight.value() == 1
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert obs.catalogue.http_requests_in_flight.value() == 0
    assert obs.catalogue.http_requests_total.value("GET", UNMATCHED, "499") == 1


async def test_a_client_disconnect_mid_response_still_releases_the_gauge() -> None:
    obs = build_observability()

    async def app_inner(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"x", "more_body": True})
        raise OSError("client went away")

    app = MetricsMiddleware(app_inner, catalogue=obs.catalogue)
    scope: Scope = {"type": "http", "method": "GET", "path": "/", "headers": []}

    async def receive() -> dict[str, object]:
        return {"type": "http.disconnect"}

    async def sink(message: object) -> None:
        return None

    try:
        await app(scope, receive, sink)
    except OSError:
        pass
    assert obs.catalogue.http_requests_in_flight.value() == 0
    # The status that had already been sent is the one recorded.
    assert obs.catalogue.http_requests_total.value("GET", UNMATCHED, "200") == 1


async def test_non_http_scopes_pass_through_uncounted() -> None:
    obs = build_observability()
    seen: list[str] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope["type"])

    await MetricsMiddleware(inner, catalogue=obs.catalogue)({"type": "lifespan"}, None, None)  # type: ignore[arg-type]
    assert seen == ["lifespan"]
    assert obs.catalogue.http_requests_in_flight.value() == 0


async def test_refusals_from_inner_layers_are_counted_in_the_real_app() -> None:
    obs = build_observability()
    app = make_app(observability=obs, ingest_max_body_bytes=2 * 1024 * 1024)
    async with client_for(app) as client:
        # Larger than the ceiling: refused by the body-budget layer, inside the metrics layer.
        too_big = await client.post(
            "/v1/events/batch",
            content=b"x" * (9 * 1024 * 1024),
            headers={"content-length": "9437184"},
        )
        assert too_big.status_code >= 400
        assert (await client.get("/definitely/not/here")).status_code == 404
        assert (await client.get("/healthz")).status_code == 200
        pre = await client.options(
            "/healthz",
            headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
        )
        assert pre.status_code == 200
    reqs = obs.catalogue.http_requests_total
    assert reqs.value("POST", "/v1/events/batch", str(too_big.status_code)) == 1
    assert reqs.value("GET", UNMATCHED, "404") == 1
    assert reqs.value("GET", "/healthz", "200") == 1
    # The CORS layer answers the preflight before routing, so it is "unmatched" by design.
    assert reqs.value("OPTIONS", UNMATCHED, "200") == 1
    assert isinstance(too_big, httpx.Response)


class _Orig:
    sqlstate = "57014"


async def test_mapped_database_errors_are_recorded_with_their_real_status() -> None:
    # The 503/504 mapping used to run in the outermost handler, outside this middleware: the
    # counter and the request log said 500 for a response the client saw as 503 or 504.
    from sqlalchemy.exc import DBAPIError, OperationalError

    from abb_api.db.breaker import BreakerOpen
    from tests.obs.log_capture import capture_everything

    obs = build_observability()
    app = make_app(observability=obs)

    @app.get("/t/timeout")
    async def timeout() -> None:
        raise DBAPIError("SELECT secret-sql", {}, _Orig())

    @app.get("/t/connect")
    async def connect() -> None:
        raise OperationalError("SELECT secret-sql", {}, ConnectionRefusedError("refused"))

    @app.get("/t/os")
    async def os_error() -> None:
        raise ConnectionResetError("reset")

    @app.get("/t/breaker")
    async def breaker() -> None:
        raise BreakerOpen(4)

    @app.get("/t/unknown")
    async def unknown() -> None:
        raise RuntimeError("boom")

    with capture_everything() as captured:
        async with client_for(app) as client:
            timed_out = await client.get("/t/timeout")
            refused = await client.get("/t/connect")
            reset = await client.get("/t/os")
            shed = await client.get("/t/breaker")
            unknown_response = await client.get("/t/unknown")
    assert timed_out.status_code == 504
    assert timed_out.json()["error"]["code"] == "DATABASE_TIMEOUT"
    assert refused.status_code == 503 and reset.status_code == 503
    assert shed.status_code == 503 and shed.headers["retry-after"] == "4"
    assert refused.headers["retry-after"] == "2"
    assert unknown_response.status_code == 500
    assert "secret-sql" not in timed_out.text + refused.text
    reqs = obs.catalogue.http_requests_total
    assert reqs.value("GET", "/t/timeout", "504") == 1
    assert reqs.value("GET", "/t/connect", "503") == 1
    assert reqs.value("GET", "/t/os", "503") == 1
    assert reqs.value("GET", "/t/breaker", "503") == 1
    assert reqs.value("GET", "/t/unknown", "500") == 1
    logged = {
        r.__dict__["route"]: r.__dict__["status"]
        for r in captured.records
        if r.getMessage() == "request" and "route" in r.__dict__
    }
    assert logged["/t/timeout"] == 504
    assert logged["/t/connect"] == 503
    assert logged["/t/breaker"] == 503
    assert logged["/t/unknown"] == 500

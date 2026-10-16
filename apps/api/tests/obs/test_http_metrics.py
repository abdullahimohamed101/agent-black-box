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

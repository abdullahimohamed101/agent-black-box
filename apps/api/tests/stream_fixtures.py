"""A real HTTP server for streaming tests (ASGITransport buffers whole responses)."""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI

from abb_api.main import create_app
from tests.api_fixtures import Api
from tests.conftest import make_settings, runtime_url
from tests.ingest_helpers import wire_event

__all__ = ["Live", "messages", "send", "serve"]


@dataclass
class Live:
    base_url: str
    app: FastAPI
    api: Api

    def headers(self, token: str = "reader", **extra: str) -> dict[str, str]:
        return {"authorization": f"Bearer {self.api.tokens.get(token, token)}", **extra}

    def open(
        self, run_id: str, *, token: str = "reader", timeout: float = 10, **params: Any
    ) -> Any:
        """`async with live.open(...) as response` streaming `GET /stream`."""
        client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)
        headers = self.headers(token, **{k: v for k, v in params.pop("headers", {}).items()})
        return _Stream(client, f"/v1/runs/{run_id}/stream", params, headers)


class _Stream:
    def __init__(
        self, client: httpx.AsyncClient, path: str, params: dict[str, Any], headers: dict[str, str]
    ):
        self._client, self._path, self._params, self._headers = client, path, params, headers
        self._cm: Any = None

    async def __aenter__(self) -> httpx.Response:
        self._cm = self._client.stream(
            "GET", self._path, params=self._params, headers=self._headers
        )
        return await self._cm.__aenter__()  # type: ignore[no-any-return]

    async def __aexit__(self, *exc: object) -> None:
        try:
            await self._cm.__aexit__(*exc)
        finally:
            await self._client.aclose()


async def serve(api: Api, runtime_database_url: str, **settings: Any) -> AsyncIterator[Live]:
    """The same database and keys as `api`, served over a socket with fast stream timings."""
    base = make_settings(runtime_url(runtime_database_url))
    timings = {
        "stream_fallback_poll_seconds": 0.2,
        "stream_keepalive_seconds": 5.0,
        "stream_end_quiet_seconds": 0.5,
        "stream_overlap_seconds": 30.0,
        "stream_window_check_seconds": 0.2,
    }
    app = create_app(base.model_copy(update={**timings, **settings}), clock=api.clock)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            log_level="warning",
            lifespan="on",
            timeout_graceful_shutdown=1,
        )
    )
    task = asyncio.get_running_loop().create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError("test server did not start")
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield Live(f"http://127.0.0.1:{port}", app, api)
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)


async def send(api: Api, run: dict[str, str], n: int, **overrides: Any) -> dict[str, Any]:
    """Ingest one event; each call advances the server clock so arrival times are distinct."""
    api.clock.now += timedelta(seconds=1)
    event = wire_event(run, n, **overrides)
    response = await api.post_batch([event])
    assert response.status_code == 202, response.text
    return event


async def messages(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    """Parse an SSE body into {event, id, data} messages and {comment} entries."""
    current: dict[str, Any] = {}
    async for raw in response.aiter_lines():
        line = raw.rstrip("\r")
        if line == "":
            if current:
                yield current
            current = {}
        elif line.startswith(":"):
            yield {"comment": line[1:].strip()}
        else:
            name, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if name == "data":
                current["data"] = json.loads(value)
            else:
                current[name] = value
    if current:
        yield current

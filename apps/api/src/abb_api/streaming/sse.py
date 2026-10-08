"""Server-Sent Events framing and a response that survives slow or vanished clients (spec §76.1)."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

RETRY_MS = 3000  # how long a browser waits before reconnecting


def retry_frame() -> bytes:
    return f"retry: {RETRY_MS}\n\n".encode()


def comment(text: str) -> bytes:
    return f": {text}\n\n".encode()


def frame(event: str, data: str, event_id: str | None = None) -> bytes:
    """One SSE message. `data` must be a single line (JSON without raw newlines)."""
    if "\n" in data or "\r" in data:
        raise ValueError("SSE data must be one line")
    head = f"id: {event_id}\n" if event_id else ""
    return f"{head}event: {event}\ndata: {data}\n\n".encode()


def json_frame(event: str, payload: Any, event_id: str | None = None) -> bytes:
    return frame(event, json.dumps(payload, separators=(",", ":"), ensure_ascii=False), event_id)


class SseResponse(StreamingResponse):
    """A stream that gives up on a client that stops reading and always runs its cleanup."""

    def __init__(
        self,
        content: AsyncIterator[bytes],
        *,
        write_timeout: float,
        on_close: Callable[[], None],
    ) -> None:
        super().__init__(
            content,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",  # proxies must not buffer a live stream
            },
        )
        self._write_timeout = write_timeout
        self._on_close = on_close

    async def stream_response(self, send: Send) -> None:
        await send(
            {"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers}
        )
        iterator = self.body_iterator
        try:
            async for chunk in iterator:
                body = chunk.encode() if isinstance(chunk, str) else bytes(chunk)
                # A client that stops reading would otherwise hold this slot (and its task) forever.
                await asyncio.wait_for(
                    send({"type": "http.response.body", "body": body, "more_body": True}),
                    self._write_timeout,
                )
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            aclose = getattr(iterator, "aclose", None)
            if aclose is not None:
                await aclose()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._on_close()

"""Pure ASGI middleware: request IDs, access logging, unhandled-error logging."""

import logging
import time

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from abb_api.core.request_context import new_request_id, sanitize_inbound, set_request_id

logger = logging.getLogger("abb_api.request")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = sanitize_inbound(Headers(scope=scope).get("x-request-id")) or new_request_id()
        set_request_id(request_id)
        started = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                # API responses carry tenant data: never sniffed as HTML, never cached by proxies.
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Cache-Control"] = "no-store"
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            logger.exception("unhandled error", extra={"path": scope.get("path")})
            raise
        finally:
            logger.info(
                "request",
                extra={
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )

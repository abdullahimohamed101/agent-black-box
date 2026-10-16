"""HTTP instrumentation as a pure ASGI middleware (ADR-165).

Outermost on purpose: a request refused by an inner layer (the body budget, CORS, an unmatched
path) is still counted with its real status. The route label is the matched route *template*
(``/v1/runs/{run_id}``), read from the scope after the app ran; a request that matched nothing is
``unmatched``, so a scanner walking random URLs adds one series, not thousands. The method label is
a closed set. The response is passed through untouched, chunk by chunk, so streaming (SSE) works.
"""

import asyncio
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from abb_api.obs.catalogue import UNMATCHED, Catalogue

# Status recorded when no response was started: the client went away or the task was cancelled.
CLIENT_GONE_STATUS = 499


def route_template(scope: Scope) -> str:
    route = scope.get("route")
    template = getattr(route, "path_format", None) or getattr(route, "path", None)
    return template if isinstance(template, str) and template else UNMATCHED


class MetricsMiddleware:
    def __init__(self, app: ASGIApp, *, catalogue: Catalogue) -> None:
        self.app = app
        self._requests = catalogue.http_requests_total
        self._duration = catalogue.http_request_duration_seconds
        self._in_flight = catalogue.http_requests_in_flight

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status = 0
        self._in_flight.inc()

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except asyncio.CancelledError:
            status = status or CLIENT_GONE_STATUS
            raise
        except BaseException:
            status = status or 500
            raise
        finally:
            self._in_flight.dec()
            method = scope.get("method", "")
            route = route_template(scope)
            self._requests.inc(method, route, str(status or 500))
            self._duration.observe(time.perf_counter() - started, method, route)

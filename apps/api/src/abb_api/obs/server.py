"""The worker's metrics endpoint: a tiny stdlib HTTP server in a daemon thread (ADR-165).

The worker has no web framework, so it serves the same registry with ``http.server``. One route
(``GET /metrics``), the same bearer token as the API, loopback by default, and hard bounds: a
socket timeout per connection, a cap on concurrent connections (extra ones are closed at once),
a total wall-clock deadline per connection (a client sending a byte every few seconds resets the
socket timeout forever, but not this), the stdlib's own request-line and header limits, no request body read, and the same global rate
bucket as the API route. Nothing about the caller is logged.
"""

import contextlib
import json
import logging
import socket
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Final

from pydantic import SecretStr

from abb_api.ingestion.ratelimit import InMemoryRateLimiter
from abb_api.obs.access import MetricsAuth
from abb_api.obs.metrics import CONTENT_TYPE, Registry

logger = logging.getLogger(__name__)

CONNECTION_TIMEOUT_SECONDS: Final = 5.0
MAX_CONCURRENT_CONNECTIONS: Final = 8
CONNECTION_DEADLINE_SECONDS: Final = 10.0

_UNAUTHORIZED: Final = json.dumps(
    {
        "error": {
            "code": "METRICS_TOKEN_INVALID",
            "message": "The metrics token is missing or invalid.",
            "category": "AUTHENTICATION",
            "retryable": False,
            "request_id": None,
            "details": {},
        }
    }
).encode()
_NOT_FOUND: Final = b'{"error":{"code":"NOT_FOUND","message":"Not found."}}'
_LIMITED: Final = b'{"error":{"code":"RATE_LIMITED","message":"Too many requests."}}'


def _close_socket(sock: Any) -> None:
    with contextlib.suppress(OSError):
        sock.shutdown(socket.SHUT_RDWR)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        owner: "MetricsServer",
        deadline_seconds: float = CONNECTION_DEADLINE_SECONDS,
    ) -> None:
        self.owner = owner
        self._deadline = deadline_seconds
        self._slots = threading.BoundedSemaphore(MAX_CONCURRENT_CONNECTIONS)
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        super().__init__(address, handler)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)  # full: drop the connection, do no work
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        # The per-read socket timeout restarts on every byte; this one does not. Closing the socket
        # from the timer makes the blocked read in the handler fail, which frees the slot.
        timer = threading.Timer(self._deadline, _close_socket, args=(request,))
        timer.daemon = True
        timer.start()
        try:
            super().process_request_thread(request, client_address)
        finally:
            timer.cancel()
            self._slots.release()

    def handle_error(self, request: Any, client_address: Any) -> None:
        # Never print the stdlib's traceback with the peer address; one bounded log line.
        logger.warning("worker metrics connection failed")


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    timeout = CONNECTION_TIMEOUT_SECONDS
    protocol_version = "HTTP/1.0"  # one request per connection: nothing to keep alive or pipeline

    def log_message(self, format: str, *args: object) -> None:
        return None  # the default logs the client address to stderr

    def _reply(self, status: int, body: bytes, content_type: str, **headers: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in headers.items():
            self.send_header(name.replace("_", "-"), value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:
        owner = self.server.owner
        if self.path.split("?", 1)[0] != "/metrics":
            self._reply(404, _NOT_FOUND, "application/json")
        elif owner.rate_limited():
            self._reply(429, _LIMITED, "application/json", Retry_After="5")
        elif not owner.auth.allows(self.headers.get("Authorization")):
            self._reply(401, _UNAUTHORIZED, "application/json", WWW_Authenticate="Bearer")
        else:
            owner.prepare()
            self._reply(200, owner.registry.render().encode(), CONTENT_TYPE)

    def _method_not_allowed(self) -> None:
        self._reply(405, _NOT_FOUND, "application/json", Allow="GET")

    do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _method_not_allowed


class MetricsServer:
    def __init__(
        self,
        registry: Registry,
        token: SecretStr,
        *,
        bind: str = "127.0.0.1",
        port: int,
        requests_per_minute: int = 120,
        before_render: Callable[[], None] | None = None,
        connection_deadline_seconds: float = CONNECTION_DEADLINE_SECONDS,
    ) -> None:
        self.registry = registry
        self._before_render = before_render
        self.auth = MetricsAuth(token)
        self.limiter = InMemoryRateLimiter(
            events_per_second=requests_per_minute / 60,
            burst_events=requests_per_minute,
            bytes_per_second=1.0,
            burst_bytes=1,
        )
        self._limiter_lock = threading.Lock()  # one handler thread per connection
        self._connection_deadline = connection_deadline_seconds
        self._bind = bind
        self._port = port
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None

    def rate_limited(self) -> bool:
        """Takes one token of the global bucket; True when there was none. Thread-safe: the
        limiter's read-modify-write is not, and handler threads run concurrently."""
        with self._limiter_lock:
            return self.limiter.acquire("metrics", events=1, bytes_=0) is not None

    def prepare(self) -> None:
        """Refresh scrape-time gauges (the worker bridges to its event loop). Never raises."""
        if self._before_render is None:
            return
        try:
            self._before_render()
        except Exception as exc:  # a scrape must not fail on the database
            logger.warning("worker scrape sample failed", extra={"error_type": type(exc).__name__})

    @property
    def port(self) -> int:
        assert self._server is not None, "not started"
        return int(self._server.server_address[1])

    def start(self) -> None:
        self._server = _Server((self._bind, self._port), _Handler, self, self._connection_deadline)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.2},
            name="abb-worker-metrics",
            daemon=True,
        )
        self._thread.start()
        logger.info("worker metrics listening", extra={"bind": self._bind, "port": self.port})

    def stop(self) -> None:
        server, thread = self._server, self._thread
        self._server = self._thread = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=5)

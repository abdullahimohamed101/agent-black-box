"""A threaded fake of the policy endpoints (ADR-141) with scriptable behaviour per route.

Routes: decide, wait, consume, cancel, failmodes, plus the event batch endpoint (so the SDK's
`policy.warning` events can be read back). Each route has a queue of scripted replies and a
default used when the queue is empty. A reply can delay, hang, drop the connection or send junk.
"""

import gzip
import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from blackbox import BlackBox

ROUTES = ("decide", "wait", "consume", "cancel", "failmodes")


@dataclass
class R:
    status: int = 200
    body: Any = None  # dict -> JSON, bytes -> raw
    headers: dict[str, str] = field(default_factory=dict)
    delay: float = 0.0
    drop: bool = False  # close the socket without answering
    hold: float = 0.0  # stay silent this long, then drop


@dataclass
class Req:
    method: str
    path: str
    route: str
    headers: dict[str, str]
    body: dict[str, Any] | None
    at: float


def iso(seconds_from_now: float) -> str:
    when = datetime.now(timezone.utc) + timedelta(seconds=seconds_from_now)
    return when.isoformat(timespec="microseconds").replace("+00:00", "Z")


def allow(**extra: Any) -> R:
    return R(
        200,
        {
            "decision": "allow",
            "policy_id": None,
            "policy_version": 3,
            "reason": "default",
            "on_error": "open",
            **extra,
        },
    )


def deny(reason: str = "no .env", policy_id: str = "deny-env", **extra: Any) -> R:
    return R(
        200,
        {
            "decision": "deny",
            "policy_id": policy_id,
            "policy_version": 3,
            "reason": reason,
            "on_error": "closed",
            **extra,
        },
    )


def require(
    approval_id: str = "apr_01", expires_in: float = 60.0, on_expiry: str = "deny", **extra: Any
) -> R:
    return R(
        200,
        {
            "decision": "require_approval",
            "policy_id": "gate-push",
            "policy_version": 3,
            "reason": "needs a person",
            "on_error": "closed",
            "approval_id": approval_id,
            "approval_state": "REQUESTED",
            "expires_at": iso(expires_in),
            "on_expiry": on_expiry,
            **extra,
        },
    )


def state(name: str, approval_id: str = "apr_01", on_expiry: str = "deny", **extra: Any) -> R:
    return R(
        200,
        {
            "approval_id": approval_id,
            "state": name,
            "expires_at": iso(60),
            "on_expiry": on_expiry,
            "policy_id": "gate-push",
            "policy_version": 3,
            "consumed": False,
            **extra,
        },
    )


def consumed(approval_id: str = "apr_01") -> R:
    return R(200, {"approval_id": approval_id, "consumed_at": iso(0)})


def refused(code: str, status: int = 409) -> R:
    return R(status, {"error": {"code": code, "message": "no"}})


def table(version: int = 3, rules: list[Any] | None = None, **defaults: str) -> R:
    return R(
        200,
        {
            "project_id": "prj_x",
            "source": "project",
            "policy_version": version,
            "rules": rules or [],
            "defaults": {
                "R0": "open",
                "R1": "open",
                "R2": "open",
                "R3": "closed",
                "R4": "closed",
                "unclassified": "open",
                **defaults,
            },
        },
    )


class PolicyServer:
    def __init__(self, tls: tuple[str, str] | None = None) -> None:
        self.requests: list[Req] = []
        self.event_batches: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.script: dict[str, deque[R]] = {r: deque() for r in ROUTES}
        self.default: dict[str, R] = {
            "decide": allow(),
            "wait": R(200, state("REQUESTED").body, delay=0.02),  # a long poll, not a spin
            "consume": consumed(),
            "cancel": R(204),
            "failmodes": table(),
        }
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _serve(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b""
                if self.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                body: Any = json.loads(raw) if raw else None
                path = self.path.split("?", 1)[0]
                route = _route(self.command, path)
                with outer.lock:
                    outer.requests.append(
                        Req(
                            self.command,
                            self.path,
                            route,
                            {k.lower(): v for k, v in self.headers.items()},
                            body,
                            time.monotonic(),
                        )
                    )
                if route == "events":
                    with outer.lock:
                        outer.event_batches.append(body)
                    reply = R(
                        202,
                        {
                            "batch_id": body.get("batch_id"),
                            "accepted": len(body["events"]),
                            "duplicates": 0,
                            "conflicts": 0,
                            "rejected": 0,
                            "errors": [],
                        },
                    )
                else:
                    with outer.lock:
                        queue = outer.script.get(route)
                        reply = queue.popleft() if queue else outer.default.get(route, R(404))
                if reply.delay:
                    time.sleep(reply.delay)
                if reply.hold:
                    time.sleep(reply.hold)
                    self.close_connection = True
                    return
                if reply.drop:
                    self.close_connection = True
                    return
                payload = (
                    reply.body
                    if isinstance(reply.body, bytes)
                    else json.dumps(reply.body).encode()
                    if reply.body is not None
                    else b""
                )
                self.send_response(reply.status)
                for key, value in reply.headers.items():
                    self.send_header(key, value)
                if payload:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except OSError:
                    pass

            do_GET = do_POST = do_DELETE = _serve

            def log_message(self, *args: Any) -> None:
                return None

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.scheme = "http"
        if tls is not None:  # (certificate file, key file)
            import ssl

            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(*tls)
            self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
            self.scheme = "https"
        threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True
        ).start()

    @property
    def url(self) -> str:
        return f"{self.scheme}://127.0.0.1:{self.server.server_address[1]}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def queue(self, route: str, *replies: R) -> None:
        with self.lock:
            self.script[route].extend(replies)

    def of(self, route: str) -> list[Req]:
        with self.lock:
            return [r for r in self.requests if r.route == route]

    def events(self) -> list[dict[str, Any]]:
        with self.lock:
            return [e for batch in self.event_batches for e in batch["events"]]


def _route(method: str, path: str) -> str:
    if path == "/v1/events/batch":
        return "events"
    if path == "/v1/policy/decide":
        return "decide"
    if path == "/v1/policies/fail-modes":
        return "failmodes"
    if path.startswith("/v1/approvals/"):
        if path.endswith("/wait"):
            return "wait"
        if path.endswith("/consume"):
            return "consume"
        if method == "DELETE":
            return "cancel"
    return "unknown"


def make_client(server: PolicyServer, **options: Any) -> BlackBox:
    options.setdefault("policy_mode", "enforce")
    options.setdefault("flush_interval", 0.02)
    return BlackBox(api_key="abb_live_test.secret", project="demo", endpoint=server.url, **options)


def warnings_of(server: PolicyServer, bb: BlackBox) -> list[dict[str, Any]]:
    bb.flush(3)
    return [e for e in server.events() if e["event_type"] == "policy.warning"]

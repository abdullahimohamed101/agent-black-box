"""Test helpers: an offline client whose events can be inspected, and a stub ingestion server."""

import gzip
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from blackbox import BlackBox


def offline(**options: Any) -> BlackBox:
    options.setdefault("mode", "offline")
    return BlackBox(api_key="abb_live_test.secret", project="demo", **options)


def events_of(bb: BlackBox) -> list[dict[str, Any]]:
    """Everything queued, in creation order."""
    return sorted(bb.buffered_events(), key=lambda e: (e["run_id"], e["sequence"]))


def types_of(events: list[dict[str, Any]]) -> list[str]:
    return [e["event_type"] for e in events]


@dataclass
class Received:
    path: str
    headers: dict[str, str]
    body: dict[str, Any]
    raw_size: int


@dataclass
class StubServer:
    """Replies from `script` in order (then 202); records every request."""

    script: list[tuple[int, dict[str, str], bytes]] = field(default_factory=list)
    received: list[Received] = field(default_factory=list)
    delay: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)
    server: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        assert self.server is not None
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def events(self) -> list[dict[str, Any]]:
        return [e for r in self.received for e in r.body["events"]]


def start_stub(
    script: list[tuple[int, dict[str, str], bytes]] | None = None,
    *,
    delay: float = 0.0,
    respond: Callable[[dict[str, Any]], bytes] | None = None,
) -> StubServer:
    stub = StubServer(script=list(script or []), delay=delay)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            data = gzip.decompress(raw) if self.headers.get("Content-Encoding") == "gzip" else raw
            body = json.loads(data)
            with stub.lock:
                stub.received.append(
                    Received(
                        self.path, {k.lower(): v for k, v in self.headers.items()}, body, len(raw)
                    )
                )
                status, headers, payload = stub.script.pop(0) if stub.script else (202, {}, b"")
            if stub.delay:
                threading.Event().wait(stub.delay)
            if status == 202 and not payload:
                n = len(body["events"])
                payload = (
                    respond(body)
                    if respond
                    else json.dumps(
                        {
                            "batch_id": body.get("batch_id"),
                            "accepted": n,
                            "duplicates": 0,
                            "conflicts": 0,
                            "rejected": 0,
                            "errors": [],
                        }
                    ).encode()
                )
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: Any) -> None:
            return None

    stub.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    stub.server.daemon_threads = True
    threading.Thread(
        target=lambda: stub.server.serve_forever(poll_interval=0.01) if stub.server else None,
        daemon=True,
    ).start()
    return stub


def stop_stub(stub: StubServer) -> None:
    assert stub.server is not None
    stub.server.shutdown()
    stub.server.server_close()

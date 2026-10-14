"""A stub policy service for adapter tests (Phase 14, D5).

Adapters that execute tools themselves (MCP) call `guard()` before the call; adapters that do not
(LangGraph, OpenAI, Anthropic) document `@guarded` on the tool function. Both need a policy service
to talk to, and the adapters' tests must not depend on the API. `PolicyStub` answers
`POST /v1/policy/decide` from a table (capability -> allow or deny) and accepts event batches, on
localhost only, recording every intent it was asked about.
"""

import gzip
import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from blackbox import BlackBox, PolicyDenied, guarded


class PolicyStub:
    def __init__(self, deny: dict[str, str] | None = None) -> None:
        self.deny = dict(deny or {})  # capability -> the policy's reason
        self.intents: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # the fail-mode table: nothing special
                self._reply(200, {"policy_version": 1, "rules": [], "defaults": {}})

            def do_POST(self) -> None:
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if self.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                body = json.loads(raw)
                if self.path == "/v1/events/batch":
                    with stub.lock:
                        stub.events.extend(body["events"])
                    self._reply(202, {"accepted": len(body["events"]), "rejected": 0})
                    return
                with stub.lock:
                    stub.intents.append(body)
                reason = stub.deny.get(body["capability"])
                verdict = {
                    "decision": "deny" if reason else "allow",
                    "policy_id": "stub-rule" if reason else None,
                    "policy_version": 1,
                    "reason": reason or "allowed",
                    "on_error": "open",
                }
                self._reply(200, verdict)

            def _reply(self, status: int, payload: dict[str, Any]) -> None:
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: Any) -> None:
                return None

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True
        ).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def client(self, **options: Any) -> BlackBox:
        options.setdefault("policy_timeout", 1.0)
        return BlackBox(
            api_key="abb_live_conformance.secret",
            project="conformance",
            endpoint=self.url,
            mode="http",
            policy_mode="enforce",
            **options,
        )


@contextmanager
def policy_stub(deny: dict[str, str] | None = None) -> Iterator[PolicyStub]:
    stub = PolicyStub(deny)
    try:
        yield stub
    finally:
        stub.stop()


Decorate = Callable[[Callable[..., Any]], Callable[..., Any]]


def check_guarded_tool(
    build: Callable[[Decorate, list[str], BlackBox], Callable[[str], Any]],
) -> None:
    """The contract for adapters that do not run tools themselves (D5): `@guarded` on the tool.

    `build(decorate, ran, bb)` returns a callable that drives the adapter's own tool path with one
    string argument; the tool body it wraps with `decorate` must append to `ran` when it runs. The
    check calls it for an allowed capability and a denied one: the allowed body runs once; the
    denied call raises `PolicyDenied` before the body runs; each call asks the service once.
    """
    with policy_stub({"tool.deleter": "No deleting."}) as stub:
        bb = stub.client()
        try:
            with bb.run("conformance"):
                ran: list[str] = []

                def decorator(capability: str) -> Decorate:
                    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
                        return guarded(capability, resource=lambda arg: arg, client=bb)(fn)

                    return decorate

                build(decorator("tool.reader"), ran, bb)("x")
                if ran != ["ran"]:
                    raise AssertionError("an allowed guarded tool must run once")
                try:
                    build(decorator("tool.deleter"), ran, bb)("y")
                except PolicyDenied as denied:
                    if ran != ["ran"] or denied.policy_id != "stub-rule":
                        raise AssertionError("a denied tool must not run") from None
                else:
                    raise AssertionError("a denied guarded tool must raise PolicyDenied")
        finally:
            bb.shutdown(1)
        if [i["capability"] for i in stub.intents] != ["tool.reader", "tool.deleter"]:
            raise AssertionError("each call must ask the policy service exactly once")
        if [i["resource"] for i in stub.intents] != ["x", "y"]:
            raise AssertionError("the resource callable must receive the tool's own arguments")

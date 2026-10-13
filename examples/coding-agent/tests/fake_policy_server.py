"""A fake of the server for the policy scenario: events, decide, wait, consume, cancel.

It decides the way `policies/starter.yaml` does for the intents the scenario produces (deny
`filesystem.read` of `.env*`, approval for `github.push` to main or production, allow the rest),
so the test shows the scenario end to end without the API. The real API is exercised by
`scripts/policy-e2e.sh`.
"""

import gzip
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class FakePolicyServer:
    def __init__(self, approval: str = "approve", approve_after_polls: int = 2) -> None:
        self.approval = approval  # approve | deny | never
        self.approve_after = approve_after_polls
        self.polls = 0
        self.lock = threading.Lock()
        self.decided: list[dict[str, Any]] = []
        self.consumed: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self.events: list[dict[str, Any]] = []
        self.intent: dict[str, Any] | None = None
        self.spent = False
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _body(self) -> Any:
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if self.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw) if raw else None

            def _send(self, status: int, payload: Any = None) -> None:
                data = json.dumps(payload).encode() if payload is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self) -> None:
                body = self._body()
                path = self.path.split("?")[0]
                if path == "/v1/events/batch":
                    with outer.lock:
                        outer.events.extend(body["events"])
                    self._send(202, {"accepted": len(body["events"]), "rejected": 0})
                elif path == "/v1/policy/decide":
                    self._send(200, outer.decide(body))
                elif path.endswith("/consume"):
                    self._send(*outer.consume(body["intent"]))
                else:
                    self._send(404, {"error": {"code": "NOT_FOUND"}})

            def do_GET(self) -> None:
                path = self.path.split("?")[0]
                if path.endswith("/wait"):
                    self._send(200, outer.wait())
                elif path == "/v1/policies/fail-modes":
                    self._send(200, {"policy_version": 1, "rules": [], "defaults": {}})
                else:
                    self._send(404, {"error": {"code": "NOT_FOUND"}})

            def do_PUT(self) -> None:  # artifact uploads: accept and drop
                self._body()
                self._send(201, {})

            def do_DELETE(self) -> None:
                with outer.lock:
                    outer.cancelled.append(self.path)
                self._send(204)

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

    # -- the policy: policies/starter.yaml for the scenario's intents ----------------------------

    def decide(self, intent: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            self.decided.append(intent)
        base = {"policy_version": 1, "on_error": "open", "approval_id": None}
        capability, resource = intent["capability"], intent["resource"]
        branch = intent["arguments"].get("branch")
        if capability == "filesystem.read" and resource.rsplit("/", 1)[-1].startswith(".env"):
            return {**base, "decision": "deny", "policy_id": "deny-env-read",
                    "reason": "Environment files hold secrets, so agents do not read them."}  # fmt: skip
        if capability == "github.push" and branch in ("main", "production"):
            self.intent = {k: intent[k] for k in intent if k not in ("trace_id", "idempotency_key")}
            expires = datetime.now(timezone.utc) + timedelta(minutes=15)
            return {**base, "decision": "require_approval", "policy_id": "approve-push-main",
                    "reason": "A push to main needs a person to approve it.",
                    "approval_id": "apr_demo", "approval_state": "REQUESTED", "on_error": "closed",
                    "expires_at": expires.isoformat().replace("+00:00", "Z"), "on_expiry": "deny"}  # fmt: skip
        return {**base, "decision": "allow", "policy_id": None, "reason": "allowed by default"}

    def wait(self) -> dict[str, Any]:
        time.sleep(0.05)
        with self.lock:
            self.polls += 1
            state = "REQUESTED"
            if self.approval != "never" and self.polls >= self.approve_after:
                state = "APPROVED" if self.approval == "approve" else "DENIED"
        expires = datetime.now(timezone.utc) + timedelta(minutes=14)
        return {"approval_id": "apr_demo", "state": state, "policy_id": "approve-push-main",
                "policy_version": 1, "on_expiry": "deny", "consumed": self.spent,
                "expires_at": expires.isoformat().replace("+00:00", "Z")}  # fmt: skip

    def consume(self, intent: dict[str, Any]) -> tuple[int, Any]:
        with self.lock:
            self.consumed.append(intent)
            if self.spent:
                return 409, {"error": {"code": "APPROVAL_CONSUMED"}}
            if intent != self.intent:
                return 409, {"error": {"code": "APPROVAL_INTENT_MISMATCH"}}
            self.spent = True
        return 200, {
            "approval_id": "apr_demo",
            "consumed_at": datetime.now(timezone.utc).isoformat(),
        }

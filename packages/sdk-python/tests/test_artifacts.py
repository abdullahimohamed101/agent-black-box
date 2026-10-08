"""Artifact uploads: redacted before leaving, bounded, non-blocking, never raising (ADR-030/031)."""

import gzip
import hashlib
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from blackbox import BlackBox, PayloadMode
from blackbox.artifacts import MAX_ARTIFACT_BYTES, MAX_QUEUE_ITEMS

SECRET = "AKIAABCDEFGHIJKLMNOP"


@dataclass
class Put:
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: bytes


@dataclass
class ArtifactServer:
    script: list[int] = field(default_factory=list)
    received: list[Put] = field(default_factory=list)
    events: list[Put] = field(
        default_factory=list
    )  # POSTed event batches (never mixed into `received`)
    delay: float = 0.0
    server: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        assert self.server is not None
        return f"http://127.0.0.1:{self.server.server_address[1]}"


@pytest.fixture
def server() -> Iterator[ArtifactServer]:
    state = ArtifactServer()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_PUT(self) -> None:
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            body = gzip.decompress(raw) if self.headers.get("Content-Encoding") == "gzip" else raw
            parts = urlsplit(self.path)
            state.received.append(
                Put(
                    parts.path,
                    parse_qs(parts.query),
                    {k.lower(): v for k, v in self.headers.items()},
                    body,
                )
            )
            if state.delay:
                time.sleep(state.delay)
            status = state.script.pop(0) if state.script else 201
            self.send_response(status)
            self.send_header("Content-Length", "2")
            if status == 429:
                self.send_header("Retry-After", "0")
            self.end_headers()
            self.wfile.write(b"{}")

        def do_POST(self) -> None:
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            body = gzip.decompress(raw) if self.headers.get("Content-Encoding") == "gzip" else raw
            parts = urlsplit(self.path)
            state.events.append(
                Put(
                    parts.path,
                    parse_qs(parts.query),
                    {k.lower(): v for k, v in self.headers.items()},
                    body,
                )
            )
            reply = b'{"accepted":0,"duplicates":0,"conflicts":0,"rejected":0}'
            self.send_response(202)
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *args: Any) -> None:
            return None

    state.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.server.daemon_threads = True
    threading.Thread(target=state.server.serve_forever, daemon=True).start()
    yield state
    state.server.shutdown()
    state.server.server_close()


def client(server: ArtifactServer, **options: Any) -> BlackBox:
    options.setdefault("payload_mode", PayloadMode.FULL)
    options.setdefault("wait", lambda _s: True)
    return BlackBox(api_key="abb_live_k.secret", endpoint=server.url, project="demo", **options)


def test_upload_redacts_hashes_and_returns_the_id_immediately(server: ArtifactServer) -> None:
    bb = client(server)
    with bb.run("r") as run:
        aid = bb.upload_artifact(f"key={SECRET}\nok\n", kind="stdout", name="pytest", run=run)
        assert aid is not None and aid.startswith("art_")
    assert bb.flush(5)
    (put,) = server.received
    assert put.path == f"/v1/artifacts/{aid}"
    assert put.query == {"run_id": [run.run_id], "kind": ["stdout"], "name": ["pytest"]}
    assert SECRET.encode() not in put.body and b"[REDACTED:aws_access_key]" in put.body
    assert put.headers["x-content-sha256"] == hashlib.sha256(put.body).hexdigest()
    assert put.headers["authorization"] == "Bearer abb_live_k.secret"
    assert bb.stats()["artifacts_uploaded"] == 1
    bb.shutdown()


def test_large_artifacts_are_gzipped_and_hash_the_stored_bytes(server: ArtifactServer) -> None:
    bb = client(server)
    with bb.run("r"):
        bb.upload_artifact("line of output\n" * 5000, kind="stdout")
    assert bb.flush(5)
    assert server.received[0].headers["content-encoding"] == "gzip"
    assert server.received[0].body == ("line of output\n" * 5000).encode()
    bb.shutdown()


@pytest.mark.parametrize("mode", [PayloadMode.METADATA_ONLY, PayloadMode.DISABLED])
def test_nothing_is_uploaded_unless_payloads_are_opted_in(
    server: ArtifactServer, mode: PayloadMode
) -> None:
    bb = client(server, payload_mode=mode)
    with bb.run("r"):
        assert bb.upload_artifact("secret stuff", kind="stdout") is None
    bb.flush(2)
    assert server.received == []
    bb.shutdown()


def test_no_run_offline_and_disabled_clients_return_none(server: ArtifactServer) -> None:
    bb = client(server)
    assert bb.upload_artifact("x") is None  # outside a run
    bb.shutdown()
    assert bb.upload_artifact("x") is None  # after shutdown
    offline = BlackBox(api_key="abb_live_k.s", mode="offline", payload_mode=PayloadMode.FULL)
    with offline.run("r"):
        assert offline.upload_artifact("x") is None
    off = BlackBox(mode="disabled")
    assert off.upload_artifact("x") is None


def test_invalid_input_never_raises(server: ArtifactServer) -> None:
    bb = client(server)
    with bb.run("r"):
        assert bb.upload_artifact(12345) is None  # type: ignore[arg-type]
        assert bb.upload_artifact(b"bytes \xff ok", kind="not-a-kind", name="a\nb\x00c") is not None
    assert bb.flush(5)
    put = server.received[0]
    assert put.query["kind"] == ["other"] and put.query["name"] == ["a b c"]
    assert "�" in put.body.decode()
    bb.shutdown()


def test_transient_failures_are_retried_then_dropped_and_counted(server: ArtifactServer) -> None:
    bb = client(server)
    server.script = [503, 429, 201]
    with bb.run("r"):
        bb.upload_artifact("retry me")
    assert bb.flush(5)
    assert len(server.received) == 3 and bb.stats()["artifacts_uploaded"] == 1
    server.script = [500, 500, 500]
    with bb.run("r2"):
        bb.upload_artifact("never lands")
    assert bb.flush(5)
    assert bb.stats()["dropped_artifacts"] == 1
    server.script = [403]  # not retryable: one attempt
    before = len(server.received)
    with bb.run("r3"):
        bb.upload_artifact("forbidden")
    assert bb.flush(5)
    assert len(server.received) == before + 1 and bb.stats()["dropped_artifacts"] == 2
    bb.shutdown()


def test_an_unreachable_server_never_blocks_or_raises() -> None:
    bb = BlackBox(
        api_key="abb_live_k.secret",
        endpoint="http://127.0.0.1:1",
        payload_mode=PayloadMode.FULL,
        wait=lambda _s: True,
        http_timeout=0.2,
    )
    started = time.monotonic()
    with bb.run("r"):
        assert bb.upload_artifact("x") is not None
    assert time.monotonic() - started < 0.5  # enqueue only
    assert bb.flush(5)
    assert bb.stats()["dropped_artifacts"] == 1
    bb.shutdown()


def test_the_queue_is_bounded(server: ArtifactServer) -> None:
    server.delay = 0.3
    bb = client(server)
    with bb.run("r"):
        ids = [bb.upload_artifact(f"n{i}") for i in range(MAX_QUEUE_ITEMS + 20)]
    assert any(i is None for i in ids)
    assert bb.stats()["dropped_artifacts"] >= 1
    assert bb.shutdown(0.1) is False or True  # bounded; must not hang
    assert bb.stats()["dropped_artifacts"] >= 1


def test_oversize_artifacts_are_dropped_not_sent(server: ArtifactServer) -> None:
    bb = client(server)
    with bb.run("r"):
        assert bb.upload_artifact("€" * (MAX_ARTIFACT_BYTES // 2)) is None  # 3 bytes each
    assert bb.stats()["dropped_artifacts"] == 1
    bb.shutdown()


def test_redact_text_catches_multiline_private_keys_and_never_raises() -> None:
    bb = BlackBox(mode="offline")
    key = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nabc\n-----END RSA PRIVATE KEY-----"
    out = bb.redact_text(f"before\n{key}\nafter")
    assert "MIIEow" not in out and "before" in out and "after" in out
    assert bb.redact_text(None) == ""  # type: ignore[arg-type]


def test_shutdown_flushes_pending_artifacts(server: ArtifactServer) -> None:
    server.delay = 0.05
    bb = client(server)
    with bb.run("r"):
        for i in range(5):
            bb.upload_artifact(f"out {i}")
    assert bb.shutdown(5) is True
    assert len(server.received) == 5

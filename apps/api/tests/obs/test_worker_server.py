"""The worker's stdlib metrics server: opt-in, token-guarded, loopback, bounded, clean shutdown."""

import socket
import threading
import time
from collections.abc import Iterator

import httpx
import pytest
from pydantic import SecretStr

from abb_api.core.config import Settings
from abb_api.jobs.worker import _start_metrics_server
from abb_api.obs.catalogue import build_observability
from abb_api.obs.server import MAX_CONCURRENT_CONNECTIONS, MetricsServer, _Handler
from tests.obs.helpers import PLACEHOLDER_DB, TOKEN
from tests.obs.parser import parse, sample_value


@pytest.fixture
def server() -> Iterator[tuple[MetricsServer, str]]:
    obs = build_observability()
    obs.catalogue.worker_jobs_total.inc("summarize_run", "succeeded")
    srv = MetricsServer(obs.registry, SecretStr(TOKEN), port=0)
    srv.start()
    try:
        yield srv, f"http://127.0.0.1:{srv.port}"
    finally:
        srv.stop()


def test_serves_the_registry_with_the_token(server: tuple[MetricsServer, str]) -> None:
    _, url = server
    response = httpx.get(f"{url}/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert response.headers["cache-control"] == "no-store"
    families = parse(response.text)
    assert (
        sample_value(families, "worker_jobs_total", type="summarize_run", outcome="succeeded") == 1
    )
    assert httpx.get(f"{url}/metrics?x=1", headers={"Authorization": f"Bearer {TOKEN}"}).is_success


def test_wrong_or_missing_token_is_401_with_one_body(server: tuple[MetricsServer, str]) -> None:
    _, url = server
    responses = [
        httpx.get(f"{url}/metrics"),
        httpx.get(f"{url}/metrics", headers={"Authorization": "Bearer nope"}),
        httpx.get(f"{url}/metrics", headers={"Authorization": f"Basic {TOKEN}"}),
    ]
    assert {r.status_code for r in responses} == {401}
    assert len({r.content for r in responses}) == 1
    assert TOKEN not in responses[0].text


def test_no_other_routes_or_methods(server: tuple[MetricsServer, str]) -> None:
    _, url = server
    good = {"Authorization": f"Bearer {TOKEN}"}
    for path in ("/", "/healthz", "/metrics/", "/v1/runs", "/docs", "/%2e%2e/metrics"):
        assert httpx.get(f"{url}{path}", headers=good).status_code == 404, path
    for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"):
        assert httpx.request(method, f"{url}/metrics", headers=good).status_code == 405, method


def test_binds_loopback_by_default(server: tuple[MetricsServer, str]) -> None:
    srv, _ = server
    assert srv._server is not None
    assert srv._server.server_address[0] == "127.0.0.1"


def test_it_is_rate_limited() -> None:
    srv = MetricsServer(
        build_observability().registry, SecretStr(TOKEN), port=0, requests_per_minute=3
    )
    srv.start()
    try:
        codes = [
            httpx.get(
                f"http://127.0.0.1:{srv.port}/metrics", headers={"Authorization": f"Bearer {TOKEN}"}
            ).status_code
            for _ in range(5)
        ]
    finally:
        srv.stop()
    assert codes == [200, 200, 200, 429, 429]


def test_a_slow_or_oversized_request_is_bounded(server: tuple[MetricsServer, str]) -> None:
    srv, _ = server
    # An oversized request line is refused by the stdlib (414), not buffered.
    with socket.create_connection(("127.0.0.1", srv.port), timeout=5) as sock:
        sock.sendall(b"GET /" + b"a" * 100_000 + b" HTTP/1.0\r\n\r\n")
        assert sock.recv(64).startswith(b"HTTP/1.0 414")
    # Too many header lines are refused (431).
    with socket.create_connection(("127.0.0.1", srv.port), timeout=5) as sock:
        sock.sendall(b"GET /metrics HTTP/1.0\r\n" + b"X-A: b\r\n" * 200 + b"\r\n")
        assert sock.recv(64).startswith(b"HTTP/1.0 431")


def test_idle_connections_cannot_exhaust_the_server(
    server: tuple[MetricsServer, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_Handler, "timeout", 1.0)  # idle sockets give their slot back after 1 s
    srv, url = server
    idle = [
        socket.create_connection(("127.0.0.1", srv.port))
        for _ in range(MAX_CONCURRENT_CONNECTIONS + 4)
    ]
    try:
        time.sleep(0.2)
        # Beyond the slot cap the extra connections were dropped; the server stays responsive
        # once a slot frees (the held connections time out), and never blocks the process.
        deadline = time.monotonic() + 15
        status = None
        while time.monotonic() < deadline and status != 200:
            try:
                status = httpx.get(
                    f"{url}/metrics", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=2
                ).status_code
            except httpx.HTTPError:
                time.sleep(0.5)
        assert status == 200
    finally:
        for sock in idle:
            sock.close()


def test_shutdown_is_clean_and_releases_the_port() -> None:
    before = {t.name for t in threading.enumerate()}
    srv = MetricsServer(build_observability().registry, SecretStr(TOKEN), port=0)
    srv.start()
    port = srv.port
    assert httpx.get(f"http://127.0.0.1:{port}/metrics").status_code == 401
    srv.stop()
    srv.stop()  # idempotent
    assert {t.name for t in threading.enumerate()} <= before
    with pytest.raises(httpx.TransportError):
        httpx.get(f"http://127.0.0.1:{port}/metrics", timeout=1)
    with socket.socket() as probe:  # the port can be bound again
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", port))


def test_the_worker_starts_the_server_only_when_configured() -> None:
    off = Settings(database_url=PLACEHOLDER_DB, metrics_token=TOKEN)  # type: ignore[arg-type]
    assert _start_metrics_server(off) is None
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    on = Settings(
        database_url=PLACEHOLDER_DB,
        metrics_token=TOKEN,  # type: ignore[arg-type]
        worker_metrics_port=port,
    )
    started = _start_metrics_server(on)
    assert started is not None
    try:
        assert httpx.get(
            f"http://127.0.0.1:{port}/metrics", headers={"Authorization": f"Bearer {TOKEN}"}
        ).is_success
    finally:
        started.stop()


def test_a_byte_at_a_time_client_cannot_hold_a_connection_past_the_total_deadline() -> None:
    # The per-read timeout resets on every byte; only a wall-clock deadline per connection stops
    # a client that sends one byte every few seconds from holding one of the eight slots forever.
    srv = MetricsServer(
        build_observability().registry, SecretStr(TOKEN), port=0, connection_deadline_seconds=0.4
    )
    srv.start()
    try:
        with socket.create_connection(("127.0.0.1", srv.port), timeout=5) as sock:
            closed = False
            for _ in range(100):  # a byte every 50 ms, far inside the per-read timeout
                try:
                    sock.sendall(b"G")
                except OSError:
                    closed = True
                    break
                time.sleep(0.05)
                sock.setblocking(False)
                try:
                    if sock.recv(1) == b"":
                        closed = True
                        break
                except BlockingIOError:
                    pass
                except OSError:
                    closed = True
                    break
                finally:
                    sock.setblocking(True)
            assert closed, "the server kept a trickling connection open"
        # The slot came back: a normal scrape works.
        assert httpx.get(
            f"http://127.0.0.1:{srv.port}/metrics", headers={"Authorization": f"Bearer {TOKEN}"}
        ).is_success
    finally:
        srv.stop()


def test_the_rate_limiter_accounts_exactly_under_concurrency() -> None:
    import sys

    srv = MetricsServer(
        build_observability().registry, SecretStr(TOKEN), port=0, requests_per_minute=500
    )
    srv.limiter._monotonic = lambda: 1000.0  # no refill: exact
    accepted = []
    barrier = threading.Barrier(64)

    def hammer() -> None:
        barrier.wait()
        accepted.append(sum(1 for _ in range(20) if not srv.rate_limited()))

    switch = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        threads = [threading.Thread(target=hammer) for _ in range(64)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        sys.setswitchinterval(switch)
    assert sum(accepted) == 500  # the burst, not a token more

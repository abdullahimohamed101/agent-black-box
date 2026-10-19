"""The policy transport against a server that answers in several TCP segments.

Found by `scripts/policy-e2e.sh` (Phase 14 step 5): against the real API (uvicorn writes the response
headers and the body separately) about one call in twenty failed under concurrency with
`OSError: [Errno 9] Bad file descriptor`, which the SDK reports as `policy_service_unreachable`. The
approval had already been spent by the server, so the agent was told "denied" for an action a person
had approved. `http.client` closes the connection as soon as it has the headers of a `Connection:
close` response and relies on the file object it handed out to keep the socket alive; the transport's
own file object did not, so a body that was not already in the read buffer was read from a closed
socket. The in-process fake server of `policy_server.py` answers in one segment and never showed it.
"""

import json
import socket
import threading
import time
from collections.abc import Iterator

import pytest

from agent_blackbox.policy_transport import PolicyTransport

BODY = json.dumps(
    {"decision": "allow", "policy_id": None, "policy_version": 3, "reason": "ok"}
).encode()


class Segmented:
    """Answers each connection with the headers, a pause, then the body, as separate segments."""

    def __init__(self, pause: float, parts: int = 2) -> None:
        self.pause, self.parts = pause, parts
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(64)
        self.port = self.listener.getsockname()[1]
        self.stopped = False
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self) -> None:
        while not self.stopped:
            try:
                conn, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self.answer, args=(conn,), daemon=True).start()

    def answer(self, conn: socket.socket) -> None:
        with conn:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk
            head, _, rest = data.partition(b"\r\n\r\n")
            length = next(
                (
                    int(h.split(b":")[1])
                    for h in head.split(b"\r\n")
                    if h.lower().startswith(b"content-length")
                ),
                0,
            )
            while len(rest) < length:
                rest += conn.recv(4096)
            conn.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: %d\r\n\r\n"
                % len(BODY)
            )
            step = max(1, len(BODY) // self.parts)
            for start in range(0, len(BODY), step):
                time.sleep(self.pause)
                conn.sendall(BODY[start : start + step])

    def stop(self) -> None:
        self.stopped = True
        self.listener.close()


@pytest.fixture
def server() -> Iterator[Segmented]:
    fake = Segmented(pause=0.02, parts=3)
    yield fake
    fake.stop()


def transport(server: Segmented) -> PolicyTransport:
    return PolicyTransport(f"http://127.0.0.1:{server.port}", "abb_live_test.secret")


def test_a_body_that_arrives_after_the_headers_is_read(server: Segmented) -> None:
    reply = transport(server).request("POST", "/v1/policy/decide", {"a": 1}, 2.0)
    assert reply.status == 200 and reply.body is not None and reply.body["policy_version"] == 3


def test_concurrent_exchanges_with_a_slow_body_all_succeed(server: Segmented) -> None:
    failures: list[str] = []
    done = threading.Barrier(9)

    def call() -> None:
        try:
            for _ in range(10):
                reply = transport(server).request("GET", "/v1/policies/fail-modes", None, 3.0)
                assert reply.body is not None and reply.body["decision"] == "allow"
        except Exception as error:
            failures.append(f"{type(error).__name__}: {error}")
        finally:
            done.wait(30)

    threads = [threading.Thread(target=call) for _ in range(8)]
    for thread in threads:
        thread.start()
    done.wait(30)
    assert failures == []

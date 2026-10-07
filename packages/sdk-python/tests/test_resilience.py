"""INV-4: backend trouble never harms the agent; memory and time stay bounded."""

import socket
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

from blackbox import BlackBox
from tests.helpers import start_stub, stop_stub


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def run_agent(bb: BlackBox, n: int = 2000) -> float:
    """A busy agent loop; returns how long the application thread was busy."""
    t0 = time.perf_counter()
    with bb.run("agent") as run:
        for i in range(n):
            with run.span(f"step{i % 5}", kind="tool"):
                pass
    return time.perf_counter() - t0


def test_backend_down_agent_continues_queue_is_bounded_nothing_raises() -> None:
    bb = BlackBox(
        api_key="k",
        endpoint=f"http://127.0.0.1:{free_port()}",
        max_queue=300,
        max_attempts=2,
        backoff_base=0.01,
        flush_interval=0.02,
    )
    elapsed = run_agent(bb)  # connection refused on every send
    assert elapsed < 5
    assert bb.stats()["queue_size"] <= 600  # hard cap, never unbounded
    t0 = time.monotonic()
    bb.shutdown(2)
    assert time.monotonic() - t0 < 4
    assert bb.stats()["events_sent"] == 0 and bb.stats()["dropped_export_failed"] > 0


def test_a_hanging_backend_never_blocks_the_application_and_shutdown_returns_in_time() -> None:
    stub = start_stub(delay=30)  # accepts, then never answers within the timeout
    try:
        bb = BlackBox(
            api_key="k",
            endpoint=stub.url,
            http_timeout=0.2,
            max_attempts=3,
            backoff_base=0.01,
            flush_interval=0.02,
            max_queue=500,
        )
        elapsed = run_agent(bb, 1000)
        assert elapsed < 3  # the application thread never waited on the socket
        t0 = time.monotonic()
        bb.shutdown(1.0)
        assert time.monotonic() - t0 < 3
        assert bb.stats()["queue_size"] == 0
    finally:
        stop_stub_nowait(stub)


def stop_stub_nowait(stub: object) -> None:
    # The hanging handler threads are daemons; closing the listener is enough.
    threading.Thread(target=stop_stub, args=(stub,), daemon=True).start()


def test_garbage_and_oddly_shaped_responses_do_not_break_the_exporter() -> None:
    from tests.helpers import StubServer  # noqa: F401

    script = [
        (200, {}, b"not json"),
        (202, {"Content-Type": "text/html"}, b"<h1>proxy</h1>"),
        (500, {}, b"\x00\xff\xfe"),
        (302, {"Location": "http://elsewhere"}, b""),
    ]
    stub = start_stub(script)
    try:
        bb = BlackBox(
            api_key="k", endpoint=stub.url, flush_interval=0.02, backoff_base=0.01, batch_size=5
        )
        run_agent(bb, 50)
        bb.shutdown(3)
        assert bb.stats()["internal_errors"] == 0
    finally:
        stop_stub(stub)


def test_unresolvable_host_and_bad_endpoints_are_survivable() -> None:
    for endpoint in ("http://nonexistent.invalid:9", "not a url", "ftp://x", "", "http://[::1"):
        bb = BlackBox(
            api_key="k", endpoint=endpoint, max_attempts=1, backoff_base=0.0, flush_interval=0.02
        )
        with bb.run("r") as run:
            run.event("custom.x")
        bb.shutdown(2)
        assert bb.stats()["internal_errors"] == 0


def test_events_are_flushed_at_interpreter_exit(tmp_path: Path) -> None:
    stub = start_stub()
    script = textwrap.dedent(
        f"""
        from blackbox import BlackBox
        bb = BlackBox(api_key="k", endpoint="{stub.url}", flush_interval=30)
        with bb.run("exit-test") as run:
            run.event("custom.last")
        # no flush, no shutdown: atexit must deliver
        """
    )
    try:
        subprocess.run([sys.executable, "-c", script], check=True, timeout=30)  # noqa: S603
        types = [e["event_type"] for e in stub.events()]
        assert types == ["run.started", "custom.last", "run.completed"]
    finally:
        stop_stub(stub)


def test_a_crash_inside_the_agent_still_records_the_failure(tmp_path: Path) -> None:
    stub = start_stub()
    script = textwrap.dedent(
        f"""
        from blackbox import BlackBox
        bb = BlackBox(api_key="k", endpoint="{stub.url}", flush_interval=30)
        with bb.run("crash") as run:
            raise RuntimeError("agent bug")
        """
    )
    try:
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=30)  # noqa: S603
        assert proc.returncode == 1 and b"agent bug" in proc.stderr  # the host's error is untouched
        assert [e["event_type"] for e in stub.events()][-1] == "run.failed"
    finally:
        stop_stub(stub)

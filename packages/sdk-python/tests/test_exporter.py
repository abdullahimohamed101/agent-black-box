"""The exporter against a real HTTP server: batching, gzip, retry policy (ADR-013), failures."""

import json
import threading
import time
from typing import Any

import pytest

from blackbox import BlackBox
from blackbox.exporter import parse_retry_after
from tests.helpers import StubServer, offline, start_stub, stop_stub

KEY = "abb_live_test.secret"


class Waits:
    """Replaces real backoff sleeping and records the requested delays."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> bool:
        self.delays.append(seconds)
        return False


def client(stub: StubServer, **options: Any) -> BlackBox:
    options.setdefault("flush_interval", 0.02)
    return BlackBox(api_key=KEY, project="demo", endpoint=stub.url, **options)


def emit(bb: BlackBox, n: int) -> None:
    with bb.run("r") as run:
        for i in range(n):
            run.event("custom.tick", {"i": i})


def test_events_arrive_with_auth_batch_id_and_the_batch_endpoint(stub: StubServer) -> None:
    bb = client(stub)
    emit(bb, 5)
    assert bb.shutdown(3)
    assert len(stub.events()) == 7  # run.started + 5 + run.completed
    req = stub.received[0]
    assert req.path == "/v1/events/batch" and req.headers["authorization"] == f"Bearer {KEY}"
    assert req.body["batch_id"].startswith("bat_")
    assert bb.stats()["events_sent"] == 7 and bb.stats()["batches_sent"] >= 1


def test_batches_are_capped_at_batch_size_and_every_event_is_delivered_once(
    stub: StubServer,
) -> None:
    bb = client(stub, batch_size=10)
    emit(bb, 95)
    assert bb.shutdown(5)
    assert all(len(r.body["events"]) <= 10 for r in stub.received)
    ids = [e["event_id"] for e in stub.events()]
    assert len(ids) == len(set(ids)) == 97


def test_large_bodies_are_gzipped_and_small_ones_are_not(stub: StubServer) -> None:
    bb = client(stub, gzip_min_bytes=2000)
    emit(bb, 40)
    bb.shutdown(3)
    assert any(r.headers.get("content-encoding") == "gzip" for r in stub.received)
    stub2 = start_stub()
    try:
        bb2 = client(stub2, gzip_min_bytes=10**9)
        emit(bb2, 3)
        bb2.shutdown(3)
        assert all("content-encoding" not in r.headers for r in stub2.received)
    finally:
        stop_stub(stub2)


def test_5xx_is_retried_with_the_same_batch_id_and_succeeds() -> None:
    stub = start_stub([(500, {}, b""), (502, {}, b"")])
    waits = Waits()
    try:
        bb = client(stub, wait=waits, backoff_base=0.5)
        emit(bb, 2)
        assert bb.shutdown(5)
        first_three = stub.received[:3]
        assert len({r.body["batch_id"] for r in first_three}) == 1  # same id on every retry
        assert len(first_three[0].body["events"]) == 4
        assert bb.stats()["retries"] == 2 and bb.stats()["events_sent"] == 4
        assert len(waits.delays) == 2 and all(
            0 <= d <= 0.5 * 2**i for i, d in enumerate(waits.delays)
        )
    finally:
        stop_stub(stub)


@pytest.mark.parametrize("status", [429, 503])
def test_retry_after_is_honoured_exactly(status: int) -> None:
    stub = start_stub([(status, {"Retry-After": "7"}, b"")])
    waits = Waits()
    try:
        bb = client(stub, wait=waits)
        emit(bb, 1)
        assert bb.shutdown(5)
        assert waits.delays == [7.0]
        assert bb.stats()["events_sent"] == 3
    finally:
        stop_stub(stub)


def test_retry_after_is_capped_and_bad_values_fall_back_to_backoff() -> None:
    assert parse_retry_after("7", 60) == 7
    assert parse_retry_after("100000", 60) == 60
    assert parse_retry_after("-5", 60) == 0
    assert parse_retry_after("soon", 60) is None
    assert parse_retry_after("nan", 60) is None
    assert parse_retry_after(None, 60) is None
    assert parse_retry_after("Wed, 21 Oct 2099 07:28:00 GMT", 60) == 60
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT", 60) == 0
    stub = start_stub([(429, {"Retry-After": "garbage"}, b"")])
    waits = Waits()
    try:
        bb = client(stub, wait=waits, backoff_base=0.25)
        emit(bb, 1)
        bb.shutdown(5)
        assert len(waits.delays) == 1 and 0 <= waits.delays[0] <= 0.25
    finally:
        stop_stub(stub)


def test_retries_are_bounded_and_the_batch_is_then_dropped_and_counted() -> None:
    stub = start_stub([(503, {}, b"")] * 50)
    try:
        bb = client(stub, wait=Waits(), max_attempts=4)
        emit(bb, 1)
        bb.shutdown(5)
        assert len(stub.received) == 4
        assert bb.stats()["dropped_export_failed"] == 3 and bb.stats()["events_sent"] == 0
    finally:
        stop_stub(stub)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 415, 422])
def test_non_retryable_statuses_drop_the_batch_without_retrying(status: int) -> None:
    stub = start_stub([(status, {}, b'{"error":{}}')])
    try:
        bb = client(stub, wait=Waits())
        emit(bb, 1)
        bb.shutdown(3)
        assert len(stub.received) == 1
        assert bb.stats()["dropped_rejected"] == 3 and bb.stats()["retries"] == 0
    finally:
        stop_stub(stub)


def test_413_splits_the_batch_in_halves() -> None:
    stub = start_stub([(413, {}, b"")])
    try:
        bb = client(stub, wait=Waits())
        emit(bb, 6)  # 8 events in one batch
        assert bb.shutdown(5)
        sizes = [len(r.body["events"]) for r in stub.received]
        assert sizes[0] == 8 and sorted(sizes[1:]) == [4, 4]
        assert bb.stats()["events_sent"] == 8
    finally:
        stop_stub(stub)


def test_partial_rejections_in_a_202_are_counted_and_not_retried() -> None:
    def respond(body: dict[str, Any]) -> bytes:
        n = len(body["events"])
        return json.dumps(
            {"accepted": n - 2, "duplicates": 1, "rejected": 2, "errors": []}
        ).encode()

    stub = start_stub(respond=respond)
    try:
        bb = client(stub)
        emit(bb, 4)
        bb.shutdown(3)
        assert len(stub.received) == 1
        stats = bb.stats()
        assert stats["rejected_by_server"] == 2 and stats["events_duplicate"] == 1
        assert stats["events_sent"] == 4
    finally:
        stop_stub(stub)


def test_a_garbage_202_body_is_still_success(stub: StubServer) -> None:
    stub.script.append((202, {}, b"<html>proxy</html>"))
    bb = client(stub)
    emit(bb, 1)
    bb.shutdown(3)
    assert bb.stats()["events_sent"] == 3 and bb.stats()["retries"] == 0


def test_oversized_batches_are_chunked_under_the_byte_limit(stub: StubServer) -> None:
    bb = client(stub, max_batch_bytes=4096, gzip_min_bytes=10**9)
    emit(bb, 60)
    assert bb.shutdown(5)
    assert len(stub.events()) == 62
    assert all(r.raw_size <= 4096 + 512 for r in stub.received)


def test_events_over_the_size_limit_are_dropped_and_counted_not_sent(stub: StubServer) -> None:
    bb = client(stub)
    huge = {f"k{i}": "x" * 4096 for i in range(64)}  # attributes alone exceed 256 KB
    with bb.run("r") as run:
        run.event("custom.huge", huge)
        run.event("custom.ok")
    bb.shutdown(3)
    sent = [e["event_type"] for e in stub.events()]
    assert "custom.ok" in sent and "custom.huge" not in sent
    assert bb.stats()["dropped_oversize"] == 1


def test_flush_waits_for_delivery_and_shutdown_is_idempotent(stub: StubServer) -> None:
    bb = client(stub, flush_interval=5.0)  # only an explicit flush moves events
    emit(bb, 3)
    assert bb.flush(3)
    assert len(stub.events()) == 5
    assert bb.shutdown(3) and bb.shutdown(3)
    emit(bb, 2)  # after shutdown: ignored, never raises
    assert len(stub.events()) == 5


def test_a_batch_full_of_events_is_sent_without_waiting_for_the_interval(stub: StubServer) -> None:
    bb = client(stub, flush_interval=30.0, batch_size=5)
    emit(bb, 8)  # crossing the batch size wakes the exporter immediately
    deadline = time.monotonic() + 3
    while not stub.received and time.monotonic() < deadline:
        time.sleep(0.01)
    assert stub.received
    bb.shutdown(3)


def test_threads_producing_concurrently_deliver_everything_exactly_once(stub: StubServer) -> None:
    bb = client(stub, batch_size=50)

    def work() -> None:
        with bb.run("t") as run:
            for i in range(100):
                run.event("custom.tick", {"i": i})

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert bb.shutdown(10)
    ids = [e["event_id"] for e in stub.events()]
    assert len(ids) == len(set(ids)) == 8 * 102
    by_run: dict[str, list[int]] = {}
    for e in stub.events():
        by_run.setdefault(e["run_id"], []).append(e["sequence"])
    assert all(sorted(v) == list(range(1, 103)) for v in by_run.values())


def test_a_connection_the_server_closed_while_idle_is_replaced_without_a_retry(
    stub: StubServer,
) -> None:
    stub.drop_idle = True
    bb = client(stub, flush_interval=5.0)
    emit(bb, 1)
    assert bb.flush(3)
    emit(bb, 1)  # the kept-alive connection is now dead
    assert bb.flush(3) and bb.shutdown(3)
    assert len(stub.events()) == 6
    assert bb.stats()["retries"] == 0 and bb.stats()["dropped_export_failed"] == 0


def test_the_config_repr_never_shows_the_api_key() -> None:
    bb = offline()
    assert "abb_live_test" not in repr(bb.config)


def test_a_two_event_batch_that_is_too_big_is_split_into_single_events() -> None:
    stub = start_stub([(413, {}, b"")])
    try:
        bb = client(stub, wait=Waits())
        with bb.run("r"):
            pass  # exactly two events
        assert bb.shutdown(3)
        assert [len(r.body["events"]) for r in stub.received] == [2, 1, 1]
        assert bb.stats()["events_sent"] == 2 and bb.stats()["dropped_rejected"] == 0
    finally:
        stop_stub(stub)


def test_a_single_event_that_is_too_big_is_dropped_not_looped() -> None:
    stub = start_stub([(413, {}, b"")] * 3)
    try:
        bb = client(stub, wait=Waits(), batch_size=1)
        with bb.run("r"):
            pass
        bb.shutdown(3)
        assert bb.stats()["dropped_rejected"] >= 1 and len(stub.received) <= 4
    finally:
        stop_stub(stub)


def test_each_batch_gets_its_own_batch_id(stub: StubServer) -> None:
    bb = client(stub, batch_size=5)
    emit(bb, 30)
    bb.shutdown(3)
    ids = [r.body["batch_id"] for r in stub.received]
    assert len(ids) > 3 and len(set(ids)) == len(ids)


def test_backoff_grows_exponentially_with_full_jitter_and_is_capped() -> None:
    from blackbox.config import Config
    from blackbox.exporter import HttpSink
    from blackbox.stats import Stats

    config = Config.build(api_key="k", backoff_base=0.5, backoff_max=3.0)
    sink = HttpSink(config, Stats())
    for attempt, ceiling in ((0, 0.5), (1, 1.0), (2, 2.0), (3, 3.0), (10, 3.0)):
        samples = [sink._backoff(attempt) for _ in range(400)]
        assert (
            max(samples) <= ceiling
            and max(samples) > ceiling * 0.8
            and min(samples) < ceiling * 0.2
        )


def test_flush_on_an_idle_or_offline_client_returns_immediately() -> None:
    bb = offline()
    t0 = time.monotonic()
    assert bb.flush(5) is True  # nothing queued
    with bb.run("r"):
        pass
    assert bb.flush(5) is False  # offline: queued events are never delivered
    assert time.monotonic() - t0 < 1


def test_flush_cannot_return_while_a_batch_is_between_the_buffer_and_the_sink() -> None:
    import threading as th

    from blackbox.buffer import EventBuffer
    from blackbox.config import Config
    from blackbox.exporter import Exporter
    from blackbox.stats import Stats

    stats = Stats()
    taken, release = th.Event(), th.Event()

    class SlowTake(EventBuffer):
        def take(self, limit: int) -> list[dict[str, Any]]:
            out = super().take(limit)
            if out:
                taken.set()
                release.wait(2)  # the window: events left the buffer, sink not called yet
            return out

    delivered: list[int] = []

    class Sink:
        def deliver(self, events: list[dict[str, Any]], abort: th.Event) -> None:
            delivered.append(len(events))

        def close(self) -> None:
            return None

    config = Config.build(api_key="k", flush_interval=0.01)
    buf = SlowTake(100, stats)
    exporter = Exporter(config, buf, stats, Sink())
    buf.put({"event_id": "a", "event_type": "run.started"}, 0)
    exporter.start()
    assert taken.wait(2)
    result: list[bool] = []
    flusher = th.Thread(target=lambda: result.append(exporter.flush(0.3)))
    flusher.start()
    flusher.join()
    assert result == [False] and delivered == []  # flush did not claim success early
    release.set()
    assert exporter.flush(2) and delivered == [1]
    exporter.shutdown(1)

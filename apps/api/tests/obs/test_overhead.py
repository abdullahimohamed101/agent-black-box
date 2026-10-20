"""Overhead is bounded by construction, asserted on counts, never on wall-clock time (19a lesson).

The timing numbers live in docs/benchmarks/phase-19c-metrics.md and come from `bench_metrics.py`.
"""

import threading
from types import TracebackType

from abb_api.obs.catalogue import UNMATCHED, Catalogue, Observability, build_catalogue
from abb_api.obs.http import MetricsMiddleware
from abb_api.obs.metrics import Registry, capped, closed
from tests.obs.helpers import client_for, make_app


class CountingLock:
    """A real lock that counts how many times it was taken."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.count = 0

    def __enter__(self) -> object:
        self._lock.acquire()
        self.count += 1
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
        /,
    ) -> None:
        self._lock.release()


def counting_registry() -> tuple[Registry, list[CountingLock]]:
    locks: list[CountingLock] = []

    def factory() -> CountingLock:
        lock = CountingLock()
        locks.append(lock)
        return lock

    return Registry(lock_factory=factory), locks


def test_each_hot_path_operation_takes_exactly_one_lock_once() -> None:
    registry, locks = counting_registry()
    counter = registry.counter("a_total", "A.", [closed("k", "x")])
    gauge = registry.gauge("b", "B.")
    hist = registry.histogram("c_seconds", "C.", [capped("r", 10)])
    for _ in range(100):
        counter.inc("x")
        gauge.inc()
        gauge.dec()
        hist.observe(0.01, "route")
    assert [lock.count for lock in locks] == [100, 200, 100]  # one acquisition per operation


def test_repeating_the_same_labels_creates_no_new_series() -> None:
    registry = Registry()
    hist = registry.histogram("c_seconds", "C.", [capped("r", 10)])
    hist.observe(0.01, "route")
    before = hist.series_count()
    for _ in range(10_000):
        hist.observe(0.01, "route")
    assert hist.series_count() == before == 1


def test_a_scrape_takes_each_metric_lock_once_and_holds_no_global_lock() -> None:
    registry, locks = counting_registry()
    registry.counter("a_total", "A.", [closed("k", "x")]).inc("x")
    registry.gauge("b", "B.").set(1)
    registry.histogram("c_seconds", "C.").observe(0.1)
    registry.render()
    assert [lock.count for lock in locks] == [2, 2, 2]  # the write above plus one collect each


async def test_a_request_costs_a_fixed_small_number_of_lock_acquisitions() -> None:
    registry, locks = counting_registry()
    catalogue: Catalogue = build_catalogue(registry)
    baseline = sum(lock.count for lock in locks)  # build_info and start time were set
    app = make_app(observability=Observability(registry, catalogue))
    async with client_for(app) as client:
        before = sum(lock.count for lock in locks)
        for _ in range(50):
            await client.get("/healthz")
        after = sum(lock.count for lock in locks)
    assert baseline > 0
    # in-flight +1, in-flight -1, one counter, one histogram: four per request, no more.
    assert (after - before) == 50 * 4
    assert catalogue.http_requests_total.value("GET", "/healthz", "200") == 50


async def test_the_middleware_adds_one_series_per_route_not_per_request() -> None:
    registry, _ = counting_registry()
    catalogue = build_catalogue(registry)
    seen = 0

    async def inner(scope: dict, receive: object, send: object) -> None:  # type: ignore[type-arg]
        nonlocal seen
        seen += 1
        await send({"type": "http.response.start", "status": 204, "headers": []})  # type: ignore[operator]
        await send({"type": "http.response.body", "body": b""})  # type: ignore[operator]

    app = MetricsMiddleware(inner, catalogue=catalogue)  # type: ignore[arg-type]

    async def sink(message: object) -> None:
        return None

    async def receive() -> dict[str, object]:
        return {"type": "http.request"}

    for _ in range(1000):
        await app({"type": "http", "method": "GET", "path": "/x", "headers": []}, receive, sink)
    assert seen == 1000
    assert catalogue.http_requests_total.series_count() == 1
    assert catalogue.http_requests_total.value("GET", UNMATCHED, "204") == 1000

"""Bounded buffer and priority classes (spec §67.3-67.4)."""

import threading

from blackbox.buffer import EventBuffer
from blackbox.stats import Stats


def ev(n: int) -> dict[str, int]:
    return {"n": n}


def test_the_buffer_never_exceeds_its_bound_for_low_priorities() -> None:
    stats = Stats()
    buf = EventBuffer(10, stats)
    results = [buf.put(ev(i), 2) for i in range(25)]
    assert len(buf) == 10 and results.count(False) == 15 and stats["dropped_p2"] == 15


def test_a_full_buffer_sacrifices_p2_then_p1_for_more_important_events() -> None:
    stats = Stats()
    buf = EventBuffer(4, stats)
    for i, p in enumerate((2, 2, 1, 1)):
        buf.put(ev(i), p)
    assert buf.put(ev(10), 1) is True  # evicts the oldest P2 (n=0)
    assert buf.put(ev(11), 0) is True  # evicts the next P2 (n=1)
    assert buf.put(ev(12), 0) is True  # no P2 left: evicts the oldest P1 (n=2)
    assert stats["dropped_p2"] == 2 and stats["dropped_p1"] == 1
    assert buf.put(ev(13), 2) is False  # nothing lower to evict: the arrival itself is dropped
    assert [e["n"] for e in buf.take(10)] == [11, 12, 3, 10]  # P0 first, then P1 oldest-first


def test_p1_does_not_evict_p1_or_p0() -> None:
    buf = EventBuffer(3, Stats())
    for i in range(3):
        buf.put(ev(i), 0 if i == 0 else 1)
    assert buf.put(ev(9), 1) is False
    assert len(buf) == 3


def test_p0_may_overflow_up_to_a_hard_cap_then_is_dropped_and_counted() -> None:
    stats = Stats()
    buf = EventBuffer(5, stats, p0_overflow=3)
    for i in range(5):
        buf.put(ev(i), 0)
    admitted = [buf.put(ev(100 + i), 0) for i in range(6)]
    assert admitted == [True, True, True, False, False, False]
    assert len(buf) == 8 == buf.hard_cap and stats["dropped_p0"] == 3


def test_take_respects_the_limit_and_priority_order() -> None:
    buf = EventBuffer(100, Stats())
    for i, p in enumerate((2, 1, 0, 2, 1, 0)):
        buf.put(ev(i), p)
    assert [e["n"] for e in buf.take(4)] == [2, 5, 1, 4]
    assert len(buf) == 2 and [e["n"] for e in buf.take(10)] == [0, 3]
    assert buf.take(10) == []


def test_clear_empties_and_counts() -> None:
    buf = EventBuffer(10, Stats())
    for i in range(7):
        buf.put(ev(i), 1)
    assert buf.clear() == 7 and len(buf) == 0


def test_concurrent_producers_and_a_consumer_never_lose_accounting() -> None:
    stats = Stats()
    buf = EventBuffer(500, stats)
    taken: list[int] = []
    stop = threading.Event()

    def produce(base: int) -> None:
        for i in range(2000):
            buf.put(ev(base + i), i % 3)

    def consume() -> None:
        while not stop.is_set():
            taken.extend(e["n"] for e in buf.take(50))

    consumer = threading.Thread(target=consume)
    producers = [threading.Thread(target=produce, args=(k * 10_000,)) for k in range(6)]
    consumer.start()
    for t in producers:
        t.start()
    for t in producers:
        t.join()
    stop.set()
    consumer.join()
    taken.extend(e["n"] for e in buf.take(10_000))
    dropped = stats["dropped_p0"] + stats["dropped_p1"] + stats["dropped_p2"]
    assert len(taken) + dropped == 12_000
    assert len(set(taken)) == len(taken)
    assert len(buf) == 0

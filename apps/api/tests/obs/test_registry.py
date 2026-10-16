"""The metrics registry: types, label discipline, bounds, concurrency, exposition format."""

import asyncio
import math
import threading

import pytest

from abb_api.obs.metrics import (
    OTHER,
    Gauge,
    Histogram,
    Registry,
    Timer,
    capped,
    closed,
    escape_label_value,
)
from tests.obs.parser import parse, sample_value


def test_counter_counts_per_label_set_and_refuses_to_go_down() -> None:
    r = Registry()
    c = r.counter("things_total", "Things.", [closed("kind", "a", "b")])
    c.inc("a")
    c.inc("a", amount=2)
    c.inc("b")
    assert (c.value("a"), c.value("b")) == (3, 1)
    with pytest.raises(ValueError):
        c.inc("a", amount=-1)
    with pytest.raises(ValueError):
        c.inc()  # wrong arity


def test_unlabelled_metrics_exist_at_zero() -> None:
    r = Registry()
    r.counter("a_total", "A.")
    r.gauge("b", "B.")
    r.histogram("c_seconds", "C.", buckets=(1.0,))
    fam = parse(r.render())
    assert sample_value(fam, "a_total") == 0
    assert sample_value(fam, "b") == 0
    assert sample_value(fam, "c_seconds", "c_seconds_count") == 0


def test_gauge_set_inc_dec_and_function() -> None:
    r = Registry()
    g = r.gauge("level", "Level.", [capped("q", 3)])
    g.set(5, "x")
    g.inc("x")
    g.dec("x", amount=2)
    assert g.value("x") == 4
    u = r.gauge("uptime", "Uptime.")
    u.set_function(lambda: 7.0)
    assert sample_value(parse(r.render()), "uptime") == 7.0
    with pytest.raises(ValueError):
        g.set_function(lambda: 1.0)
    u.set_function(lambda: 1 / 0)  # a broken callback never breaks a scrape
    broken = sample_value(parse(r.render()), "uptime")
    assert broken is not None and math.isnan(broken)


def test_closed_label_maps_unknown_values_to_other() -> None:
    r = Registry()
    c = r.counter("x_total", "X.", [closed("kind", "a")])
    for junk in ("b", "c", "A", "", "a\n", "x" * 1000):
        c.inc(junk)
    assert c.value(OTHER) == 6
    assert c.series_count() == 1


def test_capped_label_admits_cap_distinct_values_then_other() -> None:
    r = Registry()
    c = r.counter("x_total", "X.", [capped("route", 3)])
    for i in range(50):
        c.inc(f"/r{i % 10}")
    assert c.series_count() == 4  # three admitted values and "other"
    assert c.value("/r0") + c.value("/r1") + c.value("/r2") + c.value(OTHER) == 50
    c.inc(OTHER)  # a caller passing "other" itself is not admitted as a new value
    assert c.series_count() == 4


def test_long_label_values_are_truncated() -> None:
    r = Registry()
    c = r.counter("x_total", "X.", [capped("route", 3)])
    c.inc("y" * 5000)
    line = next(ln for ln in r.render().splitlines() if ln.startswith("x_total{"))
    assert len(line) < 200


def test_series_ceiling_collapses_to_the_overflow_series() -> None:
    r = Registry()
    c = r.counter("x_total", "X.", [capped("a", 100), capped("b", 100)], max_series=10)
    for i in range(30):
        for j in range(30):
            c.inc(str(i), str(j))
    assert c.series_count() <= 11  # ten series plus the overflow one
    assert sum(v for _, _, v in c.collect()) == 900  # nothing is lost, only merged


def test_histogram_buckets_sum_and_count() -> None:
    r = Registry()
    h = r.histogram("lat_seconds", "Latency.", buckets=(0.1, 1.0))
    for v in (0.05, 0.1, 0.5, 2.0):
        h.observe(v)
    counts, total = h.snapshot()
    assert counts == [2, 1, 1]  # <=0.1 (0.05 and exactly 0.1), <=1, +Inf
    assert total == pytest.approx(2.65)
    fam = parse(r.render())

    def s(sample: str, **lbl: str) -> float | None:
        return sample_value(fam, "lat_seconds", sample, **lbl)

    assert s("lat_seconds_bucket", le="0.1") == 2
    assert s("lat_seconds_bucket", le="1.0") == 3
    assert s("lat_seconds_bucket", le="+Inf") == 4
    assert s("lat_seconds_count") == 4
    assert s("lat_seconds_sum") == pytest.approx(2.65)


@pytest.mark.parametrize("bad", [(), (1.0, 1.0), (2.0, 1.0), (1.0, math.inf)])
def test_histogram_rejects_bad_buckets(bad: tuple[float, ...]) -> None:
    with pytest.raises(ValueError):
        Registry().histogram("h_seconds", "H.", buckets=bad)


def test_timer_observes_elapsed_time() -> None:
    r = Registry()
    h = r.histogram("t_seconds", "T.", buckets=(10.0,))
    with Timer(h):
        pass
    assert h.snapshot()[0][0] == 1


def test_declaration_rules() -> None:
    r = Registry()
    r.counter("a_total", "A.")
    with pytest.raises(ValueError):
        r.counter("a_total", "again")
    for bad in ("Bad", "1x", "a-b", ""):
        with pytest.raises(ValueError):
            r.gauge(bad, "bad")
    with pytest.raises(ValueError):
        r.gauge("g", "G.", [closed("le", "x")])
    with pytest.raises(ValueError):
        r.gauge("g", "G.", [closed("a", "x"), closed("a", "y")])
    with pytest.raises(ValueError):
        closed("a", OTHER)
    r.seal()
    with pytest.raises(RuntimeError):
        r.counter("late_total", "Late.")


def test_exposition_format_is_exact_and_deterministic() -> None:
    r = Registry()
    c = r.counter("b_total", 'Help with "quotes" \\ and\nnewline.', [closed("k", "x", "y")])
    c.inc("y")
    c.inc("x", amount=2.5)
    r.gauge("a_gauge", "A gauge.").set(3)
    h = r.histogram("c_seconds", "C.", [closed("m", "GET")], buckets=(0.5,))
    h.observe(0.25, "GET")
    expected = (
        "# HELP a_gauge A gauge.\n"
        "# TYPE a_gauge gauge\n"
        "a_gauge 3\n"
        '# HELP b_total Help with "quotes" \\\\ and\\nnewline.\n'
        "# TYPE b_total counter\n"
        'b_total{k="x"} 2.5\n'
        'b_total{k="y"} 1\n'
        "# HELP c_seconds C.\n"
        "# TYPE c_seconds histogram\n"
        'c_seconds_bucket{m="GET",le="0.5"} 1\n'
        'c_seconds_bucket{m="GET",le="+Inf"} 1\n'
        'c_seconds_sum{m="GET"} 0.25\n'
        'c_seconds_count{m="GET"} 1\n'
    )
    assert r.render() == expected
    assert r.render() == expected


def test_label_value_escaping_round_trips() -> None:
    assert escape_label_value('a"b\\c\nd') == 'a\\"b\\\\c\\nd'
    r = Registry()
    c = r.counter("x_total", "X.", [capped("v", 5)])
    nasty = 'q"uote\\back\nline'
    c.inc(nasty)
    fam = parse(r.render())
    assert sample_value(fam, "x_total", v=nasty) == 1


def test_many_threads_lose_no_increments() -> None:
    r = Registry()
    c = r.counter("x_total", "X.", [closed("k", "a", "b")])
    h = r.histogram("h_seconds", "H.", buckets=(1.0,))
    g = r.gauge("g", "G.")

    def work() -> None:
        for i in range(5000):
            c.inc("a" if i % 2 else "b")
            h.observe(0.5)
            g.inc()
            g.dec()

    threads = [threading.Thread(target=work) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert c.value("a") + c.value("b") == 16 * 5000
    assert h.snapshot()[0][0] == 16 * 5000
    assert g.value() == 0


async def test_many_asyncio_tasks_lose_no_increments() -> None:
    r = Registry()
    c = r.counter("x_total", "X.")
    g: Gauge = r.gauge("g", "G.")

    async def work() -> None:
        for _ in range(500):
            c.inc()
            g.inc()
            await asyncio.sleep(0)
            g.dec()

    await asyncio.gather(*(work() for _ in range(200)))
    assert c.value() == 100_000
    assert g.value() == 0


def test_render_while_writing_never_breaks() -> None:
    r = Registry()
    h: Histogram = r.histogram("h_seconds", "H.", [capped("k", 50)], buckets=(1.0,))
    stop = threading.Event()

    def writer() -> None:
        i = 0
        while not stop.is_set():
            h.observe(0.1, str(i % 40))
            i += 1

    t = threading.Thread(target=writer)
    t.start()
    try:
        for _ in range(100):
            fam = parse(r.render())
            # a histogram's +Inf bucket always equals its count within one rendering
            for key in {lbl["k"] for _, lbl, _ in fam["h_seconds"].samples if "k" in lbl}:
                inf = sample_value(fam, "h_seconds", "h_seconds_bucket", k=key, le="+Inf")
                cnt = sample_value(fam, "h_seconds", "h_seconds_count", k=key)
                assert inf == cnt
    finally:
        stop.set()
        t.join()

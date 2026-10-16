"""A small metrics registry with a closed catalogue (Phase 19c, ADR-165).

Counters, gauges and fixed-bucket histograms, exposed in the Prometheus text format 0.0.4. It is
hand-written to keep the dependency budget; ADR-165 records when `prometheus-client` would replace
it.

Safety rules the types enforce (they are the point of the module):

- Every metric is declared up front with its label names. A label is either *closed* (a fixed
  set of values; anything else becomes ``other``) or *capped* (at most ``max_values`` distinct
  values are ever admitted; later ones become ``other``). A metric also has a hard ceiling on its
  series. Request-controlled text can therefore never grow memory without bound, whatever a caller
  passes. Callers still must not pass tenant ids, paths or addresses: the catalogue test forbids
  such label *names*, and the sentinel test checks exposition output.
- The hot path is one lock acquisition and a dictionary update. There is no I/O, no allocation
  beyond the label tuple, and no ``await``, so the same lock is safe for threads and for asyncio
  tasks.
"""

import math
import re
import threading
import time
from bisect import bisect_left
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from types import TracebackType
from typing import Final, Literal, Protocol

OTHER: Final = "other"
MAX_LABEL_VALUE_LENGTH: Final = 80
DEFAULT_MAX_SERIES: Final = 500

# Defaults (seconds): 1 ms .. 30 s, roughly 2.5x steps; fits ingestion acks and slow reads.
LATENCY_BUCKETS: Final[tuple[float, ...]] = (
    0.001,
    0.0025,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
)
# Defaults (bytes): 256 B .. 64 MiB in 4x steps; covers an event up to the largest artifact.
BYTES_BUCKETS: Final[tuple[float, ...]] = tuple(float(256 * 4**i) for i in range(10))

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_LABEL_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

MetricType = Literal["counter", "gauge", "histogram"]


@dataclass(frozen=True)
class Label:
    """One label of a metric: closed (``values``) or capped (``max_values``), never free."""

    name: str
    values: frozenset[str] | None = None
    max_values: int = 0

    def __post_init__(self) -> None:
        if not _LABEL_NAME.match(self.name) or self.name.startswith("__") or self.name == "le":
            raise ValueError(f"invalid label name {self.name!r}")
        if (self.values is None) == (self.max_values <= 0):
            raise ValueError(f"label {self.name!r} needs exactly one of values or max_values")
        if self.values is not None and (not self.values or OTHER in self.values):
            raise ValueError(f"label {self.name!r}: values must be non-empty and exclude 'other'")


def closed(name: str, *values: str) -> Label:
    return Label(name, values=frozenset(values))


def capped(name: str, max_values: int) -> Label:
    return Label(name, max_values=max_values)


class LockLike(Protocol):
    def __enter__(self) -> object: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
        /,
    ) -> None: ...


class Metric:
    """Shared declaration data and the label discipline of all three types."""

    type: MetricType

    def __init__(
        self,
        name: str,
        help_: str,
        labels: Sequence[Label],
        lock: LockLike,
        max_series: int,
    ) -> None:
        if not _NAME.match(name):
            raise ValueError(f"invalid metric name {name!r}")
        if len({label.name for label in labels}) != len(labels):
            raise ValueError(f"{name}: duplicate label names")
        self.name = name
        self.help = help_
        self.labels = tuple(labels)
        self._lock = lock
        self._max_series = max_series
        self._seen: list[set[str]] = [set() for _ in self.labels]
        self._overflow_key: tuple[str, ...] = (OTHER,) * len(self.labels)

    @property
    def label_names(self) -> tuple[str, ...]:
        return tuple(label.name for label in self.labels)

    def _key(
        self, values: tuple[str, ...], exists: Callable[[tuple[str, ...]], bool]
    ) -> tuple[str, ...]:
        """Resolve raw label values to an admitted key. Call with the lock held."""
        if len(values) != len(self.labels):
            raise ValueError(f"{self.name} takes labels {self.label_names}, got {len(values)}")
        out: list[str] = []
        for label, value, seen in zip(self.labels, values, self._seen, strict=True):
            if len(value) > MAX_LABEL_VALUE_LENGTH:
                value = value[:MAX_LABEL_VALUE_LENGTH]
            if label.values is not None:
                out.append(value if value in label.values else OTHER)
            elif value in seen:
                out.append(value)
            elif len(seen) < label.max_values and value != OTHER:
                seen.add(value)
                out.append(value)
            else:
                out.append(OTHER)
        key = tuple(out)
        if not exists(key) and self._series_count() >= self._max_series:
            return self._overflow_key
        return key

    def _series_count(self) -> int:  # pragma: no cover - overridden
        raise NotImplementedError

    def series_count(self) -> int:
        with self._lock:
            return self._series_count()

    def collect(self) -> list[tuple[str, tuple[str, ...], float]]:  # pragma: no cover
        raise NotImplementedError


class Counter(Metric):
    type: MetricType = "counter"

    def __init__(
        self, name: str, help_: str, labels: Sequence[Label], lock: LockLike, max_series: int
    ) -> None:
        super().__init__(name, help_, labels, lock, max_series)
        self._values: dict[tuple[str, ...], float] = {}
        if not self.labels:
            self._values[()] = 0.0

    def inc(self, *label_values: str, amount: float = 1.0) -> None:
        if amount < 0:
            raise ValueError("a counter only goes up")
        with self._lock:
            key = self._key(label_values, self._values.__contains__)
            self._values[key] = self._values.get(key, 0.0) + amount

    def value(self, *label_values: str) -> float:
        with self._lock:
            key = self._key(label_values, self._values.__contains__)
            return self._values.get(key, 0.0)

    def _series_count(self) -> int:
        return len(self._values)

    def collect(self) -> list[tuple[str, tuple[str, ...], float]]:
        with self._lock:
            items = sorted(self._values.items())
        return [("", key, value) for key, value in items]


class Gauge(Metric):
    type: MetricType = "gauge"

    def __init__(
        self, name: str, help_: str, labels: Sequence[Label], lock: LockLike, max_series: int
    ) -> None:
        super().__init__(name, help_, labels, lock, max_series)
        self._values: dict[tuple[str, ...], float] = {}
        self._function: Callable[[], float] | None = None
        if not self.labels:
            self._values[()] = 0.0

    def set(self, value: float, *label_values: str) -> None:
        with self._lock:
            key = self._key(label_values, self._values.__contains__)
            self._values[key] = float(value)

    def inc(self, *label_values: str, amount: float = 1.0) -> None:
        with self._lock:
            key = self._key(label_values, self._values.__contains__)
            self._values[key] = self._values.get(key, 0.0) + amount

    def dec(self, *label_values: str, amount: float = 1.0) -> None:
        self.inc(*label_values, amount=-amount)

    def set_function(self, function: Callable[[], float]) -> None:
        """An unlabelled gauge computed at scrape time (uptime). It must be cheap and not raise."""
        if self.labels:
            raise ValueError("set_function is for unlabelled gauges")
        self._function = function

    def value(self, *label_values: str) -> float:
        with self._lock:
            key = self._key(label_values, self._values.__contains__)
            return self._values.get(key, 0.0)

    def clear(self) -> None:
        """Forget all series (a scrape-time gauge that is re-set whole, so stale series vanish)."""
        with self._lock:
            self._values.clear()
            if not self.labels:
                self._values[()] = 0.0

    def _series_count(self) -> int:
        return len(self._values)

    def collect(self) -> list[tuple[str, tuple[str, ...], float]]:
        if self._function is not None:
            try:
                return [("", (), float(self._function()))]
            except Exception:
                return [("", (), math.nan)]
        with self._lock:
            items = sorted(self._values.items())
        return [("", key, value) for key, value in items]


class Histogram(Metric):
    type: MetricType = "histogram"

    def __init__(
        self,
        name: str,
        help_: str,
        labels: Sequence[Label],
        lock: LockLike,
        max_series: int,
        buckets: Sequence[float] = LATENCY_BUCKETS,
    ) -> None:
        super().__init__(name, help_, labels, lock, max_series)
        bounds = tuple(float(b) for b in buckets)
        if not bounds or list(bounds) != sorted(set(bounds)) or math.inf in bounds:
            raise ValueError("buckets must be strictly increasing, finite and non-empty")
        self.buckets = bounds
        # key -> [per-bucket counts (len(bounds)+1, last is +Inf), sum]
        self._values: dict[tuple[str, ...], list[float]] = {}
        if not self.labels:
            self._values[()] = [0.0] * (len(bounds) + 2)

    def observe(self, value: float, *label_values: str) -> None:
        index = bisect_left(self.buckets, value)  # first bound >= value; len() means +Inf
        with self._lock:
            key = self._key(label_values, self._values.__contains__)
            row = self._values.get(key)
            if row is None:
                row = self._values[key] = [0.0] * (len(self.buckets) + 2)
            row[index] += 1.0
            row[-1] += value  # the sum lives in the last slot

    def _series_count(self) -> int:
        return len(self._values)

    def snapshot(self, *label_values: str) -> tuple[list[float], float]:
        """(non-cumulative bucket counts incl. +Inf, sum) of one series; for tests."""
        with self._lock:
            key = self._key(label_values, self._values.__contains__)
            row = self._values.get(key) or [0.0] * (len(self.buckets) + 2)
            return list(row[:-1]), row[-1]

    def collect(self) -> list[tuple[str, tuple[str, ...], float]]:
        with self._lock:
            items = sorted((key, list(row)) for key, row in self._values.items())
        out: list[tuple[str, tuple[str, ...], float]] = []
        for key, row in items:
            running = 0.0
            for bound, count in zip((*self.buckets, math.inf), row[:-1], strict=True):
                running += count
                out.append(("_bucket", (*key, format_bound(bound)), running))
            out.append(("_sum", key, row[-1]))
            out.append(("_count", key, running))
        return out


def format_value(value: float) -> str:
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return repr(value)


def format_bound(bound: float) -> str:
    """A histogram ``le`` value: ``+Inf`` or the float's shortest repr (``0.005``, ``1.0``)."""
    return "+Inf" if math.isinf(bound) else repr(bound)


def escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def escape_help(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n")


class Timer(AbstractContextManager["Timer"]):
    """``with Timer(histogram, "label"):`` observes the elapsed seconds on exit."""

    def __init__(self, histogram: Histogram, *label_values: str) -> None:
        self._histogram = histogram
        self._labels = label_values
        self._started = 0.0

    def __enter__(self) -> "Timer":
        self._started = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        self._histogram.observe(time.perf_counter() - self._started, *self._labels)


def _new_lock() -> LockLike:
    return threading.Lock()


class Registry:
    """The closed catalogue. Declare every metric once; ``seal()`` forbids later declarations."""

    def __init__(self, *, lock_factory: Callable[[], LockLike] = _new_lock) -> None:
        self._metrics: dict[str, Metric] = {}
        self._lock_factory = lock_factory
        self._sealed = False

    def _declare[M: Metric](self, metric: M) -> M:
        if self._sealed:
            raise RuntimeError(f"registry is sealed; cannot declare {metric.name}")
        if metric.name in self._metrics:
            raise ValueError(f"metric {metric.name} is already declared")
        self._metrics[metric.name] = metric
        return metric

    def counter(
        self,
        name: str,
        help_: str,
        labels: Sequence[Label] = (),
        *,
        max_series: int = DEFAULT_MAX_SERIES,
    ) -> Counter:
        return self._declare(Counter(name, help_, labels, self._lock_factory(), max_series))

    def gauge(
        self,
        name: str,
        help_: str,
        labels: Sequence[Label] = (),
        *,
        max_series: int = DEFAULT_MAX_SERIES,
    ) -> Gauge:
        return self._declare(Gauge(name, help_, labels, self._lock_factory(), max_series))

    def histogram(
        self,
        name: str,
        help_: str,
        labels: Sequence[Label] = (),
        *,
        buckets: Sequence[float] = LATENCY_BUCKETS,
        max_series: int = DEFAULT_MAX_SERIES,
    ) -> Histogram:
        return self._declare(
            Histogram(name, help_, labels, self._lock_factory(), max_series, buckets=buckets)
        )

    def seal(self) -> None:
        self._sealed = True

    @property
    def metrics(self) -> Mapping[str, Metric]:
        return dict(self._metrics)

    def series_total(self) -> int:
        return sum(m.series_count() for m in self._metrics.values())

    def render(self) -> str:
        """Prometheus text format 0.0.4; metrics and series in a deterministic order."""
        lines: list[str] = []
        for name in sorted(self._metrics):
            metric = self._metrics[name]
            lines.append(f"# HELP {name} {escape_help(metric.help)}")
            lines.append(f"# TYPE {name} {metric.type}")
            label_names = metric.label_names
            for suffix, values, number in metric.collect():
                names = (*label_names, "le") if suffix == "_bucket" else label_names
                if names:
                    body = ",".join(
                        f'{n}="{escape_label_value(v)}"' for n, v in zip(names, values, strict=True)
                    )
                    lines.append(f"{name}{suffix}{{{body}}} {format_value(number)}")
                else:
                    lines.append(f"{name}{suffix} {format_value(number)}")
        return "\n".join(lines) + "\n"


CONTENT_TYPE: Final = "text/plain; version=0.0.4; charset=utf-8"

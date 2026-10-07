"""Background exporter: the only code that does I/O (INV-4).

The application thread only appends to the bounded buffer. One daemon thread drains it in
batches and hands them to a sink:

* `HttpSink`   POST /v1/events/batch with gzip, timeouts and the ADR-013 retry policy
* `LocalSink`  append JSON lines to a file (durable, works without a backend)
* offline mode has no sink: events stay in the bounded buffer

Nothing in here may raise into the application, block it, or grow without bound.
"""

import gzip
import http.client
import json
import logging
import random
import threading
import time
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

from blackbox import ids
from blackbox.buffer import EventBuffer
from blackbox.config import Config
from blackbox.events import MAX_EVENT_BYTES
from blackbox.stats import Stats

log = logging.getLogger("blackbox")
Event = dict[str, Any]
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class Sink(Protocol):
    def deliver(self, events: list[Event], abort: threading.Event) -> None: ...

    def close(self) -> None: ...


class _RateLimitedLog:
    """At most one warning per kind per minute: a down backend must not flood the host's logs."""

    def __init__(self) -> None:
        self._last: dict[str, float] = {}

    def warning(self, kind: str, message: str, *args: object) -> None:
        now = time.monotonic()
        if now - self._last.get(kind, -1e9) >= 60:
            self._last[kind] = now
            log.warning(message, *args)


def serialize(event: Event, stats: Stats) -> bytes | None:
    """One event as compact JSON, or None (counted) if it cannot or must not be sent."""
    try:
        raw = json.dumps(event, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError, RecursionError):
        stats.add("dropped_invalid")
        return None
    if len(raw) > MAX_EVENT_BYTES:
        stats.add("dropped_oversize")
        return None
    return raw


def parse_retry_after(value: str | None, cap: float) -> float | None:
    """Seconds from a Retry-After header (delta-seconds or HTTP-date), capped; None if unusable."""
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
            seconds = when.timestamp() - time.time()
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
    if seconds != seconds:  # NaN
        return None
    return min(max(seconds, 0.0), cap)


class LocalSink:
    """Appends JSON lines. Safe for one process; several processes appending to one file can
    interleave large lines, so give each process its own path."""

    def __init__(self, path: str, stats: Stats) -> None:
        self._path, self._stats = path, stats
        self._lock = threading.Lock()

    def deliver(self, events: list[Event], abort: threading.Event) -> None:
        lines = [raw for e in events if (raw := serialize(e, self._stats)) is not None]
        try:
            with self._lock, open(self._path, "ab") as handle:
                handle.write(b"\n".join(lines) + b"\n" if lines else b"")
            self._stats.add("events_written_local", len(lines))
        except OSError:
            self._stats.add("dropped_export_failed", len(lines))

    def close(self) -> None:
        return None


class HttpSink:
    def __init__(self, config: Config, stats: Stats) -> None:
        self._c, self._stats = config, stats
        parts = urlsplit(config.endpoint)
        self._https = parts.scheme == "https"
        self._host = parts.hostname or "localhost"
        self._port = parts.port or (443 if self._https else 80)
        self._path = (parts.path.rstrip("/") or "") + "/v1/events/batch"
        self._conn: http.client.HTTPConnection | None = None
        self._rng = random.Random()  # noqa: S311  (jitter, not security)
        self._log = _RateLimitedLog()

    def close(self) -> None:
        self._reset()

    def _reset(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: S110
                pass
            self._conn = None

    def deliver(self, events: list[Event], abort: threading.Event) -> None:
        chunk: list[bytes] = []
        size = 0
        for event in events:
            raw = serialize(event, self._stats)
            if raw is None:
                continue
            if chunk and size + len(raw) + 1 > self._c.max_batch_bytes:
                self._send(chunk, abort)
                chunk, size = [], 0
            chunk.append(raw)
            size += len(raw) + 1
        if chunk:
            self._send(chunk, abort)

    def _wait(self, seconds: float, abort: threading.Event) -> bool:
        """Sleep that shutdown can interrupt. True if it is time to give up."""
        if self._c.wait is not None:  # tests inject a recorder instead of real sleeping
            self._c.wait(seconds)
            return abort.is_set()
        return abort.wait(seconds)

    def _backoff(self, attempt: int) -> float:
        ceiling = min(self._c.backoff_max, self._c.backoff_base * (2**attempt))
        return self._rng.uniform(0, ceiling)  # full jitter

    def _send(self, parts: list[bytes], abort: threading.Event) -> None:
        # One batch_id for every retry of this exact payload (idempotency, ADR-013).
        batch_id = ids.new_id("bat")
        self._send_with_retry(parts, batch_id, abort)

    def _send_with_retry(self, parts: list[bytes], batch_id: str, abort: threading.Event) -> None:
        attempt = 0
        while True:
            status, retry_after, body = self._post(parts, batch_id)
            if status is not None and 200 <= status < 300:
                self._record_success(len(parts), body)
                return
            if status == 413 and len(parts) > 1:  # too big: halve and send each half
                mid = len(parts) // 2
                self._send_with_retry(parts[:mid], ids.new_id("bat"), abort)
                self._send_with_retry(parts[mid:], ids.new_id("bat"), abort)
                return
            retryable = status is None or status in _RETRYABLE_STATUS
            if not retryable:
                self._stats.add("dropped_rejected", len(parts))
                self._log.warning("rejected", "blackbox: server rejected a batch (HTTP %s)", status)
                return
            attempt += 1
            if attempt >= self._c.max_attempts or abort.is_set():
                self._stats.add("dropped_export_failed", len(parts))
                self._log.warning(
                    "failed", "blackbox: dropping %d events after %d attempts", len(parts), attempt
                )
                return
            self._stats.add("retries")
            delay = self._backoff(attempt - 1)
            if status in (429, 503) and retry_after is not None:
                delay = retry_after  # the server told us when; never retry sooner
            if self._wait(delay, abort):
                self._stats.add("dropped_export_failed", len(parts))
                return

    def _post(self, parts: list[bytes], batch_id: str) -> tuple[int | None, float | None, bytes]:
        """One HTTP attempt: (status or None on transport failure, Retry-After seconds, body)."""
        body = b'{"batch_id":"%s","events":[%s]}' % (batch_id.encode(), b",".join(parts))
        headers = {
            "Authorization": f"Bearer {self._c.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "agent-black-box-python",
        }
        if len(body) >= self._c.gzip_min_bytes:
            body = gzip.compress(body, compresslevel=3)
            headers["Content-Encoding"] = "gzip"
        reused = self._conn is not None
        try:
            if self._conn is None:
                cls = http.client.HTTPSConnection if self._https else http.client.HTTPConnection
                self._conn = cls(self._host, self._port, timeout=self._c.http_timeout)
            self._conn.request("POST", self._path, body=body, headers=headers)
            response = self._conn.getresponse()
            data = response.read(1 << 20)
            retry_after = parse_retry_after(
                response.getheader("Retry-After"), self._c.retry_after_max
            )
            if response.getheader("Connection", "").lower() == "close":
                self._reset()
            return response.status, retry_after, data
        except Exception as exc:  # network errors, timeouts, protocol garbage: all retryable
            self._reset()
            if reused and isinstance(exc, (ConnectionError, http.client.HTTPException)):
                # A keep-alive connection the server closed while we were idle is not a failure:
                # redo it once on a fresh connection without spending a retry attempt.
                return self._post(parts, batch_id)
            self._log.warning("transport", "blackbox: export failed (%s)", type(exc).__name__)
            return None, None, b""

    def _record_success(self, sent: int, body: bytes) -> None:
        try:
            result = json.loads(body)
            rejected = int(result.get("rejected", 0))
            duplicates = int(result.get("duplicates", 0))
        except (ValueError, TypeError, AttributeError):
            rejected = duplicates = 0
        self._stats.add("batches_sent")
        self._stats.add("events_sent", max(0, sent - rejected))
        self._stats.add("events_duplicate", duplicates)
        if rejected:
            self._stats.add("rejected_by_server", rejected)
            self._log.warning("partial", "blackbox: server rejected %d events in a batch", rejected)


class Exporter:
    """Owns the drain thread. `notify()` is the only thing the application thread calls."""

    def __init__(
        self, config: Config, buffer: EventBuffer, stats: Stats, sink: Sink | None
    ) -> None:
        self._c, self._buffer, self._stats, self._sink = config, buffer, stats, sink
        self._wake = threading.Event()
        self._abort = threading.Event()
        self._stopping = False
        self._cv = threading.Condition()
        self._inflight = 0
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._sink is None or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="blackbox-exporter", daemon=True)
        self._thread.start()

    def notify(self) -> None:
        if len(self._buffer) >= self._c.batch_size:
            self._wake.set()

    def _run(self) -> None:
        while True:
            self._wake.wait(self._c.flush_interval)
            self._wake.clear()
            try:
                self._drain()
            except Exception:  # the exporter must survive anything
                self._stats.add("internal_errors")
            if self._abort.is_set() or (self._stopping and len(self._buffer) == 0):
                return

    def _drain(self) -> None:
        assert self._sink is not None
        while not self._abort.is_set():
            with self._cv:
                # Counted before the events leave the buffer, so flush() can never observe
                # "buffer empty and nothing in flight" while a batch is between the two.
                self._inflight += 1
            batch = self._buffer.take(self._c.batch_size)
            if not batch:
                with self._cv:
                    self._inflight -= 1
                    self._cv.notify_all()
                break
            try:
                self._sink.deliver(batch, self._abort)
            except Exception:
                self._stats.add("internal_errors")
                self._stats.add("dropped_export_failed", len(batch))
            finally:
                with self._cv:
                    self._inflight -= 1
                    self._cv.notify_all()
        with self._cv:
            self._cv.notify_all()

    def flush(self, timeout: float) -> bool:
        """Wait until everything queued so far was handed to the sink. True if it did."""
        if self._sink is None or self._thread is None:
            return len(self._buffer) == 0
        deadline = time.monotonic() + timeout
        self._wake.set()
        with self._cv:
            while len(self._buffer) > 0 or self._inflight > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._wake.set()
                self._cv.wait(min(remaining, 0.05))
        return True

    def shutdown(self, timeout: float) -> bool:
        """Stop the thread after a final drain; whatever misses the deadline is dropped, counted.

        Worst case it returns after `timeout + http_timeout + 0.5` seconds (the in-flight request).
        """
        if self._thread is None:
            return True
        self._stopping = True
        self._wake.set()
        self._thread.join(timeout)
        clean = not self._thread.is_alive()
        if not clean:
            self._abort.set()  # give up on retries; the in-flight request ends by its timeout
            self._thread.join(self._c.http_timeout + 0.5)
        leftover = self._buffer.clear()
        self._stats.add("dropped_export_failed", leftover)
        if self._sink is not None:
            self._sink.close()
        return clean and leftover == 0

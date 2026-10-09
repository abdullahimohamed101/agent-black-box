"""Artifact uploads (ADR-030): large or sensitive text kept out of events, sent in the background.

`BlackBox.upload_artifact` only enqueues; one daemon thread PUTs to `/v1/artifacts/{id}`. The queue
is bounded in items and bytes (INV-4), retries are bounded, and nothing here raises into the
application. The artifact id is generated locally so an event can reference it at once; a reference
whose upload was dropped is a normal state for the UI ("not available").
"""

import gzip
import hashlib
import http.client
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

from blackbox.config import Config
from blackbox.exporter import _RETRYABLE_STATUS, parse_retry_after
from blackbox.stats import Stats

log = logging.getLogger("blackbox")

MAX_ARTIFACT_BYTES = 8 * 1024 * 1024  # the server's default cap (ABB_ARTIFACT_MAX_BYTES)
MAX_QUEUE_ITEMS = 64
MAX_QUEUE_BYTES = 32 * 1024 * 1024
MAX_ATTEMPTS = 3
KINDS = frozenset({"diff", "stdout", "stderr", "file", "text", "other"})


@dataclass(frozen=True)
class _Upload:
    artifact_id: str
    run_id: str
    kind: str
    name: str | None
    data: bytes


class ArtifactUploader:
    def __init__(self, config: Config, stats: Stats) -> None:
        self._c, self._stats = config, stats
        try:
            parts = urlsplit(config.endpoint)
            port = parts.port
        except ValueError:  # a bad endpoint disables HTTP mode in Config; never fail construction
            parts, port = urlsplit(""), None
        self._https = parts.scheme == "https"
        self._host = parts.hostname or "localhost"
        self._port = port or (443 if self._https else 80)
        self._base = parts.path.rstrip("/") or ""
        self._queue: deque[_Upload] = deque()
        self._bytes = 0
        self._cv = threading.Condition()
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._aborted = False
        self._inflight = False
        self._conn: http.client.HTTPConnection | None = None

    @property
    def active(self) -> bool:
        return self._c.mode == "http" and bool(self._c.api_key)

    def enqueue(self, upload: _Upload) -> bool:
        """Queue an upload. False (counted) if the queue is full or the client is shutting down."""
        size = len(upload.data)
        with self._cv:
            if (
                self._stopping
                or size > MAX_ARTIFACT_BYTES
                or len(self._queue) >= MAX_QUEUE_ITEMS
                or self._bytes + size > MAX_QUEUE_BYTES
            ):
                self._stats.add("dropped_artifacts")
                return False
            self._queue.append(upload)
            self._bytes += size
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name="blackbox-artifacts", daemon=True
                )
                self._thread.start()
            self._cv.notify_all()
        return True

    def flush(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._cv:
            while self._queue or self._inflight:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cv.wait(min(remaining, 0.05))
        return True

    def shutdown(self, timeout: float) -> bool:
        with self._cv:
            self._stopping = True
            self._cv.notify_all()
            thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        clean = not thread.is_alive()
        if not clean:
            with self._cv:
                self._aborted = True
                dropped = len(self._queue)
                self._queue.clear()
                self._bytes = 0
            self._stats.add("dropped_artifacts", dropped)
            thread.join(self._timeout() + 0.5)
        self._reset()
        return clean

    # -- the upload thread -------------------------------------------------------------------------

    def _timeout(self) -> float:
        return max(self._c.http_timeout, 5.0)

    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._queue and not self._stopping:
                    self._cv.wait(0.5)
                if not self._queue:
                    return
                item = self._queue.popleft()
                self._bytes -= len(item.data)
                self._inflight = True
            try:
                self._upload(item)
            except Exception:  # the thread must survive anything
                self._stats.add("internal_errors")
                self._stats.add("dropped_artifacts")
            finally:
                with self._cv:
                    self._inflight = False
                    self._cv.notify_all()

    def _reset(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: S110
                pass
            self._conn = None

    def _upload(self, item: _Upload) -> None:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            status, retry_after = self._put(item)
            if status is not None and 200 <= status < 300:
                self._stats.add("artifacts_uploaded")
                self._stats.add("artifact_bytes_uploaded", len(item.data))
                return
            retryable = status is None or status in _RETRYABLE_STATUS
            if not retryable or attempt == MAX_ATTEMPTS or self._aborted:
                self._stats.add("dropped_artifacts")
                log.warning("blackbox: dropped an artifact upload (HTTP %s)", status)
                return
            delay = retry_after if retry_after is not None else min(0.25 * 2**attempt, 2.0)
            if self._c.wait is not None:
                self._c.wait(delay)
            else:
                time.sleep(min(delay, 5.0))

    def _put(self, item: _Upload) -> tuple[int | None, float | None]:
        query = f"run_id={quote(item.run_id)}&kind={quote(item.kind)}"
        if item.name:
            query += f"&name={quote(item.name[:256])}"
        headers = {
            "Authorization": f"Bearer {self._c.api_key}",
            "Content-Type": "text/plain; charset=utf-8",
            "X-Content-SHA256": hashlib.sha256(item.data).hexdigest(),
            "User-Agent": "agent-black-box-python",
        }
        body = item.data
        if len(body) >= self._c.gzip_min_bytes:
            body = gzip.compress(body, compresslevel=3)
            headers["Content-Encoding"] = "gzip"
        try:
            if self._conn is None:
                cls = http.client.HTTPSConnection if self._https else http.client.HTTPConnection
                self._conn = cls(self._host, self._port, timeout=self._timeout())
            path = f"{self._base}/v1/artifacts/{quote(item.artifact_id)}?{query}"
            self._conn.request("PUT", path, body=body, headers=headers)
            response = self._conn.getresponse()
            response.read(1 << 20)
            retry_after = parse_retry_after(
                response.getheader("Retry-After"), self._c.retry_after_max
            )
            if response.getheader("Connection", "").lower() == "close":
                self._reset()
            return response.status, retry_after
        except Exception as exc:
            self._reset()
            log.warning("blackbox: artifact upload failed (%s)", type(exc).__name__)
            return None, None

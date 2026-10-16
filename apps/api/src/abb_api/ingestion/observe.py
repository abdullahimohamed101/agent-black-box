"""Ingestion's metrics: the telemetry counters and the acknowledgement SLIs (Phase 19c, spec 117).

One `IngestObserver` wraps one ingestion request in the router, so it sees every way the request
can end, including body-read and decode failures that never reach the service:

- ``committed``: the request is answered 2xx. The observer is told *after* ``ingest_batch``
  returned, i.e. after the transaction committed, so accepted-event durability is
  ``committed / (committed + error)``. An acknowledged request whose commit failed raised before
  that point and counts as ``error``.
- ``rejected``: the client's fault (an `AppError` below 500, or a single-event request the
  endpoint answers with a 4xx).
- ``error``: ours (an `AppError` of 500 or above, or any unexpected exception).

The duration histogram is observed exactly once per request, whatever the outcome. Reasons are a
closed vocabulary derived from the stable error codes; anything unknown becomes ``other``.
"""

import time
from types import TracebackType

from fastapi import Request

from abb_api.core.errors import AppError
from abb_api.ingestion.schemas import BatchResponse, EventErrorOut
from abb_api.obs.catalogue import Catalogue

_REASON_OF_CODE = {
    "BATCH_INVALID": "invalid",
    "EVENT_INVALID": "invalid",
    "REQUEST_INVALID": "invalid",
    "UNSUPPORTED_MEDIA_TYPE": "invalid",
    "UNSUPPORTED_ENCODING": "invalid",
    "PAYLOAD_TOO_LARGE": "too_large",
    "EVENT_TOO_LARGE": "too_large",
    "RATE_LIMITED": "rate_limited",
    "QUOTA_EXCEEDED": "quota_exceeded",
    "PROJECT_KEY_REQUIRED": "unauthorized",
    "DEPENDENCY_UNAVAILABLE": "unavailable",
    "REDACTION_FAILED": "redaction_failed",
    "RESERVED_EVENT_TYPE": "reserved",
    "RUN_PROJECT_MISMATCH": "conflict",
    "EVENT_SCHEMA_UNSUPPORTED": "schema_unsupported",
}


def reason_of(code: str) -> str:
    return _REASON_OF_CODE.get(code, "other")


class IngestObserver:
    """``with IngestObserver(request) as obs: ... obs.committed(...)``."""

    def __init__(self, request: Request) -> None:
        self._cat: Catalogue = request.app.state.observability.catalogue
        self._started = time.perf_counter()
        self._events_counted = False
        self._committed = False

    def __enter__(self) -> "IngestObserver":
        return self

    def committed(self, response: BatchResponse, size_bytes: int) -> None:
        """The batch is committed. Per-event refusals inside it are counted here."""
        self._committed = True
        self._cat.telemetry_events_received_total.inc(amount=float(response.accepted))
        self._cat.telemetry_bytes_received_total.inc(amount=float(size_bytes))
        self.events_refused(response.errors)

    def events_refused(self, errors: list[EventErrorOut], *, count_quota: bool = True) -> None:
        """Count refused events (once). The batch endpoint reports quota refusals inside its 202,
        where nothing else would count them; the single-event endpoint raises them as an
        `AppError`, which the error handler counts (so it passes ``count_quota=False``)."""
        self._events_counted = True
        for error in errors:
            self._cat.telemetry_events_rejected_total.inc(reason_of(error.code))
            if count_quota and error.quota is not None:
                self._cat.quota_refusals_total.inc(error.quota)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        cat = self._cat
        if exc is None:
            outcome = "committed" if self._committed else "rejected"
        elif isinstance(exc, AppError):
            outcome = "rejected" if exc.status_code < 500 else "error"
            if not self._events_counted:  # a request-level refusal counts once, as a request
                cat.telemetry_events_rejected_total.inc(reason_of(exc.code))
        elif isinstance(exc, Exception):
            outcome = "error"
        else:  # cancellation: the client went away; neither our success nor our failure
            return
        cat.ingest_acks_total.inc(outcome)
        cat.ingest_ack_duration_seconds.observe(time.perf_counter() - self._started)

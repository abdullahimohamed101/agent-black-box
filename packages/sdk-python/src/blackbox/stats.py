"""Local counters (spec §67.3: expose dropped-event counters locally). Cheap, lock-protected."""

import threading

COUNTERS = (
    "events_created",
    "events_sent",  # accepted by the server (including duplicates)
    "events_duplicate",
    "events_written_local",
    "dropped_p0",
    "dropped_p1",
    "dropped_p2",
    "dropped_invalid",  # malformed event type or builder failure
    "dropped_oversize",  # an event over the contract's size limit
    "dropped_rejected",  # whole batch refused with a non-retryable status
    "dropped_export_failed",  # retries exhausted or shutdown deadline passed
    "dropped_redaction_error",  # a user redaction callback failed
    "rejected_by_server",  # individual events the server refused inside a 202
    "attributes_dropped",
    "payloads_dropped",
    "redactions",
    "batches_sent",
    "retries",
    "internal_errors",
)


class Stats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values = dict.fromkeys(COUNTERS, 0)

    def add(self, name: str, amount: int = 1) -> None:
        if amount:
            with self._lock:
                self._values[name] += amount

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._values)

    def __getitem__(self, name: str) -> int:
        with self._lock:
            return self._values[name]

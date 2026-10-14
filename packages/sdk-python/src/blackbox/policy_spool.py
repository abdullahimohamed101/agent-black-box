"""A small bounded memory of `policy.warning` events the exporter had to drop (review KI-152).

A warning is written when the policy service could not be asked, so the API is often down at that
moment and the exporter's bounded retry window (a few seconds) ends before it returns. The
warning is the only trace that an action went ahead (or was refused) without a decision, so the
exporter hands the dropped ones to this spool and the client puts them back in the buffer when the
API answers again: after the next successful batch or policy decision, and at flush and shutdown.

Bounded (`CAP` events, oldest dropped first, counted), standard library only, in memory only.
The count of warnings lost to the bound is reported once, as one more `policy.warning` carrying
that number, the next time the API is reachable.
"""

import copy
import threading
from collections import deque
from typing import Any

from blackbox import ids
from blackbox.events import now_rfc3339
from blackbox.stats import Stats

CAP = 1000
WARNING_TYPE = "policy.warning"
REASON_DROPPED = "policy_warnings_dropped"
Event = dict[str, Any]


class WarningSpool:
    def __init__(self, stats: Stats, cap: int = CAP) -> None:
        self._stats = stats
        self._cap = cap
        self._lock = threading.Lock()
        self._events: deque[Event] = deque()
        self._lost = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)

    def offer(self, events: list[Event]) -> None:
        """Keep the `policy.warning` events among `events` (called from the exporter thread)."""
        with self._lock:
            for event in events:
                if event.get("event_type") != WARNING_TYPE:
                    continue
                if len(self._events) >= self._cap:
                    self._events.popleft()
                    self._lost += 1
                    self._stats.add("policy_warnings_lost")
                self._events.append(event)

    def take_all(self) -> list[Event]:
        """Everything kept, oldest first, and (as a last event) the report of what was lost."""
        with self._lock:
            events, self._events = list(self._events), deque()
            lost, self._lost = self._lost, 0
        if lost and events:
            events.append(_report(events[0], lost))
        return events


def _report(template: Event, lost: int) -> Event:
    """One warning saying how many were lost, on the run of the oldest one kept."""
    report = copy.deepcopy(template)
    report["event_id"] = ids.new_id("evt")
    report["occurred_at"] = now_rfc3339()
    report.pop("sequence", None)
    report["attributes"] = {
        "policy.capability": template.get("attributes", {}).get("policy.capability", "invalid"),
        "policy.reason": REASON_DROPPED,
        "policy.warnings_dropped": lost,
    }
    return report

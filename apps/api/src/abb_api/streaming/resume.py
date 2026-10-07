"""Arrival-time resume for live streams (ADR-022, D1).

Canonical order cannot drive a stream: a late event sorts before events already sent. An insertion
counter can skip rows that commit out of order. Streams follow `received_at`, which is
stamped before the transaction starts, so a row can become visible after rows with a later
`received_at`. Every poll re-reads a trailing overlap window and the cursor drops what this
connection already sent.
Clients de-duplicate by `event_id` anyway, so the overlap is also what a reconnect may repeat.
A transaction that stays open longer than the overlap can still be missed; the window is a setting.
"""

import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta

from abb_event_schema.event import Event
from abb_event_schema.ids import to_uuid

EventRow = tuple[Event, bool]  # (event without payload, has_payload)


class ArrivalCursor:
    """Tracks one connection's position in arrival time and which events it already sent."""

    def __init__(self, start: datetime | None, overlap: timedelta) -> None:
        if overlap < timedelta(0):
            raise ValueError("overlap must not be negative")
        self._overlap = overlap
        self._lower: datetime | None = None if start is None else start - overlap
        self._newest: datetime | None = None
        self._sent: dict[uuid.UUID, datetime] = {}

    @property
    def lower_bound(self) -> datetime | None:
        """Read events with `received_at >=` this; None means from the start of the run."""
        return self._lower

    def unseen(self, rows: Iterable[EventRow]) -> list[EventRow]:
        """The rows this connection has not sent yet, recorded as sent; advances the lower bound."""
        fresh: list[EventRow] = []
        for event, has_payload in rows:
            key = to_uuid(event.event_id)
            if key in self._sent:
                continue
            self._sent[key] = event.received_at
            fresh.append((event, has_payload))
            if self._newest is None or event.received_at > self._newest:
                self._newest = event.received_at
        if self._newest is not None:
            self._lower = self._newest - self._overlap
            # Rows older than the lower bound are never read again, so they need no memory.
            self._sent = {k: v for k, v in self._sent.items() if v >= self._lower}
        return fresh

"""Deterministic event ordering (spec §65.1).

Arrival order is meaningless. If every event carries a `sequence`, order by it (the SDK's logical
clock is immune to wall-clock skew); otherwise fall back to `occurred_at` so events that lack a
sequence are never exiled to the end of the timeline. Ties break on `received_at` then
`event_id` so the result is a total, reproducible order.

Multiple emitters (Phase 9, ADR-070): `sequence` is only comparable within one emitter, so a
process that did not create the run (a continued run, or a forked child that inherited it) sends
no `sequence`. One such event puts the whole run in time order, with `sequence` as the tiebreak
inside one emitter. Across emitters the order therefore follows their clocks; a skewed clock
interleaves them wrongly in the chronological list, which is why span nesting uses
`parent_span_id` and never time (spec §65.3).
"""

from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from typing import Protocol, TypeVar

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class Orderable(Protocol):
    @property
    def event_id(self) -> str: ...
    @property
    def sequence(self) -> int | None: ...
    @property
    def occurred_at(self) -> datetime: ...


def _received_at(event: Orderable) -> datetime:
    return getattr(event, "received_at", None) or _EPOCH


def sequence_key(event: Orderable) -> tuple[int, datetime, datetime, str]:
    return (event.sequence or 0, event.occurred_at, _received_at(event), event.event_id)


def time_key(event: Orderable) -> tuple[datetime, int, datetime, str]:
    return (event.occurred_at, event.sequence or 0, _received_at(event), event.event_id)


T = TypeVar("T", bound=Orderable)


def sort_events(events: Iterable[T]) -> list[T]:
    """Return events in canonical order. Pure: the input is not modified."""
    items: Sequence[T] = list(events)
    if items and all(e.sequence is not None for e in items):
        return sorted(items, key=sequence_key)
    return sorted(items, key=time_key)

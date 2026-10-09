"""Capture what an adapter emits through the SDK; validate it against the event contract."""

from collections.abc import Callable
from typing import Any

from abb_event_schema.event import EventIn
from blackbox import BlackBox


def new_client(**options: Any) -> BlackBox:
    """An SDK client that queues events in memory (no network, no key needed)."""
    options.setdefault("mode", "offline")
    return BlackBox(api_key="abb_live_conformance.secret", project="conformance", **options)


def capture(drive: Callable[[BlackBox], None], **options: Any) -> list[dict[str, Any]]:
    """Run `drive(bb)` against an offline client and return the events, in creation order."""
    bb = new_client(**options)
    try:
        drive(bb)
        return bb.buffered_events()
    finally:
        bb.shutdown(0)


def validate_events(events: list[dict[str, Any]]) -> None:
    """Every event must satisfy the authoritative contract (`abb_event_schema.EventIn`)."""
    for event in events:
        EventIn.model_validate(event)

"""Attribute readers tolerant of hostile values, and the event-to-token normalisation shared by
the sequence and cascade detectors. Everything is partitioned per `agent_id` (per emitter)."""

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from abb_event_schema.event import Event

from abb_api.findings.model import label

CLOSERS = ("completed", "failed")
_CLOSER_PREFIXES = {
    "tool.call": None,  # token is tool:<name>
    "llm.request": "llm",
    "shell.command": "shell",
    "db.query": "db",
}


def attr_str(event: Event, key: str) -> str | None:
    value = event.attributes.get(key)
    return value if isinstance(value, str) else None


def attr_int(event: Event, key: str) -> int | None:
    """An integer attribute; booleans, floats, NaN and strings are not integers."""
    value = event.attributes.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def attr_number(event: Event, key: str) -> float | None:
    value = event.attributes.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def is_error(event: Event) -> bool:
    status = event.status.value if event.status is not None else None
    return status in ("error", "timeout") or event.event_type.endswith(".failed")


def by_agent(events: Iterable[Event]) -> dict[str, list[Event]]:
    """Events per emitter, each list in the (canonical) order of the input."""
    groups: dict[str, list[Event]] = {}
    for event in events:
        groups.setdefault(event.agent_id, []).append(event)
    return groups


def closer_kind(event: Event) -> str | None:
    """`tool.call`, `llm.request`, `shell.command` or `db.query` for a completed/failed event."""
    prefix, _, suffix = event.event_type.rpartition(".")
    if suffix in CLOSERS and prefix in _CLOSER_PREFIXES:
        return prefix
    return None


def tool_name(event: Event) -> str | None:
    name = attr_str(event, "tool.name")
    return label(name) if name is not None else None


def token_for(event: Event) -> str | None:
    """The sequence token of an event, or None when it takes no part in sequences."""
    kind = closer_kind(event)
    if kind == "tool.call":
        return "tool:" + (tool_name(event) or "?")
    if kind is not None:
        return _CLOSER_PREFIXES[kind]
    event_type = event.event_type
    if event_type.startswith(("file.", "git.")):
        return label(event_type)
    if event_type == "http.response":
        return "http"
    return None


@dataclass(frozen=True)
class Token:
    text: str
    event: Event


def tokens_by_agent(events: Sequence[Event]) -> dict[str, list[Token]]:
    out: dict[str, list[Token]] = {}
    for event in events:
        text = token_for(event)
        if text is not None:
            out.setdefault(event.agent_id, []).append(Token(text, event))
    return out


def millis(later: Event, earlier: Event) -> int:
    return round((later.occurred_at - earlier.occurred_at).total_seconds() * 1000)

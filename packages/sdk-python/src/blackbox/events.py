"""Event envelope builder (event contract 1.0) using only the standard library.

The server validates events; this module only avoids sending guaranteed rejects (oversized or
malformed values) and fills the envelope. Drift from the contract is prevented by tests that
validate everything built here with `abb_event_schema` (ADR-013).
"""

import json
import math
import re
from datetime import datetime, timezone
from typing import Any

from blackbox import ids
from blackbox.stats import Stats

SCHEMA_VERSION = "1.0"

# Mirrors abb_event_schema.limits (asserted equal by tests).
MAX_EVENT_BYTES = 256 * 1024
MAX_INLINE_PAYLOAD_BYTES = 64 * 1024
MAX_ATTRIBUTES = 64
MAX_ATTRIBUTE_KEY_LENGTH = 128
MAX_ATTRIBUTE_STRING_LENGTH = 4096
MAX_ATTRIBUTE_LIST_ITEMS = 64
MAX_TAGS = 16
MAX_TAG_LENGTH = 64
MAX_SAFE_INTEGER = 2**53 - 1

EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){1,3}$")
_ATTR_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")
_SPAN_KINDS = frozenset("agent llm tool shell db http file git retrieval evaluation custom".split())

# Spec §67.4 priority classes. Unlisted types (including custom.*) are P1.
_P0 = frozenset(
    """run.started run.completed run.failed run.cancelled agent.started agent.completed
    agent.spawned llm.request.failed tool.call.failed shell.command.failed db.query.failed
    retry.attempted timeout.occurred loop.detected policy.warning policy.action.blocked
    secret.detected approval.requested approval.granted approval.denied span.failed""".split()
)
_P2 = frozenset("agent.state_changed http.request http.response".split())


def priority_of(event_type: str) -> int:
    if event_type in _P0:
        return 0
    return 2 if event_type in _P2 else 1


def now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _clean_scalar(value: Any) -> Any:
    """A contract-legal scalar, or None if the value cannot be sent."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if abs(value) <= MAX_SAFE_INTEGER else None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value.replace("\x00", "")[:MAX_ATTRIBUTE_STRING_LENGTH]
    return None


def clean_attributes(attributes: dict[str, Any] | None, stats: Stats) -> dict[str, Any]:
    """Keep legal keys with scalar / flat-list values; count what had to be dropped."""
    if not attributes:
        return {}
    out: dict[str, Any] = {}
    dropped = 0
    for key, value in attributes.items():
        if len(out) >= MAX_ATTRIBUTES:
            dropped += 1
            continue
        if (
            not isinstance(key, str)
            or len(key) > MAX_ATTRIBUTE_KEY_LENGTH
            or not _ATTR_KEY_RE.match(key)
        ):
            dropped += 1
            continue
        if isinstance(value, (list, tuple)):
            items = [_clean_scalar(v) for v in list(value)[:MAX_ATTRIBUTE_LIST_ITEMS]]
            if any(i is None for i in items):
                dropped += 1
                continue
            out[key] = items
            continue
        cleaned = _clean_scalar(value)
        if cleaned is None:
            dropped += 1
        else:
            out[key] = cleaned
    stats.add("attributes_dropped", dropped)
    return out


def clean_tags(tags: tuple[str, ...] | list[str] | None) -> list[str]:
    return [t.replace("\x00", "")[:MAX_TAG_LENGTH] for t in (tags or ())[:MAX_TAGS] if t]


def name_attr(value: Any, limit: int = 256) -> str:
    return str(value).replace("\x00", "")[:limit] or "unnamed"


def span_kind(value: str) -> str:
    return value if value in _SPAN_KINDS else "custom"


def check_payload(payload: dict[str, Any], stats: Stats) -> dict[str, Any] | None:
    """The payload if it is plain JSON within the inline limit, else None (never raises)."""
    try:
        size = len(json.dumps(payload, separators=(",", ":"), allow_nan=False).encode())
    except (TypeError, ValueError, RecursionError):
        stats.add("payloads_dropped")
        return None
    if size > MAX_INLINE_PAYLOAD_BYTES:
        stats.add("payloads_dropped")
        return None
    return payload


def build_event(
    *,
    event_type: str,
    run_id: str,
    trace_id: str,
    agent_id: str,
    agent_version: str | None,
    sequence: int,
    span_id: str | None = None,
    parent_span_id: str | None = None,
    status: str | None = None,
    duration_ms: float | None = None,
    attributes: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    tags: tuple[str, ...] | list[str] | None = None,
    sdk_version: str,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "event_id": ids.new_id(ids.EVENT),
        "run_id": run_id,
        "trace_id": trace_id,
        "agent_id": agent_id,
        "event_type": event_type,
        "occurred_at": now_rfc3339(),
        "sequence": sequence,
        "attributes": attributes or {},
        "sdk": {"name": "agent-black-box-python", "version": sdk_version},
    }
    if span_id:
        event["span_id"] = span_id
    if parent_span_id:
        event["parent_span_id"] = parent_span_id
    if agent_version:
        event["agent_version"] = agent_version[:128]
    if status:
        event["status"] = status
    if duration_ms is not None and math.isfinite(duration_ms):
        event["duration_ms"] = max(0.0, round(duration_ms, 3))
    if payload is not None:
        event["payload"] = payload
    if tags:
        event["tags"] = list(tags)
    return event

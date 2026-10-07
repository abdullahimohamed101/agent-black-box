"""Builders for test events. IDs are fixed so failures are reproducible."""

from typing import Any

WS = "ws_01J9ZZZZZZZZZZZZZZZZZZZZZZ"
PRJ = "prj_01J9ZZZZZZZZZZZZZZZZZZZZZZ"
RUN = "run_01J9ZZZZZZZZZZZZZZZZZZZZZY"
TRACE = "trc_01J9ZZZZZZZZZZZZZZZZZZZZZY"


def evt(n: int) -> str:
    return f"evt_01J9{n:022d}"[:30].replace(" ", "0")


def spn(n: int) -> str:
    return f"spn_01J9{n:022d}"[:30]


def make_event(**overrides: Any) -> dict[str, Any]:
    """A valid client-side event (tool.call.completed) with overrides applied."""
    event: dict[str, Any] = {
        "schema_version": "1.0",
        "event_id": evt(1),
        "run_id": RUN,
        "trace_id": TRACE,
        "span_id": spn(1),
        "agent_id": "coding-agent",
        "event_type": "tool.call.completed",
        "occurred_at": "2026-10-06T20:13:22.029Z",
        "sequence": 1,
        "status": "success",
        "duration_ms": 843,
        "attributes": {"tool.name": "github", "tool.operation": "search_issues"},
        "tags": ["production"],
    }
    event.update(overrides)
    return {k: v for k, v in event.items() if v is not ...}


def without(event: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: v for k, v in event.items() if k not in keys}

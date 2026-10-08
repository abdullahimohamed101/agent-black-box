"""Golden canonical-event fixtures: deterministic normalization and compare/update helpers."""

import json
import os
from pathlib import Path
from typing import Any

_VOLATILE_ATTRS = ("llm.latency_ms", "tool.latency_ms")


def _id(prefix: str, number: int) -> str:
    return f"{prefix}_{number:026d}"  # digits are valid Crockford base32: still a legal id


def normalize(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace ids, times and durations by deterministic, contract-valid stand-ins.

    The result is still a valid event list (so a golden file doubles as a schema fixture) but is
    identical on every run: ids are numbered by first appearance, time advances 1 ms per event.
    """
    maps: dict[str, dict[str, int]] = {"run": {}, "trc": {}, "spn": {}}

    def mapped(prefix: str, value: str | None) -> str | None:
        if value is None:
            return None
        table = maps[prefix]
        return _id(prefix, table.setdefault(value, len(table) + 1))

    out: list[dict[str, Any]] = []
    for index, event in enumerate(events, start=1):
        e = json.loads(json.dumps(event))  # deep copy, JSON-safe
        e["event_id"] = _id("evt", index)
        e["run_id"] = mapped("run", e["run_id"])
        e["trace_id"] = mapped("trc", e["trace_id"])
        for key in ("span_id", "parent_span_id"):
            if e.get(key) is not None:
                e[key] = mapped("spn", e[key])
        e["occurred_at"] = f"2026-01-01T00:00:00.{index:06d}Z"
        if "duration_ms" in e:
            e["duration_ms"] = 1.0
        for key in _VOLATILE_ATTRS:
            if key in e["attributes"]:
                e["attributes"][key] = 1.0
        e["sdk"] = {"name": e["sdk"]["name"], "version": "0"} if "sdk" in e else {}
        if not e["sdk"]:
            del e["sdk"]
        out.append(e)
    return out


def assert_golden(path: Path, events: list[dict[str, Any]]) -> None:
    """Compare normalized events to the fixture at `path`; `ABB_UPDATE_GOLDEN=1` rewrites it."""
    actual = normalize(events)
    if os.environ.get("ABB_UPDATE_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n")
    expected = json.loads(path.read_text())
    assert actual == expected, (
        f"{path.name} differs from the golden fixture (ABB_UPDATE_GOLDEN=1 rewrites it)"
    )

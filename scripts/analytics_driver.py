"""Writes a small, known set of runs into a live API for the analytics E2E (plain HTTP, no SDK).

    DRIVER_API_URL=... DRIVER_WRITE_KEY=... python scripts/analytics_driver.py EXPECT_FILE

Four runs over three days with exact costs (see EXPECT below), a retry, a failure, a timeout, and hostile
agent/model/tool/run names (markup in telemetry must render as text). Writes the expected figures to
EXPECT_FILE as JSON for the browser test to compare against.
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

from abb_event_schema.ids import IdKind, new_id

API, KEY = os.environ["DRIVER_API_URL"], os.environ["DRIVER_WRITE_KEY"]
HOSTILE = '<img src=x onerror="window.__pwned=1">'
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def post(events: list[dict]) -> None:
    request = urllib.request.Request(
        f"{API}/v1/events/batch",
        data=json.dumps({"events": events}).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        body = json.load(response)
    assert body["rejected"] == 0, body


def run(start: datetime, agent: str, end: str, seconds: int, name: str, steps: list) -> None:
    ids = {"run_id": new_id(IdKind.RUN), "trace_id": new_id(IdKind.TRACE)}
    events: list[dict] = []

    def add(kind: str, at: datetime, attributes: dict, **extra: object) -> None:
        events.append(
            {
                "schema_version": "1.0", "event_id": new_id(IdKind.EVENT), **ids,
                "agent_id": agent, "event_type": kind, "sequence": len(events) + 1,
                "occurred_at": at.isoformat().replace("+00:00", "Z"), "attributes": attributes,
                **extra,
            }
        )  # fmt: skip

    add("run.started", start, {"run.name": name})
    scope = new_id(IdKind.SPAN)
    for step in steps:
        kind = step[0]
        if kind == "llm":
            _, attrs, retry = step
            if retry:
                add("retry.attempted", start, {"retry.attempt": 1}, span_id=scope)
            add("llm.request.completed", start, attrs, span_id=new_id(IdKind.SPAN),
                parent_span_id=scope, status="success", duration_ms=1500)  # fmt: skip
        elif kind == "tool":
            _, tool, status, ms = step
            add("tool.call.completed" if status == "success" else "tool.call.failed", start,
                {"tool.name": tool}, span_id=new_id(IdKind.SPAN), status=status, duration_ms=ms)  # fmt: skip
    add("tool.call.started", start, {"tool.name": "scope"}, span_id=scope)
    add("run.completed", start + timedelta(seconds=seconds), {}, status=end)
    post(events)


X = {"llm.provider": "example-provider", "llm.model": "model-x"}
run(NOW - timedelta(hours=1), "coding-agent", "success", 12, "Fix login bug",
    [("llm", {**X, "llm.input_tokens": 1_000_000}, False), ("tool", "git", "success", 120)])  # fmt: skip
run(NOW - timedelta(hours=2), "coding-agent", "error", 30, "<script>alert(1)</script> failing run",
    [("llm", {**X, "llm.input_tokens": 500_000}, False), ("llm", {**X, "llm.input_tokens": 500_000}, True),
     ("tool", "git", "error", 800)])  # fmt: skip
run(NOW - timedelta(days=1), "research-agent", "timeout", 60, "Slow research",
    [("llm", {"llm.provider": HOSTILE, "llm.model": HOSTILE, "llm.input_tokens": 9,
              "cost.estimated_usd": 0.25}, False)])  # fmt: skip
run(NOW - timedelta(days=2), "research-agent", "success", 20, "Summarise docs",
    [("llm", {"llm.provider": "example-provider", "llm.model": "model-x-mini-1",
              "llm.input_tokens": 1_000_000, "llm.output_tokens": 1_000_000}, False),
     ("tool", HOSTILE, "success", 300)])  # fmt: skip

with open(sys.argv[1], "w") as handle:
    json.dump(
        {"runs": 4, "success": 2, "failed": 1, "timed_out": 1, "total_usd": 7.75, "retry_usd": 1.5,
         "hostile": HOSTILE, "tools": ["git", HOSTILE]},
        handle,
    )  # fmt: skip
print("driver wrote 4 runs")  # noqa: T201

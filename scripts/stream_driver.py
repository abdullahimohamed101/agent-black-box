"""Writes a run into a live API for the streaming E2E, on cue from the test.

    stream_driver.py sdk                      the Python SDK, a small agent run with pauses
    stream_driver.py http COUNT INTERVAL_S    COUNT single events posted over HTTP, INTERVAL_S apart

Env: DRIVER_API_URL, DRIVER_WRITE_KEY. The run is created first and its id printed (JSON line); the driver then
waits for a line on stdin ("go") so the browser can open the page before anything else happens. In http mode every
event's acknowledgement time is printed (epoch ms, taken when the API answered 202) for the latency measurement.
Run with: uv run --project packages/sdk-python python scripts/stream_driver.py ...
"""

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

from abb_event_schema.ids import IdKind, new_id

API = os.environ["DRIVER_API_URL"]
KEY = os.environ["DRIVER_WRITE_KEY"]


def say(**fields: object) -> None:
    print(json.dumps(fields), flush=True)  # noqa: T201


def wait_for_go() -> None:
    line = sys.stdin.readline()
    if line.strip() != "go":
        raise SystemExit("driver expected 'go'")


def post(events: list[dict]) -> float:
    request = urllib.request.Request(
        f"{API}/v1/events/batch",
        data=json.dumps({"events": events}).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        body = json.load(response)
    acked = time.time() * 1000
    assert body["accepted"] == len(events) and body["rejected"] == 0, body
    return acked


def sdk_agent() -> None:
    from blackbox import BlackBox

    bb = BlackBox(api_key=KEY, endpoint=API)
    with bb.run("Live: fix login bug") as run:
        bb.flush(timeout=5)  # run.started is on the server before the browser opens the page
        say(run_id=run.run_id)
        wait_for_go()
        with run.span("search_code", kind="tool") as span:
            span.set_attribute("tool.name", "search_code")
            time.sleep(1.2)
        with run.llm_call("demo", "model-x") as llm:
            time.sleep(1.2)
            llm.record_usage(1200, 80)
        with run.span("run_tests", kind="tool") as span:
            span.set_attribute("tool.name", "run_tests")
            time.sleep(1.2)
    bb.shutdown()
    say(done=True)


def http_agent(count: int, interval: float) -> None:
    run, trace = new_id(IdKind.RUN), new_id(IdKind.TRACE)
    clock = datetime.now(timezone.utc)
    n = 0

    def event(kind: str, **extra: object) -> dict:
        nonlocal n
        n += 1
        at = (clock + timedelta(milliseconds=n)).isoformat().replace("+00:00", "Z")
        return {
            "schema_version": "1.0",
            "event_id": new_id(IdKind.EVENT),
            "run_id": run,
            "trace_id": trace,
            "agent_id": "coding-agent",
            "event_type": kind,
            "sequence": n,
            "occurred_at": at,
            "attributes": extra.pop("attributes", {}),
            **extra,
        }

    post([event("run.started", attributes={"run.name": "Live: latency probe"})])
    say(run_id=run)
    wait_for_go()
    for i in range(count):
        e = event(
            "tool.call.completed",
            attributes={"tool.name": f"step_{i}"},
            status="success",
            span_id=new_id(IdKind.SPAN),
        )
        say(event_id=e["event_id"], index=i + 1, ack_ms=post([e]))
        time.sleep(interval)
    post([event("run.completed", status="success")])
    say(done=True)


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "sdk":
        sdk_agent()
    elif mode == "http":
        http_agent(int(sys.argv[2]), float(sys.argv[3]))
    else:
        raise SystemExit(f"unknown mode {mode}")

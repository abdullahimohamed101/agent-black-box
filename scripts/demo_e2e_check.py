"""Helpers of scripts/demo-e2e.sh (Phase 20, D9). Run with `uv run --project apps/api python`.

    summarise RUNS_FILE   read the demo's "<name> <run id>" lines, wait (bounded polls, no wall-clock assertions) until each
                          run is derived, and print one JSON document: per run, what the agents DID. Run ids, event ids,
                          timestamps, durations and everything that depends on clock or thread timing are left out.
    report SUMMARY_FILE   print the counts the plan asks to see: runs, failed runs, findings by type, agents per run.

Environment: DEMO_E2E_API, DEMO_E2E_KEY_FILE (a key with runs:read). Nothing here prints a credential or event content.
"""

import collections
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

API = os.environ.get("DEMO_E2E_API", "http://localhost:8170")
POLL_CEILING_SECONDS = 120.0
POLL_STEP_SECONDS = 0.5
# Summary fields that only depend on what happened (counts, tokens, spend), not on when or in which thread order.
STABLE_SUMMARY = (
    "agent_count",
    "llm_calls",
    "tool_calls",
    "error_count",
    "event_count",
    "retry_count",
    "input_tokens",
    "output_tokens",
    "llm_spans_ok",
    "tool_spans_ok",
    "llm_spans_finished",
    "tool_spans_finished",
    "files_modified",
    "unpriced_calls",
    "clamped_calls",
    "estimated_cost_usd",
    "cost_by_source_usd",
)
STABLE_AGENT = (
    "agent_id",
    "parent_agent_id",
    "state",
    "llm_calls",
    "tool_calls",
    "error_count",
    "event_count",
    "estimated_cost_usd",
)


def fail(message: str) -> None:
    print(f"DEMO-E2E CHECK FAILED: {message}", file=sys.stderr)
    raise SystemExit(1)


def client() -> httpx.Client:
    key = Path(os.environ["DEMO_E2E_KEY_FILE"]).read_text().strip()
    return httpx.Client(base_url=API, headers={"Authorization": f"Bearer {key}"}, timeout=15)


def pages(http: httpx.Client, path: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 500}
        if cursor:
            params["cursor"] = cursor
        response = http.get(path, params=params)
        response.raise_for_status()
        body = response.json()
        items += body["items"]
        cursor = body.get("next_cursor")
        if not cursor:
            return items


def settled(
    http: httpx.Client, run_id: str
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Bounded poll: the summary is current and counts every stored event, and the findings are current."""
    deadline = time.monotonic() + POLL_CEILING_SECONDS
    while True:
        run = http.get(f"/v1/runs/{run_id}").raise_for_status().json()
        events = pages(http, f"/v1/runs/{run_id}/events")
        findings = http.get(f"/v1/runs/{run_id}/findings").raise_for_status().json()
        if (
            run["summary_state"] == "current"
            and run["status"] not in ("RUNNING", "QUEUED")
            and run["summary"].get("event_count") == len(events)
            and findings["state"]["status"] == "current"
            and findings["state"].get("event_count_seen") == len(events)
        ):
            return run, events, findings
        if time.monotonic() > deadline:
            fail(
                f"{run_id} was not derived in {POLL_CEILING_SECONDS:.0f} s: summary_state={run['summary_state']} "
                f"events={len(events)}/{run['summary'].get('event_count')} findings={findings['state']}"
            )
        time.sleep(POLL_STEP_SECONDS)


def normalise(http: httpx.Client, run_id: str) -> dict[str, Any]:
    run, events, findings = settled(http, run_id)
    spans = pages(http, f"/v1/runs/{run_id}/spans")
    name_of = {s["id"]: s["name"] for s in spans}
    summary = run["summary"]
    event_counts = collections.Counter(
        f"{e['agent_id']} {e['event_type']} {e.get('status') or '-'}" for e in events
    )
    return {
        "name": run["name"],
        "status": run["status"],
        "agent_id": run["agent_id"],
        "ordering_mode": run["ordering_mode"],
        "has_first_error": run.get("first_error") is not None,
        "summary": {k: summary.get(k) for k in STABLE_SUMMARY},
        "agents": sorted(
            ({k: a.get(k) for k in STABLE_AGENT} for a in summary.get("agents", [])),
            key=lambda a: a["agent_id"],
        ),
        "models": sorted(summary.get("models", [])),
        "events": dict(sorted(event_counts.items())),
        "spans": sorted(
            f"{s['agent_id']} | {s['kind']} | {s['name']} | {s['status']} | parent={name_of.get(s['parent_span_id'])}"
            for s in spans
        ),
        "findings": sorted(
            f"{f['category']} {f['finding_type']} {f['kind']} {f['severity']} {f['confidence']}"
            for f in findings["items"]
        ),
        "health": {
            "overall": findings["health"]["overall"],
            **{d["name"]: d["status"] for d in findings["health"]["dimensions"]},
        },
    }


def summarise(runs_file: str) -> None:
    pairs = [line.split() for line in Path(runs_file).read_text().splitlines() if line.strip()]
    with client() as http:
        out = {name: normalise(http, run_id) for name, run_id in pairs}
    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    print()


def report(summary_file: str) -> None:
    data: dict[str, dict[str, Any]] = json.loads(Path(summary_file).read_text())
    failed = [n for n, r in data.items() if r["status"] != "SUCCESS"]
    by_type = collections.Counter(
        f for r in data.values() for f in (x.split()[1] for x in r["findings"])
    )
    print(f"runs: {len(data)}; failed runs: {len(failed)} ({', '.join(failed) or 'none'})")
    print(
        "findings by type: " + (", ".join(f"{t}={n}" for t, n in sorted(by_type.items())) or "none")
    )
    print("agents per run: " + ", ".join(f"{n}={len(r['agents'])}" for n, r in data.items()))
    multi = data.get("multi-agent")
    print(f"multi-agent lanes (agents): {len(multi['agents']) if multi else 'n/a'}")
    print(
        "error spans: "
        + ", ".join(f"{n}={sum('| error |' in s for s in r['spans'])}" for n, r in data.items())
    )


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("summarise", "report"):
        fail("usage: demo_e2e_check.py summarise RUNS_FILE | report SUMMARY_FILE")
    {"summarise": summarise, "report": report}[sys.argv[1]](sys.argv[2])

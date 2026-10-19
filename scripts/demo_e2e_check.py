"""Helpers of scripts/demo-e2e.sh (Phase 20, D9). Run with `uv run --project apps/api python`.

    summarise RUNS_FILE [REALISM_FILE]
                          read the demo's "<name> <run id>" lines, wait (bounded polls, no wall-clock assertions) until each
                          run is derived, and print one JSON document: per run, what the agents DID. Run ids, event ids,
                          timestamps, durations and everything that depends on clock or thread timing are left out. With REALISM_FILE
                          the volatile facts (start time, duration, cost, model) are written there for `asks`, never compared.
    report SUMMARY_FILE   print the counts the plan asks to see: runs, failed runs, findings by type, agents per run.
    asks SUMMARY_FILE [REALISM_FILE]
                          check the demo against the data the video needs (docs/design/launch/video-brief.md, section 6): a failed
                          run with a one-word error early, findings with evidence, five lanes, a waiting approval, three security
                          rules, plain names, no real model names; with REALISM_FILE also: durations of 8 to 90 s, costs of $0.03 to $1.50, runs
                          spread over five or more days, three or more scripted models. Exits non-zero on the first ask that does not hold.
    approvals             with DEMO_E2E_SESSION (a development session cookie of the demo owner): the pending approval's
                          capability, resource and time left (hours, not minutes).

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
    return httpx.Client(
        base_url=API, headers={"Authorization": f"Bearer {key}"}, timeout=15
    )


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


def first_error(
    run: dict[str, Any], events: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Where the run first went wrong, as the run page says it: the step (event sequence) and the error type."""
    found = run.get("first_error")
    if not found:
        return None
    event = next((e for e in events if e["event_id"] == found["event_id"]), None)
    attributes = (event or {}).get("attributes") or {}
    return {
        "step": found["sequence"],
        "event_type": (event or {}).get("event_type"),
        "error_type": attributes.get("error.type") or attributes.get("tool.error_type"),
        "retries_after": found.get("retries_after"),
    }


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
        "first_error": first_error(run, events),
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


def volatile(http: httpx.Client, run_id: str) -> dict[str, Any]:
    """What differs between two plays on purpose (when it ran, how long it took): kept out of the determinism diff."""
    run = http.get(f"/v1/runs/{run_id}").raise_for_status().json()
    summary = run["summary"]
    return {
        "name": run["name"],
        "status": run["status"],
        "started_at": run["started_at"],
        "duration_ms": run["duration_ms"],
        "cost_usd": summary.get("estimated_cost_usd"),
        "models": sorted(summary.get("models", [])),
    }


def summarise(runs_file: str, realism_file: str | None = None) -> None:
    pairs = [
        line.split()
        for line in Path(runs_file).read_text().splitlines()
        if line.strip()
    ]
    with client() as http:
        out = {name: normalise(http, run_id) for name, run_id in pairs}
        if realism_file:
            Path(realism_file).write_text(
                json.dumps({n: volatile(http, i) for n, i in pairs}, indent=2, sort_keys=True)
            )
    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    print()


def report(summary_file: str) -> None:
    data: dict[str, dict[str, Any]] = json.loads(Path(summary_file).read_text())
    failed = [n for n, r in data.items() if r["status"] != "SUCCESS"]
    by_type = collections.Counter(
        f for r in data.values() for f in (x.split()[1] for x in r["findings"])
    )
    print(
        f"runs: {len(data)}; failed runs: {len(failed)} ({', '.join(failed) or 'none'})"
    )
    print(
        "findings by type: "
        + (", ".join(f"{t}={n}" for t, n in sorted(by_type.items())) or "none")
    )
    print(
        "agents per run: "
        + ", ".join(f"{n}={len(r['agents'])}" for n, r in data.items())
    )
    multi = data.get("multi-agent")
    print(f"multi-agent lanes (agents): {len(multi['agents']) if multi else 'n/a'}")
    print(
        "error spans: "
        + ", ".join(
            f"{n}={sum('| error |' in s for s in r['spans'])}" for n, r in data.items()
        )
    )


REAL_MODEL_WORDS = (
    "claude",
    "gpt",
    "anthropic",
    "openai",
    "gemini",
    "llama",
    "mistral",
)
SECURITY_RULES = {"destructive_command", "secret_file_read", "risky_git"}


def ask(condition: bool, label: str) -> None:
    if not condition:
        fail(f"ask not met: {label}")
    print(f"  ok  {label}")


def realism(path: str) -> None:
    """Runs that look like work: how long they took, what they cost, when they ran (the owner's review of the first draft)."""
    from datetime import datetime

    rows: dict[str, dict[str, Any]] = json.loads(Path(path).read_text())
    for name, r in rows.items():
        seconds = (r["duration_ms"] or 0) / 1000
        if r["status"] == "WAITING_FOR_APPROVAL":
            continue  # it is still running: its duration grows while it waits
        if name != "multi-agent":  # its delays are real sleeps, and this check runs with DEMO_PACE=0 (no pauses at all)
            ask(8 <= seconds <= 90, f"{name}: lasted {seconds:.0f} s (8 to 90 s)")
        cost = float(r["cost_usd"] or 0)
        ask(0.03 <= cost <= 1.5, f"{name}: cost ${cost:.3f} ($0.03 to $1.50)")
    days = {datetime.fromisoformat(r["started_at"].replace("Z", "+00:00")).date() for r in rows.values()}
    ask(len(days) >= 5, f"runs spread over {len(days)} different days (5 or more)")
    models = {m for r in rows.values() for m in r["models"]}
    ask(len(models) >= 3, f"{len(models)} scripted models ({sorted(models)})")


def asks(summary_file: str, realism_file: str | None = None) -> None:
    data: dict[str, dict[str, Any]] = json.loads(Path(summary_file).read_text())
    failed = {n: r for n, r in data.items() if r["status"] == "FAILED"}
    ask(
        len(data) >= 12 and len(failed) >= 3,
        f"at least 12 runs and 3 failed ({len(data)} runs, {len(failed)} failed)",
    )
    outage = data.get("support-outage")
    ask(
        outage is not None and outage["status"] == "FAILED",
        "a failed support run exists",
    )
    assert outage is not None
    fe = outage["first_error"]
    ask(
        fe is not None and fe["step"] <= 9 and (fe["error_type"] or "").isalpha(),
        f"first error at step <= 9 with a one-word error type ({fe and (fe['step'], fe['error_type'])})",
    )
    ask(
        bool(fe and fe["retries_after"] and fe["retries_after"] >= 1),
        "at least one retry after the first error",
    )
    severities = {f.split()[3] for f in outage["findings"]}
    ask(
        {"critical", "warning"} <= severities,
        f"critical and warning findings on the failed run ({sorted(severities)})",
    )
    ask(
        any(
            f.split()[1] == "retry_storm" for r in data.values() for f in r["findings"]
        ),
        "a retry-storm finding exists",
    )
    multi = data.get("multi-agent")
    lanes = [a["agent_id"] for a in (multi or {}).get("agents", [])]
    ask(
        len(lanes) == 5 and all(len(x) <= 12 for x in lanes),
        f"five lanes with names of 12 characters or fewer ({lanes})",
    )
    ask(
        data.get("coding-awaiting-approval", {}).get("status")
        == "WAITING_FOR_APPROVAL",
        "a run is waiting for approval",
    )
    types = {f.split()[1] for r in data.values() for f in r["findings"]}
    ask(
        SECURITY_RULES <= types,
        f"security findings for destructive command, secret file read and risky git ({sorted(types & SECURITY_RULES)})",
    )
    names = [r["name"] for r in data.values()]
    ask(
        all(
            len(n) <= 40
            and "@" not in n
            and not any(w in n.lower() for w in ("test", "foo"))
            for n in names
        ),
        "run names are plain (40 characters or fewer, no emails, no 'test' or 'foo')",
    )
    models = {
        m if isinstance(m, str) else str(m) for r in data.values() for m in r["models"]
    }
    ask(
        len(models) >= 2
        and not any(w in m.lower() for m in models for w in REAL_MODEL_WORDS),
        f"two or more scripted models, none a real one ({sorted(models)})",
    )
    ask(len({r["agent_id"] for r in data.values()}) >= 3, "three or more agents")
    if realism_file:
        realism(realism_file)


def approvals() -> None:
    with httpx.Client(
        base_url=API,
        cookies={"abb_session": os.environ["DEMO_E2E_SESSION"]},
        headers={"Origin": os.environ["DEMO_E2E_ORIGIN"]},
        timeout=15,
    ) as http:
        me = http.get("/v1/me").raise_for_status().json()
        workspace = next(
            m["workspace"]
            for m in me["memberships"]
            if m["workspace"]["slug"] == "local"
        )
        http.headers["x-abb-workspace"] = workspace["id"]
        items = (
            http.get("/v1/approvals", params={"state": "pending", "limit": 100})
            .raise_for_status()
            .json()["items"]
        )
    from datetime import datetime, timezone

    ask(len(items) >= 1, f"{len(items)} pending approval(s)")
    left_hours = min(
        (
            datetime.fromisoformat(a["expires_at"].replace("Z", "+00:00"))
            - datetime.now(timezone.utc)
        ).total_seconds()
        / 3600
        for a in items
    )
    ask(
        all(a["capability"] == "github.push" for a in items),
        "the request is a github.push",
    )
    ask(
        left_hours > 2,
        f"hours, not minutes, are left on the approval ({left_hours:.1f} h)",
    )


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "approvals" and len(sys.argv) == 2:
        approvals()
    elif command == "summarise" and len(sys.argv) in (3, 4):
        summarise(*sys.argv[2:])
    elif command == "asks" and len(sys.argv) in (3, 4):
        asks(*sys.argv[2:])
    elif command == "report" and len(sys.argv) == 3:
        report(sys.argv[2])
    else:
        fail(
            "usage: demo_e2e_check.py summarise RUNS_FILE [REALISM_FILE] | report SUMMARY_FILE | asks SUMMARY_FILE [REALISM_FILE] | approvals"
        )

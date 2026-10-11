"""API-side assertions of scripts/findings-e2e.sh (Phase 10). Run with `uv run --project apps/api python`.

    runs                      print {"<scenario>": "<run id>"} for the committed pathological fixtures
    wait [--after FILE]       wait (bounded) until every run's findings are `current` (and, with --after, were
                              recomputed after the snapshot in FILE)
    verify                    each scenario yields exactly its hand-written expected findings, every cited event
                              resolves, titles say "Probable:" iff probable, a VIEWER sees the same bytes as the OWNER
    snapshot FILE             store (id, created_at, fingerprint, evidence ...) of every finding and each run's computed_at
    compare FILE              the current findings equal the snapshot (a rebuild reproduces them, ids and created_at too)

Environment: E2E_API, E2E_WORKSPACE (slug), E2E_READ_KEY (runs:read), E2E_OWNER_SESSION, E2E_VIEWER_SESSION and
E2E_FIXTURES (the directory of `*.ndjson` / `*.expected.json`). Nothing here prints a credential or event content.
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

API = os.environ.get("E2E_API", "http://localhost:8135")
FIXTURES = Path(os.environ.get("E2E_FIXTURES", "apps/api/tests/fixtures/pathological"))
SCENARIOS = [
    "healthy",
    "looping",
    "identical",
    "retry_storm",
    "context_growth",
    "two_agents",
    "skewed",
    "cost_spike",
    "cascade",
    "failing",
]


def fail(message: str) -> None:
    print(f"FINDINGS-E2E CHECK FAILED: {message}", file=sys.stderr)
    raise SystemExit(1)


def load(name: str) -> dict[str, list[dict[str, Any]]]:
    runs: dict[str, list[dict[str, Any]]] = {}
    for line in (FIXTURES / f"{name}.ndjson").read_text().splitlines():
        wire = json.loads(line)
        runs.setdefault(wire["run_id"], []).append(wire)
    return runs


def target(name: str) -> tuple[str, list[dict[str, Any]]]:
    """The run a scenario's findings belong to: the last run for `cost_spike`, the only one otherwise."""
    runs = load(name)
    run_id = list(runs)[-1]
    return run_id, runs[run_id]


def all_run_ids() -> list[str]:
    return [r for name in SCENARIOS for r in load(name)]


def reader(kind: str) -> httpx.Client:
    if kind == "key":
        return httpx.Client(
            base_url=API,
            headers={"Authorization": f"Bearer {os.environ['E2E_READ_KEY']}"},
            timeout=30,
        )
    token = os.environ["E2E_OWNER_SESSION" if kind == "owner" else "E2E_VIEWER_SESSION"]
    client = httpx.Client(base_url=API, cookies={"abb_session": token}, timeout=30)
    me = client.get("/v1/me")
    me.raise_for_status()
    slug = os.environ["E2E_WORKSPACE"]
    for membership in me.json()["memberships"]:
        if membership["workspace"]["slug"] == slug:
            client.headers["x-abb-workspace"] = membership["workspace"]["id"]
            return client
    fail(f"the {kind} session is not a member of {slug}")
    raise AssertionError  # unreachable


def findings(client: httpx.Client, run_id: str) -> dict[str, Any]:
    response = client.get(f"/v1/runs/{run_id}/findings")
    if response.status_code != 200:
        fail(f"GET findings {response.status_code}")
    body: dict[str, Any] = response.json()
    return body


def computed(client: httpx.Client) -> dict[str, str | None]:
    return {r: findings(client, r)["state"]["computed_at"] for r in all_run_ids()}


def cmd_runs() -> None:
    print(json.dumps({name: target(name)[0] for name in SCENARIOS}))


def cmd_wait(after: str | None) -> None:
    before: dict[str, str | None] = json.loads(Path(after).read_text())["computed"] if after else {}
    client = reader("key")
    deadline = time.monotonic() + 90
    pending: list[str] = []
    while time.monotonic() < deadline:
        pending = []
        for run_id in all_run_ids():
            state = findings(client, run_id)["state"]
            fresh = state["computed_at"] is not None and (
                before.get(run_id) is None or state["computed_at"] > before[run_id]
            )
            if state["status"] != "current" or not fresh:
                pending.append(f"{run_id[:12]}={state['status']}")
        if not pending:
            print(f"{len(all_run_ids())} runs analysed and current")
            return
        time.sleep(1)
    fail(f"not current within 90 s: {', '.join(pending[:6])}")


def dig(value: Any, path: str) -> Any:
    for part in path.split("."):
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def same(key: str, actual: Any, wanted: Any) -> bool:
    if key.rsplit(".", 1)[-1] in ("cost_usd", "median_usd", "p95_usd"):
        from decimal import Decimal

        return Decimal(str(actual)) == Decimal(str(wanted))
    if isinstance(wanted, float):
        return abs(float(actual) - wanted) < 1e-6
    return bool(actual == wanted)


def cmd_verify() -> None:
    owner, viewer, key = reader("owner"), reader("viewer"), reader("key")
    checked = 0
    for name in SCENARIOS:
        run_id, wires = target(name)
        expected = json.loads((FIXTURES / f"{name}.expected.json").read_text())
        by_seq: dict[int, list[str]] = {}
        for w in wires:
            by_seq.setdefault(w["sequence"], []).append(w["event_id"])
        body = findings(owner, run_id)
        if body["state"]["status"] != "current":
            fail(f"{name}: state {body['state']['status']}")
        items = body["items"]
        if len(items) != len(expected["findings"]):
            fail(f"{name}: {len(items)} findings, expected {len(expected['findings'])}")
        remaining = list(items)
        for want in expected["findings"]:

            def event_id(seq: int | None) -> str | None:
                if seq is None:
                    return None
                ids = by_seq[seq]
                return (
                    ids[0] if len(ids) == 1 else None
                )  # ambiguous sequence (two emitters): checked below

            start, end = event_id(want["start_seq"]), event_id(want["end_seq"])
            match = next(
                (
                    f
                    for f in remaining
                    if f["finding_type"] == want["finding_type"]
                    and f["detector"] == want["detector"]
                    and (
                        want["start_seq"] is None
                        or f["start_event_id"] in by_seq[want["start_seq"]]
                    )
                    and (want["end_seq"] is None or f["end_event_id"] in by_seq[want["end_seq"]])
                ),
                None,
            )
            if match is None:
                fail(
                    f"{name}: no {want['finding_type']} at events {want['start_seq']}..{want['end_seq']}"
                )
            remaining.remove(match)
            if start is not None and match["start_event_id"] != start:
                fail(f"{name}: start event differs")
            if (match["kind"], match["severity"]) != (want["kind"], want["severity"]):
                fail(f"{name}: {want['finding_type']} is {match['kind']}/{match['severity']}")
            for k, v in want["evidence"].items():
                if not same(k, dig(match["evidence"], k), v):
                    fail(f"{name}: evidence {k}")
        for f in items:
            if f["title"].startswith("Probable:") != (f["kind"] == "probable"):
                fail(f"{name}: title/kind mismatch on {f['finding_type']}")
            if not re.fullmatch(r"[a-z0-9-]+@\d+\.\d+\.\d+", f["detector_version"]):
                fail(f"{name}: detector_version {f['detector_version']}")
            cited = {f["start_event_id"], f["end_event_id"], *f["evidence_event_ids"]} - {None}
            known = {w["event_id"] for w in wires}
            if not cited <= known:
                fail(f"{name}: a cited event is not in the run")
            for event in sorted(cited):  # the link resolves through the events route
                if key.get(f"/v1/runs/{run_id}/events/{event}").status_code != 200:
                    fail(f"{name}: cited event does not resolve")
        # viewer parity (D8): identical bytes for the OWNER and a VIEWER, no content in either
        viewer_body = findings(viewer, run_id)
        if json.dumps(viewer_body["items"], sort_keys=True) != json.dumps(items, sort_keys=True):
            fail(f"{name}: VIEWER items differ from OWNER items")
        if json.dumps(viewer_body["health"], sort_keys=True) != json.dumps(
            body["health"], sort_keys=True
        ):
            fail(f"{name}: VIEWER health differs from OWNER health")
        text = json.dumps(  # the fingerprint is a published digest of (detector, type, start id, key), not content
            {
                **viewer_body,
                "items": [
                    {k: v for k, v in f.items() if k != "fingerprint"} for f in viewer_body["items"]
                ],
            }
        )
        for leak in ("pytest", "src/app.py", "sha256:", "Bearer"):
            if leak in text:
                fail(f"{name}: the findings response contains event content ({leak})")
        if re.search(r"\b[0-9a-f]{40,}\b", text):
            fail(f"{name}: a long hex run (a digest?) appears in the findings response")
        if "score" in text:
            fail(f"{name}: a score appears in health")
        checked += len(items)
    healthy = findings(owner, target("healthy")[0])
    if healthy["items"]:
        fail("healthy has findings")
    spike = findings(owner, target("cost_spike")[0])["items"]
    if [f["finding_type"] for f in spike] != ["cost_spike"]:
        fail("the expensive run lacks exactly one cost_spike")
    for run_id in list(load("cost_spike"))[:-1]:
        if findings(owner, run_id)["items"]:
            fail("a baseline run has findings")
    print(
        f"verified {checked} findings across {len(SCENARIOS)} scenarios; viewer sees identical bytes"
    )


def cmd_snapshot(path: str) -> None:
    owner = reader("owner")
    items = {r: findings(owner, r)["items"] for r in all_run_ids()}
    Path(path).write_text(json.dumps({"items": items, "computed": computed(owner)}))
    print(f"snapshot of {sum(len(v) for v in items.values())} findings")


def cmd_compare(path: str) -> None:
    before = json.loads(Path(path).read_text())["items"]
    owner = reader("owner")
    n = 0
    for run_id in all_run_ids():
        now = findings(owner, run_id)["items"]
        keyed = lambda xs: sorted(  # noqa: E731
            (
                (
                    f["detector"],
                    f["finding_type"],
                    f["fingerprint"],
                    f["id"],
                    f["created_at"],
                    json.dumps(f["evidence"], sort_keys=True),
                )
                for f in xs
            )
        )
        if keyed(now) != keyed(before[run_id]):
            fail(f"a rebuild changed the findings of {run_id[:12]}")
        n += len(now)
    print(
        f"a rebuild reproduced {n} findings with the same ids, fingerprints, evidence and created_at"
    )


def main() -> int:
    cmd, *rest = sys.argv[1:] or ["?"]
    if cmd == "runs":
        cmd_runs()
    elif cmd == "wait":
        cmd_wait(rest[rest.index("--after") + 1] if "--after" in rest else None)
    elif cmd == "verify":
        cmd_verify()
    elif cmd == "snapshot":
        cmd_snapshot(rest[0])
    elif cmd == "compare":
        cmd_compare(rest[0])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

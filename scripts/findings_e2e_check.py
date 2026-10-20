"""API-side assertions of scripts/findings-e2e.sh (Phase 10). Run with `uv run --project apps/api python`.

    runs                      print {"<scenario>": "<run id>"} for the committed pathological fixtures
    wait [--after FILE]       wait (bounded) until every run's findings are `current` (and, with --after, were
                              recomputed after the snapshot in FILE)
    verify                    each scenario yields exactly its hand-written expected findings, every cited event
                              resolves, titles say "Probable:" iff probable, a VIEWER sees the same bytes as the OWNER
    snapshot FILE             store (id, created_at, fingerprint, evidence ...) of every finding and each run's computed_at
    compare FILE              the current findings equal the snapshot (a rebuild reproduces them, ids and created_at too)
    live start|mid|end|verify FILE
                              KI-097 / ADR-174, one live run through a worker whose minimum analysis interval is 600 s
                              (so nothing here depends on a wall clock): `start` posts the first events and waits for
                              the first analysis; `mid` posts more and asserts the earlier analysis is read, labelled
                              stale with `as_of` = its `computed_at`; `end` posts the terminal events and waits for the
                              final analysis (no longer stale); `verify` (after `rebuild-findings`) asserts the findings
                              equal a from-scratch rebuild (INV-2) and that no event row changed (INV-1)

Environment: E2E_API, E2E_WORKSPACE (slug), E2E_READ_KEY (runs:read), E2E_OWNER_SESSION, E2E_VIEWER_SESSION and
E2E_FIXTURES (the directory of `*.ndjson` / `*.expected.json`). Nothing here prints a credential or event content.
"""

import hashlib
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


# ----------------------------------------------------------------------------- live run (KI-097)

LIVE_STEPS = (
    (1, 69),
    (70, 87),
    (88, 89),
)  # events by sequence: through the reported loop, more, the end
POLL_SECONDS, POLL_STEP = 30.0, 0.2
ID = re.compile(r"\b(?:evt|run|trc|spn)_[0-9A-Z]{26}\b")


def poll(what: str, ready: Any) -> Any:
    """The only waiting in this stage: a bounded poll of an API-visible condition (never a fixed sleep)."""
    deadline = time.monotonic() + POLL_SECONDS
    while True:
        got = ready()
        if got:
            return got
        if time.monotonic() > deadline:
            fail(f"live: timed out after {POLL_SECONDS:.0f} s waiting for {what}")
        time.sleep(POLL_STEP)


def live_wires() -> list[dict[str, Any]]:
    """The committed `looping` run under fresh, deterministic ids, so it cannot collide with the shuffled one."""
    from abb_event_schema.ids import IdGenerator, IdKind

    text = (FIXTURES / "looping.ndjson").read_text()
    kinds = {"evt": IdKind.EVENT, "run": IdKind.RUN, "trc": IdKind.TRACE, "spn": IdKind.SPAN}
    mapping: dict[str, str] = {}
    for old in sorted(set(ID.findall(text))):
        digest = hashlib.sha256(f"live:{old}".encode()).digest()
        gen = IdGenerator(clock_ms=lambda: 1_760_000_000_000, entropy=lambda n, d=digest: d[:n])
        mapping[old] = gen.new_id(kinds[old.split("_")[0]])
    return [json.loads(ID.sub(lambda m: mapping[m.group(0)], line)) for line in text.splitlines()]


def live_post(wires: list[dict[str, Any]]) -> None:
    key = Path(os.environ["E2E_WRITE_KEY_FILE"]).read_text().strip()
    response = httpx.post(
        f"{API}/v1/events/batch",
        json={"events": wires},
        headers={"Authorization": f"Bearer {key}"},
        timeout=60,
    )
    if response.status_code >= 300:
        fail(f"live: ingest answered {response.status_code}")


def live_slice(wires: list[dict[str, Any]], step: int) -> list[dict[str, Any]]:
    lo, hi = LIVE_STEPS[step]
    return [w for w in wires if lo <= w["sequence"] <= hi]


def live_dims(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {d["name"]: d for d in body["health"]["dimensions"]}


def event_hash(run_id: str) -> str:
    """One digest over the run's event rows as stored (INV-1: nothing may change them)."""
    import asyncio

    import asyncpg
    from abb_event_schema.ids import to_uuid

    async def go() -> str:
        url = os.environ["ABB_DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(url)
        try:
            value = await conn.fetchval(
                "select md5(string_agg(e::text, '' order by e.event_id)) from events e where e.run_id = $1",
                to_uuid(run_id),
            )
        finally:
            await conn.close()
        return str(value)

    return asyncio.run(go())


def keyed_items(items: list[dict[str, Any]]) -> list[tuple[str, ...]]:
    return sorted(
        (
            f["detector"],
            f["finding_type"],
            f["fingerprint"],
            f["id"],
            f["created_at"],
            json.dumps(f["evidence"], sort_keys=True),
        )
        for f in items
    )


def cmd_live(step: str, path: str) -> None:
    wires = live_wires()
    run_id = wires[0]["run_id"]
    client = reader("key")
    state_file = Path(path)
    saved: dict[str, Any] = json.loads(state_file.read_text()) if state_file.exists() else {}
    if step == "start":
        live_post(live_slice(wires, 0))
        body = poll(
            "the first analysis",
            lambda: (lambda b: b if b["state"]["computed_at"] else None)(findings(client, run_id)),
        )
        saved = {"first": body["state"]["computed_at"]}
        print("live: first analysis exists (the run is still active)")
    elif step == "mid":
        live_post(live_slice(wires, 1))
        body = poll(
            "the newer analysis to be queued",
            lambda: (lambda b: b if b["state"]["status"] == "processing" else None)(
                findings(client, run_id)
            ),
        )
        dims = live_dims(body)
        for name in ("loops", "context"):
            d = dims[name]
            if d["status"] == "no_data":
                fail(f"live: {name} reads no_data while an earlier analysis exists")
            if d["stale"] is not True or d["as_of"] != saved["first"]:
                fail(f"live: {name} is not labelled with the earlier analysis (stale/as_of)")
        if body["state"]["computed_at"] != saved["first"]:
            fail("live: the interval floor did not hold (a second analysis ran)")
        if dims["reliability"]["stale"] or dims["reliability"]["as_of"] is not None:
            fail("live: reliability does not read findings and must not carry an age")
        print(
            f"live: loops={dims['loops']['status']} stale as of the first analysis; no second analysis"
        )
    elif step == "end":
        live_post(live_slice(wires, 2))

        def finished() -> dict[str, Any] | None:
            b = findings(client, run_id)
            s = b["state"]
            if s["status"] == "current" and s["computed_at"] > saved["first"]:
                return b
            return None

        body = poll("the final analysis after the run ended", finished)
        if body["state"]["event_count_seen"] != len(wires):
            fail("live: the final analysis did not read every event")
        dims = live_dims(body)
        if any(d["stale"] for d in dims.values()):
            fail("live: a dimension is still stale after the final analysis")
        if (
            dims["loops"]["status"] == "no_data"
            or dims["loops"]["as_of"] != body["state"]["computed_at"]
        ):
            fail("live: loops is not read from the final analysis")
        saved.update(
            final=body["state"]["computed_at"],
            items=body["items"],
            events=event_hash(run_id),
        )
        print(
            f"live: the final analysis ran without waiting out the interval ({len(body['items'])} findings)"
        )
    elif step == "verify":
        body = poll(
            "the rebuilt analysis",
            lambda: (
                lambda b: (
                    b
                    if b["state"]["status"] == "current"
                    and b["state"]["computed_at"] > saved["final"]
                    else None
                )
            )(findings(client, run_id)),
        )
        if keyed_items(body["items"]) != keyed_items(saved["items"]):
            fail("live: a from-scratch rebuild changed the findings (INV-2)")
        if event_hash(run_id) != saved["events"]:
            fail("live: event rows changed during the stage (INV-1)")
        print(
            f"live: a from-scratch rebuild reproduced {len(body['items'])} findings; event rows unchanged"
        )
    else:
        fail(f"live: unknown step {step}")
    state_file.write_text(json.dumps(saved))


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
    elif cmd == "live":
        cmd_live(rest[0], rest[1])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

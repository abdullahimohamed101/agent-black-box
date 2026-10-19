#!/usr/bin/env python3
"""Posts a run into the hosted stack through the public edge, for scripts/hosting-e2e.sh (Phase 23).

    hosting_e2e_seed.py BASE_URL KEY_FILE [--cacert F] [--resolve IP] [--runs N] [--events N] [--leave-open]

Prints one JSON line: {"run_id": "...", "accepted": N}. The last run stays open (no run.completed) with --leave-open, so its
event stream stays live for the verifier. The key is read from a file and never printed. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hosting_verify_lib as lib  # noqa: E402

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid() -> str:
    value = ((time.time_ns() // 1_000_000) << 80) | int.from_bytes(secrets.token_bytes(10), "big")
    return "".join(ALPHABET[(value >> (5 * i)) & 31] for i in range(25, -1, -1))


def build_run(index: int, count: int, close: bool) -> tuple:
    run, trace = f"run_{ulid()}", f"trc_{ulid()}"
    clock = datetime.now(timezone.utc)
    events: list = []

    def add(kind: str, **extra: object) -> None:
        n = len(events) + 1
        at = (clock + timedelta(milliseconds=n)).isoformat().replace("+00:00", "Z")
        events.append(
            {
                "schema_version": "1.0", "event_id": f"evt_{ulid()}", "run_id": run, "trace_id": trace,
                "agent_id": "rig-agent", "event_type": kind, "sequence": n, "occurred_at": at,
                "attributes": extra.pop("attributes", {}), **extra,
            }
        )  # fmt: skip

    add("run.started", attributes={"run.name": f"rig run {index}"})
    for i in range(count):
        add("tool.call.completed", attributes={"tool.name": f"step_{i}"}, status="success", span_id=f"spn_{ulid()}")
    if close:
        add("run.completed", status="success")
    return run, events


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("base_url")
    p.add_argument("key_file")
    p.add_argument("--cacert")
    p.add_argument("--resolve")
    p.add_argument("--runs", type=int, default=1)
    p.add_argument("--events", type=int, default=20)
    p.add_argument("--leave-open", action="store_true")
    p.add_argument("--browse-as", help="then sign in as this invited person and load the dashboard pages (needs --workspace)")
    p.add_argument("--workspace")
    p.add_argument("--project", default="demo")
    p.add_argument("--rounds", type=int, default=3)
    args = p.parse_args()
    target = lib.Target(args.base_url, args.cacert, args.resolve)
    key = open(args.key_file, encoding="utf-8").read().strip()
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    accepted, run = 0, ""
    for index in range(args.runs):
        run, events = build_run(index, args.events, close=not (args.leave_open and index == args.runs - 1))
        r = target.request("POST", "/v1/events/batch", headers, json.dumps({"events": events}).encode())
        body = json.loads(r.body or b"{}")
        if r.status != 202 or body.get("rejected"):
            print(json.dumps({"error": f"ingest answered {r.status}", "rejected": body.get("rejected")}))
            return 1
        accepted += body.get("accepted", 0)
    if args.browse_as:
        failed = browse(target, args)
        if failed:
            print(json.dumps({"error": "pages that did not answer 200", "pages": failed}))
            return 1
    print(json.dumps({"run_id": run, "accepted": accepted}))
    return 0


def browse(target: "lib.Target", args: argparse.Namespace) -> list:
    """Smoke load: sign in through the fake provider and render the main pages a few times (server-rendered, per-request nonce)."""
    done, note = lib._sign_in(target, args.browse_as)
    session = lib._cookies(done).get("__Host-abb_session") if done else None
    if not session:
        return [f"sign-in: {note or 'no session issued'}"]
    cookie = {"Cookie": "__Host-abb_session=" + session[0]}
    ws, prj = args.workspace, args.project
    me = json.loads(target.request("GET", "/api/abb/v1/me", cookie).body or b"{}")
    ws_id = next((m["workspace"]["id"] for m in me.get("memberships", []) if m.get("workspace", {}).get("slug") == ws), "")
    if not ws_id:
        return ["the signed-in person has no membership in the workspace"]
    pages = [f"/w/{ws}/projects/{prj}/runs", f"/w/{ws}/projects/{prj}/analytics", f"/w/{ws}/settings/members",
             f"/w/{ws}/settings/sessions", "/api/abb/v1/me", "/api/abb/v1/runs", "/api/abb/v1/analytics/summary"]  # fmt: skip
    failed = []
    for _ in range(args.rounds):
        for page in pages:
            # The relay's workspace-scoped reads need the workspace the page is showing.
            r = target.request("GET", page, dict(cookie, Accept="text/html,application/json", **{"X-ABB-Workspace": ws_id}))
            if r.status != 200:
                failed.append(f"{page.split('?')[0]} -> {r.status}")
    return sorted(set(failed))


if __name__ == "__main__":
    sys.exit(main())

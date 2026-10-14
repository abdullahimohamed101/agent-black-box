"""API-side half of scripts/policy-e2e.sh (Phase 14). Run with `uv run --project apps/api python`.

Subcommands, in the order the script calls them (each prints one JSON evidence line and exits 1 on a failed assertion):

    provision           keys (created by the OWNER through the management API), project ids
    publish-starter     publishes examples/coding-agent/policies/starter.yaml as the owner and asserts version 1
    gate1-start V       V = approve | deny | expiry: runs `run_demo.py --policy-demo` in the background until it asks for approval
    gate1-finish V      the demo's outcome, the bare remote, the recorded events, the approval's final state
    gate2               a replayed approval is rejected in every way
    gate5               atomic state machine: concurrent deciders, consumes, races; illegal SQL as the runtime role
    gate6-prepare       requests for the browser's authorization checks; the API-side authorization matrix
    gate6-verify        what the browser's decisions left behind (audit rows, events, redaction)
    gate7               a SIGINT during an approval wait cancels the approval; bounded waits; standard library only
    gate8               dry run against an independent evaluation; decision and ingestion latency
    ui-prepare          data for the real-stack UI checks
    scan                no planted secret, key token or intent value in any column, artifact file or log

Environment (set by the script): E2E_* (see policy-e2e.sh) and ABB_DATABASE_URL. Secrets are never printed.
"""

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from policy_e2e_lib import (
    API,
    FAIL_API,
    PROJECTS,
    ROOT,
    SLUG,
    WORK,
    agent,
    clean_env,
    emit,
    expect,
    fail,
    ids,
    person,
    run_events,
    run_status,
    save,
    spawn,
    state,
    stop_group,
    wait_until,
)


RACES_POLICY = """\
version: 1
policies:
  - id: gate-push
    description: Pushing to main needs a person.
    when: {capability: github.push, resource: "origin main"}
    decision: require_approval
    approver_role: admin
    expires_in: 15m
  - id: gate-short
    description: A short-lived approval.
    when: {capability: test.short}
    decision: require_approval
    approver_role: admin
    expires_in: 1m
  - id: gate-shell
    description: Risky shell commands need a person.
    when: {capability: shell.execute, risk_class: R3}
    decision: require_approval
    approver_role: admin
    expires_in: 15m
  - id: gate-self
    description: A person may approve their own agent here.
    when: {capability: notes.write}
    decision: require_approval
    allow_self_approval: true
    expires_in: 15m
  - id: gate-dev
    description: A developer may approve this.
    when: {capability: lint.fix}
    decision: require_approval
    approver_role: developer
    expires_in: 15m
  - id: gate-owner
    description: Only an owner may approve this.
    when: {capability: billing.change}
    decision: require_approval
    approver_role: owner
    expires_in: 15m
  - id: gate-secret
    description: Connecting to a database needs a person.
    when: {capability: db.connect}
    decision: require_approval
    approver_role: admin
    expires_in: 15m
"""

STARTER = ROOT / "examples" / "coding-agent" / "policies" / "starter.yaml"


# ------------------------------------------------------------------------------------------- provisioning


def create_key(client: Any, name: str, scopes: list[str], project: str | None) -> str:
    body: dict[str, Any] = {"name": name, "scopes": scopes}
    if project:
        body["project_id"] = project
    response = client.post("/v1/api-keys", json=body)
    expect(response.status_code == 201, f"create key {name} -> {response.status_code}")
    token = response.json()["token"]
    expect(token.startswith("abb_live_"), "key shape")
    return str(token)


def provision() -> None:
    owner = person("owner")
    listed = owner.get("/v1/projects")
    expect(listed.status_code == 200, "list projects")
    by_slug = {p["slug"]: p["id"] for p in listed.json()["items"]}
    expect(all(slug in by_slug for slug in PROJECTS), f"projects missing: {sorted(by_slug)}")
    keys: dict[str, str] = {}
    # The agent keys are created BY THE OWNER, so the self-approval rule applies to the owner (ADR-141 point 7).
    for slug in PROJECTS:
        keys[slug] = create_key(owner, f"agent-{slug}", ["events:write", "artifacts:write"], by_slug[slug])
    keys["races_b"] = create_key(owner, "agent-races-b", ["events:write"], by_slug["races"])  # a second key of one project
    keys["check_only"] = create_key(owner, "agent-check-only", ["policy:check"], by_slug["races"])
    keys["wide"] = create_key(owner, "agent-workspace-wide", ["policy:check"], None)  # PROJECT_KEY_REQUIRED
    keys["reader"] = create_key(owner, "script-read", ["runs:read"], None)
    # A key created by the DEVELOPER: the owner and the admin can both decide its requests (the race tests need two deciders).
    keys["races_dev"] = create_key(person("developer"), "agent-races-by-developer", ["events:write"], by_slug["races"])
    foreign = person("other_admin")
    foreign_projects = {p["slug"]: p["id"] for p in foreign.get("/v1/projects").json()["items"]}
    keys["foreign"] = create_key(foreign, "agent-foreign", ["events:write", "artifacts:write"], foreign_projects["foreign"])
    save(projects=by_slug, foreign_project=foreign_projects["foreign"], keys=keys)
    emit(provisioned={"projects": len(by_slug), "keys": len(keys)}, created_by_owner=True)


def publish(project: str | None, document: str, comment: str, role: str = "owner", base: int | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"document": document, "comment": comment}
    if project:
        body["project_id"] = ids()[project]
    if base is not None:
        body["base_version"] = base
    response = person(role).post("/v1/policies/publish", json=body)
    expect(response.status_code == 201, f"publish {project} -> {response.status_code} {response.text[:200]}")
    return dict(response.json())


def publish_starter() -> None:
    document = STARTER.read_text()
    valid = person("owner").post("/v1/policies/validate", json={"document": document}).json()
    expect(valid["valid"] and valid["rule_count"] == 5, f"starter policy invalid: {valid}")
    version = publish("full", document, "starter policy (policy-e2e)")
    expect(version["version"] == 1, f"starter policy is version {version['version']}, expected 1")
    # The expiry variant: the same policy, but a push to main may wait only one minute (the shortest expiry allowed).
    variant = document.replace("expires_in: 15m", "expires_in: 1m")
    expect(variant != document, "expiry variant")
    expiry = publish("expiry", variant, "starter with a one-minute expiry (policy-e2e)")
    expect(expiry["version"] == 1, "expiry variant version")
    for project in ("races", "ui"):
        publish(project, RACES_POLICY, f"gates for {project} (policy-e2e)")
    audit = person("owner").get("/v1/audit", params={"limit": 50}).json()["items"]
    published = [a for a in audit if a["action"] == "policy.publish"]
    expect(len(published) >= 4, f"policy.publish audit rows: {len(published)}")
    emit(starter_version=version["version"], rules=version["rule_count"], audit_rows=len(published))


# ------------------------------------------------------------------------------------------------- gate 1


def demo_env(key_name: str) -> dict[str, str]:
    return clean_env(BLACKBOX_API_KEY=state()["keys"][key_name], BLACKBOX_ENDPOINT=API, BLACKBOX_POLICY="enforce")


def bare_head(remote: Path) -> str | None:
    out = subprocess.run(["git", "--git-dir", str(remote), "rev-parse", "--verify", "-q", "refs/heads/main"], capture_output=True, text=True)
    return out.stdout.strip() or None


def pending_line(log: Path) -> dict[str, Any] | None:
    if not log.exists():
        return None
    for line in log.read_text(errors="replace").splitlines():
        if line.startswith('{"event": "approval_pending"'):
            return dict(json.loads(line))
    return None


def demo_summary(log: Path) -> dict[str, Any] | None:
    for line in reversed(log.read_text(errors="replace").splitlines()):
        if line.startswith('{"run_id"'):
            return dict(json.loads(line))
    return None


def gate1_start(variant: str) -> None:
    project = "expiry" if variant == "expiry" else "full"
    workdir = WORK / f"demo-{variant}"
    log = WORK / f"demo-{variant}.log"
    argv = ["uv", "run", "--project", "packages/sdk-python", "python", "examples/coding-agent/run_demo.py", "--policy-demo", "--workdir", str(workdir)]
    proc = spawn(argv, demo_env(project), log)
    started = time.monotonic()
    save(**{f"demo_{variant}": {"pid": proc.pid, "workdir": str(workdir), "started": time.time()}})
    pending = wait_until(f"the {variant} demo to ask for approval", lambda: pending_line(log) or (proc.poll() is not None and {"exited": proc.returncode}), 90)
    expect("approval_id" in pending, f"the {variant} demo exited ({pending}) before asking for approval: {log.read_text(errors='replace')[-600:]}")
    approval_id = pending["approval_id"]
    remote = workdir / "origin.git"
    expect(remote.exists() and bare_head(remote) is None, "the bare remote must have no commit on main while the push is paused")
    detail = wait_until("the approval in the list", lambda: next((a for a in person("admin").get("/v1/approvals", params={"state": "pending", "limit": 100}).json()["items"] if a["id"] == approval_id), None), 20)
    run_id = detail["run_id"]
    expect(detail["capability"] == "github.push" and detail["policy_id"] == "approve-push-main", f"unexpected request: {detail['capability']} {detail['policy_id']}")
    expect(detail["policy_version"] == 1, "policy version 1")
    waiting = wait_until("the run to be WAITING_FOR_APPROVAL", lambda: run_status(run_id) == "WAITING_FOR_APPROVAL", 40)
    expect(waiting, "run state")
    time.sleep(1.5)
    expect(bare_head(remote) is None, "the push happened while the approval was pending")
    expect(proc.poll() is None, "the demo must still be waiting")
    save(**{f"demo_{variant}": {"pid": proc.pid, "workdir": str(workdir), "started": time.time() - (time.monotonic() - started), "approval_id": approval_id, "run_id": run_id}})
    save(**{f"gate1_{variant}_approval": approval_id, f"gate1_{variant}_run": run_id})
    emit(gate=1, variant=variant, phase="paused", approval=approval_id, run=run_id, run_status="WAITING_FOR_APPROVAL", remote_has_commit=False,
         seconds_to_pending=round(time.monotonic() - started, 1), expires_in_seconds=pending["expires_in_seconds"])


PROCS: dict[str, Any] = {}


def gate1_finish(variant: str) -> None:
    info = state()[f"demo_{variant}"]
    log = WORK / f"demo-{variant}.log"
    remote = Path(info["workdir"]) / "origin.git"
    run_id = info["run_id"]

    def finished() -> dict[str, Any] | None:
        return demo_summary(log)

    summary = wait_until(f"the {variant} demo to finish", finished, 200)
    expect(summary["run_id"] == run_id, "the summary's run id")
    expect(summary["blocked"][0] == "read_file", f"the .env read must be blocked first: {summary['blocked']}")
    expect(summary["flushed"] is True, "the SDK flushed its events")
    text = log.read_text(errors="replace")
    expect("Traceback" not in text, "the demo crashed: " + text[-400:])
    # the run settles; read every event
    wait_until("the run to settle", lambda: run_status(run_id) in ("SUCCESS", "FAILED"), 60)
    events = run_events(run_id)
    types = [e["event_type"] for e in events]
    blocked = [e for e in events if e["event_type"] == "policy.action.blocked"]
    expect(any(e["attributes"].get("policy.id") == "deny-env-read" and e["attributes"].get("policy.version") == 1 for e in blocked), "policy.action.blocked for deny-env-read v1")
    env_reads = [e for e in events if e["event_type"] == "file.read" and str(e["attributes"].get("file.path", "")).endswith(".env")]
    expect(not env_reads, "the .env file was recorded as read")
    expect(not any(".env" in json.dumps(e["attributes"]) and e["event_type"] == "file.read" for e in events), ".env in a read event")
    sdk_names = {e["event_type"]: e.get("sdk", {}).get("name") for e in events if e["event_type"].startswith(("policy.", "approval."))}
    emitters = {t: n for t, n in sdk_names.items() if t != "policy.warning"}
    expect(all(n == "abb-policy-service" for n in emitters.values()), f"server-written events carry the policy service emitter: {emitters}")
    approval = person("admin").get(f"/v1/approvals/{info['approval_id']}").json()
    final: dict[str, Any] = {"gate": 1, "variant": variant, "run": run_id, "approval_state": approval["state"], "blocked_tools": summary["blocked"], "pushed": summary["pushed"]}
    head = bare_head(remote)
    if variant == "approve":
        expect(summary["pushed"] is True and "git_push" not in summary["blocked"], f"the approved push must happen: {summary}")
        expect(head is not None, "the commit must reach the bare remote")
        message = subprocess.run(["git", "--git-dir", str(remote), "log", "-1", "--format=%s", "main"], capture_output=True, text=True).stdout.strip()
        expect(message == "Refresh sessions before expiry", f"the pushed commit: {message}")
        expect(approval["state"] == "APPROVED" and approval["consumed"] is True, f"approval {approval['state']} consumed={approval['consumed']}")
        for needed in ("approval.requested", "approval.granted", "approval.consumed"):
            expect(needed in types, f"{needed} missing from the run")
        expect("approval.denied" not in types and "approval.expired" not in types, "unexpected approval end")
        final.update(commit=message, events={t: types.count(t) for t in sorted(set(types)) if t.startswith(("policy.", "approval."))})
    else:
        expect(summary["pushed"] is False and "git_push" in summary["blocked"], f"the push must not happen: {summary}")
        expect(head is None, "no commit may reach the bare remote")
        want = "DENIED" if variant == "deny" else "EXPIRED"
        expect(approval["state"] == want and approval["consumed"] is False, f"approval {approval['state']}")
        expect(("approval.denied" if variant == "deny" else "approval.expired") in types, "the end of the request is a typed event")
        expect("approval.consumed" not in types and "approval.granted" not in types, "an approval was used")
        final.update(events={t: types.count(t) for t in sorted(set(types)) if t.startswith(("policy.", "approval."))})
        if variant == "expiry":
            started = float(info["started"])
            final["seconds_from_ask_to_end"] = round(time.time() - started, 1)
    final["run_status"] = run_status(run_id)
    # a denial or an expiry is a policy outcome: the run itself completes
    expect(final["run_status"] == "SUCCESS", f"the run status is {final['run_status']}")
    emit(**final)


# -------------------------------------------------------------------------------------------------- scan


def scan(label: str) -> None:
    from security_e2e_check import scan_database, scan_files  # noqa: PLC0415  (shares the database scanner of Phase 13)

    keys = list(state().get("keys", {}).values())
    secrets = [os.environ["E2E_AWS"], os.environ["E2E_PASSWORD"], *keys, *state().get("scan_extra", [])]
    hits, columns, rows = asyncio.run(scan_database(secrets))
    control, _, _ = asyncio.run(scan_database([os.environ["E2E_PLANT"]]))
    if state().get("planted"):
        expect(any(c.startswith(("events.", "approvals.")) for c in control[os.environ["E2E_PLANT"]]), "positive control: the scan did not find the plant tag at all")
    for needle, places in hits.items():
        expect(not places, f"a planted secret, key token or value is stored in {sorted(set(places))}")
    file_hits, files, size = scan_files(secrets, Path(os.environ["E2E_ARTIFACT_DIR"]))
    for needle, places in file_hits.items():
        expect(not places, f"a planted secret is on disk in {places}")
    log_hits = 0
    for log in filter(None, os.environ.get("E2E_LOGS", "").split(":")):
        data = Path(log).read_bytes() if Path(log).exists() else b""
        log_hits += sum(n.encode() in data for n in secrets)
    for needle in state().get("log_needles", []):
        log_hits += sum(needle.encode() in (Path(log).read_bytes() if Path(log).exists() else b"") for log in filter(None, os.environ.get("E2E_LOGS", "").split(":")))
    expect(log_hits == 0, "a planted secret, key token or intent value is in a server or demo log")
    emit(scan=label, needles=len(secrets), columns_scanned=columns, rows_in_tables=rows, artifact_files=files, artifact_bytes=size, found=0)


COMMANDS: dict[str, Callable[[], None]] = {}


def main() -> None:
    args = sys.argv[1:]
    commands: dict[str, Callable[[], None]] = {
        "provision": provision,
        "publish-starter": publish_starter,
        "gate1-start": lambda: gate1_start(args[1]),
        "gate1-finish": lambda: gate1_finish(args[1]),
        "scan": lambda: scan(args[1] if len(args) > 1 else "final"),
    }
    from policy_e2e_gates import GATES  # noqa: PLC0415

    commands.update(GATES)
    if not args or args[0] not in commands:
        fail(f"usage: {sys.argv[0]} {{{'|'.join(commands)}}}")
    commands[args[0]]()


if __name__ == "__main__":
    main()

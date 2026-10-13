"""Shared helpers of scripts/policy-e2e.sh (Phase 14): environment, state file, HTTP clients, waiting and process spawning.

Imported by `policy_e2e_check.py` (runs in the API's environment) and, for the parts that need only the standard library,
mirrored in `policy_e2e_sdk.py` (runs in the SDK's environment, which has no httpx). Secrets are never printed: failures
name the check and the endpoint, not a value.
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

API = os.environ.get("E2E_API", "http://localhost:8200")
FAIL_API = os.environ.get("E2E_FAIL_API", "http://localhost:8202")
SLUG = os.environ.get("E2E_WORKSPACE", "")
SLUG2 = os.environ.get("E2E_WORKSPACE2", "")
ORIGIN = os.environ.get("E2E_ORIGIN", "http://localhost:3200")
STATE_FILE = Path(os.environ.get("E2E_STATE", "/dev/null"))
WORK = Path(os.environ.get("E2E_WORK", "/tmp"))
ROOT = Path(os.environ.get("E2E_ROOT", "."))
SESSIONS = {
    "owner": "E2E_OWNER_SESSION",
    "admin": "E2E_ADMIN_SESSION",
    "developer": "E2E_DEVELOPER_SESSION",
    "viewer": "E2E_VIEWER_SESSION",
    "billing": "E2E_BILLING_SESSION",
    "other_admin": "E2E_OTHER_ADMIN_SESSION",
}
PROJECTS = ("full", "expiry", "failmodes", "races", "ui", "bench", "other")


def fail(message: str) -> None:
    print(f"POLICY-E2E CHECK FAILED: {message}", file=sys.stderr)
    raise SystemExit(1)


def expect(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def emit(**evidence: Any) -> None:
    print(json.dumps(evidence, sort_keys=True, default=str))


def state() -> dict[str, Any]:
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.stat().st_size else {}


def save(**values: Any) -> None:
    current = state()
    current.update(values)
    STATE_FILE.write_text(json.dumps(current))


# ------------------------------------------------------------------------------------------------ clients

_clients: dict[str, httpx.Client] = {}


def person(role: str) -> httpx.Client:
    """A signed-in person (a development session) with the workspace chosen. `other_admin` lives in the second workspace."""
    if role in _clients:
        return _clients[role]
    client = httpx.Client(
        base_url=API,
        cookies={"abb_session": os.environ[SESSIONS[role]]},
        headers={"Origin": ORIGIN},
        timeout=60,
    )
    me = client.get("/v1/me")
    me.raise_for_status()
    wanted = SLUG2 if role == "other_admin" else SLUG
    for membership in me.json()["memberships"]:
        if membership["workspace"]["slug"] == wanted:
            client.headers["x-abb-workspace"] = membership["workspace"]["id"]
            _clients[role] = client
            return client
    fail(f"{role} is not a member of {wanted}")
    raise AssertionError


def agent(name: str, base: str = API) -> httpx.Client:
    """A client that authenticates with a key stored by `provision` (never printed)."""
    return httpx.Client(base_url=base, headers={"Authorization": f"Bearer {state()['keys'][name]}"}, timeout=60)


def ids() -> dict[str, str]:
    return state()["projects"]


# ------------------------------------------------------------------------------------------------ waiting


def wait_until(what: str, probe: Any, timeout: float = 90.0, every: float = 0.5) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = probe()
        if found:
            return found
        time.sleep(every)
    fail(f"timed out waiting for {what}")


def run_events(run_id: str, role: str = "owner") -> list[dict[str, Any]]:
    response = person(role).get(f"/v1/runs/{run_id}/events", params={"limit": 500})
    expect(response.status_code == 200, f"GET run events -> {response.status_code}")
    return response.json()["items"]


def run_status(run_id: str, role: str = "owner") -> str:
    response = person(role).get(f"/v1/runs/{run_id}")
    expect(response.status_code == 200, f"GET run -> {response.status_code}")
    return str(response.json()["status"])


# --------------------------------------------------------------------------------------- process spawning


def record_group(pgid: int) -> None:
    """So the shell's cleanup can end a process group this run started, whatever happens to the checks."""
    with (WORK / "spawned.pids").open("a") as handle:
        handle.write(f"{pgid}\n")


def spawn(argv: list[str], env: dict[str, str], log: Path, cwd: Path | None = None) -> subprocess.Popen[bytes]:
    """Start a process in a group of its own (so it can be stopped by that group, never by name)."""
    handle = log.open("ab")
    # A script run in the background (CI, `&`) has SIGINT ignored, and Python only installs its KeyboardInterrupt handler when it is not:
    # the child gets the default back, so a Ctrl-C (SIGINT to the group) means what it means in a terminal.
    proc = subprocess.Popen(  # noqa: S603  (argv is fixed by this script)
        argv, cwd=cwd or ROOT, env=env, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True,
        preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL),  # noqa: PLW1509
    )
    record_group(proc.pid)
    return proc


def stop_group(proc: subprocess.Popen[bytes], sig: int = signal.SIGTERM, wait: float = 10.0) -> None:
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=wait)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)


def clean_env(**extra: str) -> dict[str, str]:
    """The environment of an SDK process: no secrets of this script, only what the SDK needs."""
    env = {"HOME": os.environ["HOME"], "PATH": os.environ["PATH"], "LANG": "C.UTF-8"}
    env.update(extra)
    return env


# ------------------------------------------------------------------------------------------- the API's verbs

from abb_event_schema.ids import IdKind, new_id  # noqa: E402  (after the helpers that do not need it)
from abb_event_schema.intent import intent_hash  # noqa: E402


def new_run() -> str:
    return str(new_id(IdKind.RUN))


def intent(run: str | None = None, **overrides: Any) -> dict[str, Any]:
    """The gated push of the `races` and `ui` policies (override any field)."""
    body: dict[str, Any] = {
        "run_id": run or new_run(),
        "agent_id": "coding-agent",
        "capability": "github.push",
        "resource": "origin main",
        "risk_class": "R3",
        "arguments": {"remote": "origin", "force": False},
    }
    body.update(overrides)
    return body


def ask(client: httpx.Client, body: dict[str, Any]) -> httpx.Response:
    return client.post("/v1/policy/decide", json=body)


def ask_ok(client: httpx.Client, body: dict[str, Any]) -> str:
    """Ask for an action that needs a person; returns the approval id."""
    response = ask(client, body)
    expect(response.status_code == 200, f"decide -> {response.status_code} {response.text[:200]}")
    verdict = response.json()
    expect(verdict["decision"] == "require_approval" and verdict["approval_id"], f"expected an approval request, got {verdict['decision']}")
    return str(verdict["approval_id"])


def decide(role: str, approval: str, decision: str, comment: str | None = None) -> httpx.Response:
    body: dict[str, Any] = {"decision": decision}
    if comment:
        body["comment"] = comment
    return person(role).post(f"/v1/approvals/{approval}/decision", json=body)


def consume(client: httpx.Client, approval: str, body: dict[str, Any]) -> httpx.Response:
    return client.post(f"/v1/approvals/{approval}/consume", json={"intent": body})


def error_code(response: httpx.Response) -> str | None:
    try:
        return str(response.json()["error"]["code"])
    except Exception:
        return None


def detail(approval: str, role: str = "admin") -> dict[str, Any]:
    response = person(role).get(f"/v1/approvals/{approval}")
    expect(response.status_code == 200, f"GET approval -> {response.status_code}")
    return dict(response.json())


def hash_of(body: dict[str, Any], project: str = "races") -> str:
    """The intent hash as the server computes it (workspace and project from the key). Never printed or sent."""
    me = person("owner").get("/v1/me").json()
    workspace = next(m["workspace"]["id"] for m in me["memberships"] if m["workspace"]["slug"] == SLUG)
    return str(
        intent_hash(
            workspace_id=workspace, project_id=ids()[project], run_id=body["run_id"], agent_id=body["agent_id"],
            capability=body["capability"], resource=body.get("resource", ""), risk_class=body.get("risk_class"),
            arguments=body.get("arguments", {}),
        )
    )


def log_text() -> str:
    """Every server and demo log of this run (for leak checks); the shell exports their paths."""
    parts = []
    for log in filter(None, os.environ.get("E2E_LOGS", "").split(":")):
        path = Path(log)
        if path.exists():
            parts.append(path.read_text(errors="replace"))
    return "\n".join(parts)


def add_log_needles(*needles: str) -> None:
    """Values that must never appear in a log or an agent-visible response (they may be stored: approvals keep scrubbed content)."""
    save(log_needles=sorted(set(state().get("log_needles", [])) | set(needles)))


from abb_event_schema.ids import to_uuid  # noqa: E402,F401  (re-exported for the gates)

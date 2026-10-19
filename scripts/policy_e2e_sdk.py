"""SDK-side half of scripts/policy-e2e.sh (Phase 14, gate 4 and the SDK part of gate 7). Run with
`uv run --project packages/sdk-python python scripts/policy_e2e_sdk.py gate4`.

It uses the REAL Python SDK against the REAL API, then makes the API unreachable by stopping the process group THIS script
started (never by name), and checks what `guard()` does:

  * the service answers: allow, deny; the fail-mode table is cached after the first decision
  * the service is down: R0-R2 and unclassified fail OPEN, R3 and R4 fail CLOSED, an operator override and the `on_error=`
    argument win, a rule's `on_error: closed` for a low-risk capability holds ONLY when the table was cached before the outage
  * a `policy.warning` with a fixed reason is recorded for every fall-back once the API returns (exactly one per call)
  * a slow responder and a responder that never answers: the decision call ends at `policy_timeout`
  * the API dies during an approval wait: `PolicyUnavailable` within the documented bound (five failed rounds), closed
  * an approval that expires: `on_expiry: deny` raises ApprovalExpired, `on_expiry: allow` proceeds with a warning
  * guard() raised nothing but the documented exceptions in any of it

Only the standard library plus `agent_blackbox` are used here (the SDK environment has no httpx). Secrets are never printed.
"""

import http.server
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from agent_blackbox import (
    ApprovalExpired,
    BlackBox,
    PolicyDenied,
    PolicyUnavailable,
)
from agent_blackbox.policy import ApprovalDenied

API = os.environ["E2E_API"]
FAIL_API = os.environ["E2E_FAIL_API"]
FAIL_PORT = int(FAIL_API.rsplit(":", 1)[1])
SLOW_PORT = int(os.environ["E2E_SLOW_PORT"])
ORIGIN = os.environ["E2E_ORIGIN"]
SLUG = os.environ["E2E_WORKSPACE"]
WORK = Path(os.environ["E2E_WORK"])
ROOT = Path(os.environ["E2E_ROOT"])
STATE = json.loads(Path(os.environ["E2E_STATE"]).read_text())
KEY = STATE["keys"]["failmodes"]
PROJECT = STATE["projects"]["failmodes"]
DOCUMENTED = (PolicyDenied, ApprovalDenied, ApprovalExpired, PolicyUnavailable)
RAISED: set[str] = set()  # the exception types guard() raised across every scenario
CALLS = {"n": 0}
SENTINEL = "SENT-" + os.environ["E2E_PLANT"]
VOCABULARY = {
    "policy_service_unreachable", "policy_timeout", "policy_error", "unauthorised", "intent_too_large", "invalid_intent",
    "internal_error", "approval_consume_failed", "approval_expired_allowed",
}


def fail(message: str) -> None:
    print(f"POLICY-E2E SDK CHECK FAILED: {message}", file=sys.stderr)
    raise SystemExit(1)


def expect(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def emit(**evidence: Any) -> None:
    print(json.dumps(evidence, sort_keys=True, default=str), flush=True)


# --------------------------------------------------------------------------------------------- the people


_workspace: dict[str, str] = {}


def person(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    """A request as the signed-in OWNER (a development session), to the API that stays up."""
    if not _workspace:
        request = urllib.request.Request(API + "/v1/me", headers={"Cookie": "abb_session=" + os.environ["E2E_OWNER_SESSION"]})
        with urllib.request.urlopen(request, timeout=20) as response:
            me = json.loads(response.read())
        _workspace["id"] = next(m["workspace"]["id"] for m in me["memberships"] if m["workspace"]["slug"] == SLUG)
    headers = {"Cookie": "abb_session=" + os.environ["E2E_OWNER_SESSION"], "Origin": ORIGIN, "x-abb-workspace": _workspace["id"], "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        raw = error.read()
        return error.code, json.loads(raw) if raw else None


def publish(document: str, comment: str) -> None:
    status, body = person("POST", "/v1/policies/publish", {"document": document, "project_id": PROJECT, "comment": comment})
    expect(status == 201, f"publish -> {status} {body}")


def run_warnings(run_id: str) -> list[dict[str, Any]]:
    status, body = person("GET", f"/v1/runs/{run_id}/events?limit=500")
    expect(status == 200, f"events -> {status}")
    return [e["attributes"] for e in body["items"] if e["event_type"] == "policy.warning"]


# -------------------------------------------------------------------------------------------- the fail API


class FailApi:
    """A second API process on the same database, in a process group of its own: stopped by that group, never by name."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen[bytes] | None = None
        self.log = WORK / "api-fail.log"

    def start(self) -> None:
        handle = self.log.open("ab")
        self.proc = subprocess.Popen(  # noqa: S603  (fixed argv)
            ["uv", "run", "uvicorn", "abb_api.main:app_from_env", "--factory", "--port", str(FAIL_PORT)],
            cwd=ROOT / "apps" / "api", env=os.environ.copy(), stdout=handle, stderr=subprocess.STDOUT, start_new_session=True,
        )
        with (WORK / "spawned.pids").open("a") as pids:
            pids.write(f"{self.proc.pid}\n")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(FAIL_API + "/readyz", timeout=2):
                    return
            except Exception:
                time.sleep(0.3)
        fail("the second API did not become ready")

    def stop(self) -> float:
        """Make the API unreachable; returns when the port refuses connections."""
        assert self.proc is not None
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)  # an outage, not a graceful drain
        except ProcessLookupError:
            pass
        self.proc.wait(timeout=10)
        started = time.monotonic()
        while time.monotonic() - started < 15:
            try:
                socket.create_connection(("127.0.0.1", FAIL_PORT), timeout=0.3).close()
            except OSError:
                return time.monotonic() - started
            time.sleep(0.1)
        fail("the second API still accepts connections")
        return 0.0


# ------------------------------------------------------------------------------------------- the scenarios


def client(endpoint: str, **options: Any) -> BlackBox:
    # The exporter's retry window (ADR-013: five attempts, about 8 s with the defaults) is bounded, so a warning recorded during an
    # outage survives only if the API returns inside it. A cold `uv run` start takes longer than that on a loaded machine, so the
    # clients that must deliver warnings after the restart get ten attempts; what the defaults do is asserted separately below.
    options.setdefault("max_attempts", 10)
    options.setdefault("backoff_max", 5.0)
    return BlackBox(api_key=KEY, endpoint=endpoint, project="failmodes", agent_id="failmodes-agent", policy_mode="enforce", flush_interval=0.1, **options)


def guard(bb: BlackBox, run: Any, capability: str, risk: str | None, **kw: Any) -> tuple[str, float]:
    """Call guard() the way an integration does; returns (outcome, seconds). Records which exception TYPE came out."""
    CALLS["n"] += 1
    started = time.monotonic()
    try:
        decision = bb.guard(capability, "res", risk, {"note": SENTINEL, "flag": True}, run=run, **kw)
        return decision.via, time.monotonic() - started
    except DOCUMENTED as error:
        RAISED.add(type(error).__name__)
        return type(error).__name__, time.monotonic() - started
    except BaseException as error:  # anything else is the bug this gate looks for
        RAISED.add("UNDOCUMENTED:" + type(error).__name__)
        raise


FAIL_MODES_POLICY = """\
version: 1
policies:
  - id: low-closed
    description: This low-risk capability must not run unchecked.
    when: {capability: low.risky}
    decision: allow
    on_error: closed
  - id: gate-push
    description: Pushing to main needs a person.
    when: {capability: github.push, resource: "origin main"}
    decision: require_approval
    approver_role: admin
    expires_in: 15m
  - id: expire-deny
    description: Expires into a denial.
    when: {capability: expire.deny}
    decision: require_approval
    expires_in: 1m
  - id: expire-allow
    description: Expires into an allow.
    when: {capability: expire.allow}
    decision: require_approval
    expires_in: 1m
    on_expiry: allow
  - id: deny-secrets
    description: Secrets are off limits.
    when: {capability: filesystem.read, resource: "**/.env*"}
    decision: deny
"""


def expiry_variants(results: dict[str, Any]) -> None:
    """~65 s each, in parallel with the outage scenarios, against the API that stays up. Nobody decides."""

    def one(name: str, capability: str) -> None:
        bb = client(API)
        with bb.run(f"expiry {name}") as run:
            started = time.monotonic()
            outcome, _ = guard(bb, run, capability, None)
            elapsed = time.monotonic() - started
            results[name] = {"outcome": outcome, "seconds": round(elapsed, 1), "run": run.run_id}
        bb.shutdown(20)

    threads = [threading.Thread(target=one, args=("on_expiry_deny", "expire.deny")), threading.Thread(target=one, args=("on_expiry_allow", "expire.allow"))]
    for thread in threads:
        thread.start()
    results["_threads"] = threads


class Slow(http.server.BaseHTTPRequestHandler):
    delay = 5.0

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        time.sleep(self.delay)
        try:
            body = json.dumps({"decision": "allow", "policy_id": None, "policy_version": 1, "reason": "", "on_error": "open"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass

    do_GET = do_POST

    def log_message(self, *args: Any) -> None:
        pass


def gate4() -> None:
    publish(FAIL_MODES_POLICY, "fail modes (policy-e2e)")
    api = FailApi()
    api.start()
    results: dict[str, Any] = {}
    expiry_variants(results)  # background; talks to the API that stays up

    # --- concurrent callers against the live API: every answer arrives (found by this script: uvicorn writes the headers and the body
    # separately, and the SDK's transport used to read the body from a closed socket, so about one call in twenty reported an outage)
    busy = client(FAIL_API)
    outcomes: list[str] = []
    with busy.run("concurrent decisions") as b_run:
        def hammer(thread: int) -> None:
            for i in range(120):
                outcomes.append(guard(busy, b_run, f"stress.t{thread}", "R0")[0])

        crowd = [threading.Thread(target=hammer, args=(n,)) for n in range(8)]
        for thread in crowd:
            thread.start()
        for thread in crowd:
            thread.join(120)
    expect(len(outcomes) == 960 and set(outcomes) == {"policy"}, f"concurrent decisions: {len(outcomes)} answers, outcomes {sorted(set(outcomes))}")
    expect(busy.stats().get("policy_fail_open", 0) == 0, f"SDK stats after 960 concurrent decisions: {busy.stats()}")
    busy.shutdown(10)
    results["concurrent_decisions"] = {"threads": 8, "decisions": len(outcomes), "answered_by_policy": len(outcomes)}

    # --- warm clients while the service answers: allow, deny, and the cached fail-mode table
    warm = client(FAIL_API)
    cold = client(FAIL_API)  # never asked while the service was up: no cached table
    over = client(FAIL_API, policy_fail_modes={"filesystem.read": "closed", "shell.execute": "open"})
    runs: dict[str, Any] = {}
    with warm.run("fail modes: warm") as w_run, cold.run("fail modes: cold") as c_run, over.run("fail modes: overrides") as o_run:
        runs = {"warm": w_run, "cold": c_run, "over": o_run}
        up_allow = guard(warm, w_run, "low.risky", "R1")
        expect(up_allow[0] == "policy", f"allow while up: {up_allow}")
        up_deny = guard(warm, w_run, "filesystem.read", "R0")  # resource "res" does not match the deny glob: allow
        expect(up_deny[0] == "policy", f"allow while up (2): {up_deny}")
        bb_env = client(FAIL_API)
        with bb_env.run("fail modes: deny") as d_run:
            try:
                bb_env.guard("filesystem.read", "app/.env", "R0", {}, run=d_run)
                fail("the .env read was allowed while the service is up")
            except PolicyDenied as denied:
                RAISED.add(type(denied).__name__)
                expect(denied.policy_id == "deny-secrets", f"deny rule: {denied.policy_id}")
        bb_env.shutdown(5)
        table = warm._policy_engine()._table  # the cached fail-mode table (private on purpose: it is the thing under test)
        expect(table is not None and table.mode_for("low.risky", "res", "R1") == "closed", "the rule's on_error closed was not cached")
        over_up = guard(over, o_run, "low.risky", "R1")  # also caches its table
        expect(over_up[0] == "policy", f"allow while up (3): {over_up}")

        # --- the outage
        outage_started = time.monotonic()
        refused_after = api.stop()
        out: dict[str, Any] = {}
        for risk in ("R0", "R1", "R2", None, "R3", "R4"):
            out[f"default {risk}"] = guard(cold, c_run, "default.cap", risk)
        out["cold: rule closed, not cached"] = guard(cold, c_run, "low.risky", "R1")
        out["warm: rule closed, cached"] = guard(warm, w_run, "low.risky", "R1")
        out["warm: other R1 (not listed)"] = guard(warm, w_run, "other.low", "R1")
        out["warm: R3 (default closed)"] = guard(warm, w_run, "default.cap", "R3")
        out["override closed beats R0"] = guard(over, o_run, "filesystem.read", "R0")
        out["override open beats R4"] = guard(over, o_run, "shell.execute", "R4")
        out["on_error=open beats R3"] = guard(cold, c_run, "default.cap", "R3", on_error="open")
        out["on_error=closed beats R0"] = guard(cold, c_run, "default.cap", "R0", on_error="closed")
        outage_seconds = time.monotonic() - outage_started
        for risk in ("R0", "R1", "R2", None):
            expect(out[f"default {risk}"][0] == "fail_open", f"R0-R2 and unclassified fail open: {risk} -> {out[f'default {risk}']}")
        for risk in ("R3", "R4"):
            expect(out[f"default {risk}"][0] == "PolicyUnavailable", f"R3/R4 fail closed: {risk} -> {out[f'default {risk}']}")
        expect(out["cold: rule closed, not cached"][0] == "fail_open", "a rule's on_error is unknown to an SDK that never cached the table")
        expect(out["warm: rule closed, cached"][0] == "PolicyUnavailable", "the cached fail-mode table was not used after the outage")
        expect(out["warm: other R1 (not listed)"][0] == "fail_open", "an unlisted low-risk capability fails open")
        expect(out["warm: R3 (default closed)"][0] == "PolicyUnavailable", "R3 default")
        expect(out["override closed beats R0"][0] == "PolicyUnavailable", "operator override closed")
        expect(out["override open beats R4"][0] == "fail_open", "operator override open")
        expect(out["on_error=open beats R3"][0] == "fail_open", "on_error argument open")
        expect(out["on_error=closed beats R0"][0] == "PolicyUnavailable", "on_error argument closed")
        slowest = max(seconds for _, seconds in out.values())
        expect(slowest < 1.0, f"a decision call against a dead port took {slowest:.2f} s")
        results["outage"] = {k: v[0] for k, v in out.items()}

        # --- the API returns: the buffered policy.warning events are delivered, exactly one per fall-back
        api.start()
    for name, bb in (("warm", warm), ("cold", cold), ("over", over)):
        expect(bb.flush(30), f"{name}: the SDK could not deliver its events after the API returned")
    warned = {name: run_warnings(runs[name].run_id) for name in runs}
    per_run_calls = {"cold": 6 + 1 + 2, "warm": 3, "over": 2}  # one warning per fall-back, no matter the outcome
    for name, warnings in warned.items():
        expect(len(warnings) == per_run_calls[name], f"{name}: {len(warnings)} policy.warning events, expected {per_run_calls[name]}")
        for attributes in warnings:
            expect(attributes.get("policy.reason") == "policy_service_unreachable", f"{name}: reason {attributes.get('policy.reason')}")
            expect(set(attributes) <= {"policy.capability", "policy.reason"}, f"{name}: warning carries {sorted(attributes)}")
            expect(SENTINEL not in json.dumps(attributes), "a warning carries argument content")
    results["warnings"] = {name: len(w) for name, w in warned.items()}
    results["reasons"] = sorted({a["policy.reason"] for w in warned.values() for a in w})
    expect(set(results["reasons"]) <= VOCABULARY, "warning reasons outside the fixed vocabulary")
    for bb in (warm, cold, over):
        stats = bb.stats()
        expect(stats.get("dropped", 0) == 0 and stats.get("internal_errors", 0) == 0, f"SDK stats after the outage: {stats}")
        bb.shutdown(5)
    results["outage_seconds"] = round(outage_seconds, 2)
    results["port_refused_after_s"] = round(refused_after, 2)

    # --- the API dies DURING an approval wait: PolicyUnavailable within the bound, closed whatever the fail mode
    pending: dict[str, str] = {}
    announced = threading.Event()

    def on_approval(info: Any) -> None:
        pending["id"] = info.approval_id
        announced.set()

    waiter = client(FAIL_API, policy_on_approval=on_approval, policy_fail_modes={"github.push": "open"})  # even "open" must not let a broken wait through
    outcome: dict[str, Any] = {}
    with waiter.run("wait broken") as wait_run:
        def asked() -> None:
            CALLS["n"] += 1
            started = time.monotonic()
            try:
                waiter.guard("github.push", "origin main", "R3", {"remote": "origin"}, run=wait_run)
                outcome.update(via="proceeded", seconds=time.monotonic() - started)
            except DOCUMENTED as error:
                RAISED.add(type(error).__name__)
                outcome.update(via=type(error).__name__, seconds=time.monotonic() - started, reason=getattr(error, "reason", None))
            except BaseException as error:
                RAISED.add("UNDOCUMENTED:" + type(error).__name__)
                outcome.update(via="UNDOCUMENTED:" + type(error).__name__, seconds=0.0)

        thread = threading.Thread(target=asked)
        thread.start()
        expect(announced.wait(20), "the wait never started")
        time.sleep(1.5)  # let at least one long-poll round be in flight
        killed_at = time.monotonic()
        api.stop()
        thread.join(60)
        expect(not thread.is_alive(), "guard() did not return within 60 s of the API dying during a wait")
        since_kill = time.monotonic() - killed_at
    expect(outcome["via"] == "PolicyUnavailable", f"a broken wait must fail closed: {outcome}")
    expect(since_kill < 20.0, f"a broken wait took {since_kill:.1f} s to give up (bound: five failed rounds with <= 2 s backoff)")
    results["wait_broken"] = {"outcome": outcome["via"], "reason": outcome.get("reason"), "seconds_after_kill": round(since_kill, 1)}
    api.start()  # tidy up: withdraw the abandoned request with the key that asked
    abandoned = pending["id"]
    request = urllib.request.Request(API + f"/v1/approvals/{abandoned}", method="DELETE", headers={"Authorization": "Bearer " + KEY})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            results["abandoned_cancelled"] = response.status
    except urllib.error.HTTPError as error:
        results["abandoned_cancelled"] = error.code
    waiter.shutdown(10)

    # --- timeouts: a slow responder and one that never answers (the whole exchange ends at policy_timeout)
    results["timeouts"] = {}
    for label, delay in (("slow responder (3 s)", 3.0), ("responder that never answers", 600.0)):
        Slow.delay = delay
        server = http.server.ThreadingHTTPServer(("127.0.0.1", SLOW_PORT), Slow)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            slow = BlackBox(api_key=KEY, endpoint=f"http://127.0.0.1:{SLOW_PORT}", project="failmodes", agent_id="failmodes-agent", policy_mode="enforce", policy_timeout=0.6, flush_interval=0.1)
            with slow.run("timeouts") as t_run:
                open_result = guard(slow, t_run, "default.cap", "R0")
                closed_result = guard(slow, t_run, "default.cap", "R3")
            expect(open_result[0] == "fail_open" and 0.5 <= open_result[1] < 1.6, f"{label}: R0 {open_result}")
            expect(closed_result[0] == "PolicyUnavailable" and 0.5 <= closed_result[1] < 1.6, f"{label}: R3 {closed_result}")
            results["timeouts"][label] = {"R0": f"{open_result[0]} in {open_result[1]:.2f} s", "R3": f"{closed_result[0]} in {closed_result[1]:.2f} s", "policy_timeout": 0.6}
            slow.shutdown(1)
        finally:
            server.shutdown()
            server.server_close()
    # an address nothing routes to (a blackhole for the SYN) ends at the same deadline, or is refused at once
    hole = BlackBox(api_key=KEY, endpoint="http://10.255.255.1:9", project="failmodes", agent_id="failmodes-agent", policy_mode="enforce", policy_timeout=0.6, flush_interval=0.1)
    with hole.run("blackhole") as h_run:
        blackholed = guard(hole, h_run, "default.cap", "R3")
    expect(blackholed[0] == "PolicyUnavailable" and blackholed[1] < 2.0, f"blackhole: {blackholed}")
    results["timeouts"]["blackholed address"] = f"{blackholed[0]} in {blackholed[1]:.2f} s"
    hole.shutdown(1)

    # --- expiry fallbacks (run in the background since the start)
    for thread in results.pop("_threads"):
        thread.join(150)
        expect(not thread.is_alive(), "an expiry variant did not finish")
    deny, allow = results["on_expiry_deny"], results["on_expiry_allow"]
    expect(deny["outcome"] == "ApprovalExpired", f"on_expiry deny: {deny}")
    expect(allow["outcome"] == "expiry_allow", f"on_expiry allow: {allow}")
    for variant in (deny, allow):
        expect(50 <= variant["seconds"] <= 80, f"an expiry of one minute ended after {variant['seconds']} s")
    # the allow variant recorded why it proceeded; the deny variant has the server's own approval.expired event
    time.sleep(1.0)
    expect(any(w.get("policy.reason") == "approval_expired_allowed" for w in run_warnings(allow["run"])), "no approval_expired_allowed warning")
    status, events = person("GET", f"/v1/runs/{deny['run']}/events?limit=200")
    expect(any(e["event_type"] == "approval.expired" for e in events["items"]), "no approval.expired event")

    undocumented = sorted(t for t in RAISED if t.startswith("UNDOCUMENTED"))
    expect(not undocumented, f"guard() raised {undocumented}")
    expect(RAISED <= {"PolicyDenied", "ApprovalDenied", "ApprovalExpired", "PolicyUnavailable"}, f"exception types: {sorted(RAISED)}")
    timeout_seconds = [float(v["R0"].split(" in ")[1].split(" ")[0]) for v in results["timeouts"].values() if isinstance(v, dict)]
    bounds = {
        "timeouts_max_s": max(timeout_seconds + [float(results["timeouts"]["blackholed address"].split(" in ")[1].split(" ")[0])]),
        "wait_broken_s": results["wait_broken"]["seconds_after_kill"],
        "expiry_min_s": min(deny["seconds"], allow["seconds"]),
        "expiry_max_s": max(deny["seconds"], allow["seconds"]),
    }
    (WORK / "gate4.json").write_text(json.dumps({"exception_types": sorted(RAISED), "calls": CALLS["n"], "bounds": bounds}))
    emit(gate=4, **{k: v for k, v in results.items() if not k.startswith("_")}, guard_calls=CALLS["n"], exception_types=sorted(RAISED))


def main() -> None:
    commands = {"gate4": gate4}
    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        fail(f"usage: {sys.argv[0]} {{{'|'.join(commands)}}}")
    commands[sys.argv[1]]()


if __name__ == "__main__":
    main()

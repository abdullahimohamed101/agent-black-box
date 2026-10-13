"""Gates 2, 5, 6, 7, 8 and the UI preparation of scripts/policy-e2e.sh. Imported by policy_e2e_check.py."""

import asyncio
import json
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

import httpx

from policy_e2e_lib import (
    SLUG,
    add_log_needles,
    agent,
    ask,
    ask_ok,
    consume,
    decide,
    detail,
    emit,
    error_code,
    expect,
    hash_of,
    ids,
    intent,
    log_text,
    new_run,
    person,
    run_events,
    save,
    state,
)

# ------------------------------------------------------------------------------------------------- gate 2


def gate2() -> None:
    from policy_e2e_check import RACES_POLICY, publish  # noqa: PLC0415

    sentinel = f"SENT-{os.environ['E2E_PLANT']}"
    seen: list[str] = []  # everything an agent key was told, to prove none of it carries the intent or its hash
    owner_agent = agent("races")
    other_key = agent("races_b")
    other_project = agent("other")
    foreign = agent("foreign")

    def agent_call(response: httpx.Response) -> httpx.Response:
        seen.append(response.text)
        return response

    def shell_intent(**overrides: Any) -> dict[str, Any]:
        return intent(
            capability="shell.execute",
            resource="git",
            risk_class="R3",
            arguments={"command": f"git push --force origin main # {sentinel}", "cwd": "/work"},
            **overrides,
        )

    def approved(body: dict[str, Any], client: httpx.Client = owner_agent) -> str:
        approval = ask_ok(client, body)
        seen.append("")
        response = decide("admin", approval, "approve", "looks right")
        expect(response.status_code == 200, f"approve -> {response.status_code} {response.text[:200]}")
        return approval

    # approvals whose clock starts first, so they are expired by the time we get back to them
    short_body = intent(capability="test.short", resource="x", risk_class=None, arguments={"note": sentinel})
    short_approved = approved(short_body)
    short_unapproved = ask_ok(owner_agent, intent(capability="test.short", resource="y", risk_class=None, arguments={"note": sentinel}))
    started = time.monotonic()

    # 1. an approval for command A cannot be used for anything else, and a failed attempt consumes nothing
    base = shell_intent()
    approval = approved(base)
    variants: dict[str, dict[str, Any]] = {
        "a different command": {**base, "arguments": {"command": "rm -rf / # " + sentinel, "cwd": "/work"}},
        "a different resource": {**base, "resource": "curl"},
        "a different argument": {**base, "arguments": {**base["arguments"], "cwd": "/elsewhere"}},
        "an extra argument": {**base, "arguments": {**base["arguments"], "dry_run": False}},
        "a different risk class": {**base, "risk_class": "R2"},
        "no risk class": {**base, "risk_class": None},
        "a different agent": {**base, "agent_id": "someone-else"},
        "a different run": {**base, "run_id": new_run()},
        "a different capability": {**base, "capability": "shell.run"},
    }
    codes = {}
    for name, body in variants.items():
        response = agent_call(consume(owner_agent, approval, body))
        codes[name] = (response.status_code, error_code(response))
        expect(codes[name] == (409, "APPROVAL_INTENT_MISMATCH"), f"consume with {name} -> {codes[name]}")
    now = detail(approval)
    expect(now["state"] == "APPROVED" and now["consumed"] is False, "a mismatched attempt consumed or changed the approval")

    # 2. the exact intent: once
    first = agent_call(consume(owner_agent, approval, base))
    expect(first.status_code == 200 and set(first.json()) == {"approval_id", "consumed_at"}, f"exact consume -> {first.status_code} {first.text[:120]}")
    again = agent_call(consume(owner_agent, approval, base))
    expect((again.status_code, error_code(again)) == (409, "APPROVAL_CONSUMED"), f"second use -> {again.status_code} {error_code(again)}")
    expect(detail(approval)["consumed"] is True, "consumed flag")
    deciding_again = [decide("admin", approval, "approve"), decide("admin", approval, "deny")]
    for response in deciding_again:
        expect((response.status_code, error_code(response)) == (409, "APPROVAL_NOT_REQUESTED"), f"deciding a decided approval -> {response.status_code} {error_code(response)}")

    # 3. a fresh ask for the same intent after consumption is a NEW request: single use means a human again
    fresh = agent_call(ask(owner_agent, base)).json()
    expect(fresh["decision"] == "require_approval" and fresh["approval_id"] != approval and fresh["approval_state"] == "REQUESTED", f"fresh ask: {fresh}")
    expect(fresh["approval_id"] != approval, "the consumed approval was reused")
    cancelled = fresh["approval_id"]

    # 4. not found for everybody else: another key of the same project, another project, another workspace
    exact = shell_intent()
    other = approved(exact)
    crossing = {}
    for name, client in (("another key of the project", other_key), ("another project", other_project), ("another workspace", foreign)):
        response = agent_call(consume(client, other, exact))
        crossing[name] = (response.status_code, error_code(response))
        expect(crossing[name] == (404, "APPROVAL_NOT_FOUND"), f"consume by {name} -> {crossing[name]}")
    expect(agent_call(client_wait(other_key, other)).status_code == 404, "another key waits on the approval")
    expect(agent_call(other_key.delete(f"/v1/approvals/{other}")).status_code == 404, "another key cancels the approval")
    expect(detail(other)["consumed"] is False, "a foreign consume spent the approval")
    expect(agent_call(consume(owner_agent, other, exact)).status_code == 200, "the asking key can use its own approval")

    # 5. before approval, denied, cancelled: never usable
    waiting = shell_intent()
    waiting_id = ask_ok(owner_agent, waiting)
    r = agent_call(consume(owner_agent, waiting_id, waiting))
    expect((r.status_code, error_code(r)) == (409, "APPROVAL_NOT_APPROVED"), f"before approval -> {r.status_code} {error_code(r)}")
    expect(decide("admin", waiting_id, "deny", "no").status_code == 200, "deny")
    r = agent_call(consume(owner_agent, waiting_id, waiting))
    expect((r.status_code, error_code(r)) == (409, "APPROVAL_NOT_APPROVED"), f"after denial -> {r.status_code} {error_code(r)}")
    r = decide("admin", waiting_id, "approve")
    expect((r.status_code, error_code(r)) == (409, "APPROVAL_NOT_REQUESTED"), f"approving a denied request -> {r.status_code} {error_code(r)}")
    expect(agent_call(owner_agent.delete(f"/v1/approvals/{cancelled}")).status_code == 204, "cancel")
    r = agent_call(consume(owner_agent, cancelled, base))
    expect((r.status_code, error_code(r)) == (409, "APPROVAL_NOT_APPROVED"), f"after cancel -> {r.status_code} {error_code(r)}")
    r = decide("admin", cancelled, "approve")
    expect((r.status_code, error_code(r)) == (409, "APPROVAL_NOT_REQUESTED"), f"approving a cancelled request -> {r.status_code} {error_code(r)}")

    # 6. a deny published after the approval wins (the approval stays usable once the deny is gone)
    guarded = shell_intent()
    guarded_id = approved(guarded)
    current = person("owner").get("/v1/policies/current", params={"project_id": ids()["races"]}).json()["policy"]["version"]
    deny_rule = RACES_POLICY.replace("policies:\n", "policies:\n  - id: deny-shell\n    description: Shell is off for now.\n    when: {capability: shell.execute}\n    decision: deny\n", 1)
    publish("races", deny_rule, "temporary deny (policy-e2e)", base=current)
    r = agent_call(consume(owner_agent, guarded_id, guarded))
    expect((r.status_code, error_code(r)) == (409, "APPROVAL_POLICY_DENIED"), f"consume after a deny was published -> {r.status_code} {error_code(r)}")
    expect(detail(guarded_id)["state"] == "APPROVED" and detail(guarded_id)["consumed"] is False, "a policy-denied consume changed the approval")
    events = [e for e in run_events(guarded["run_id"]) if e["event_type"] == "policy.action.blocked"]
    audit = person("owner").get("/v1/audit", params={"limit": 100}).json()["items"]
    expect(any(a["action"] == "approval.consume_refused" for a in audit), "the refused consume is audit-logged")
    publish("races", RACES_POLICY, "restore (policy-e2e)", base=current + 1)
    expect(agent_call(consume(owner_agent, guarded_id, guarded)).status_code == 200, "after the deny is gone the approval is usable again")

    # 7. expiry: wait until the one-minute approvals are over
    remaining = 64 - (time.monotonic() - started)
    if remaining > 0:
        time.sleep(remaining)
    r = agent_call(consume(owner_agent, short_approved, short_body))
    expect((r.status_code, error_code(r)) == (409, "APPROVAL_EXPIRED"), f"consume after expiry -> {r.status_code} {error_code(r)}")
    r = decide("admin", short_unapproved, "approve")
    expect((r.status_code, error_code(r)) == (409, "APPROVAL_NOT_REQUESTED"), f"approving after expiry -> {r.status_code} {error_code(r)}")
    r = agent_call(consume(owner_agent, short_unapproved, short_body))
    expect((r.status_code, error_code(r)) == (409, "APPROVAL_EXPIRED"), f"consume of an unapproved expired request -> {r.status_code} {error_code(r)}")
    expect(detail(short_approved)["state"] == "EXPIRED" and detail(short_approved)["consumed"] is False, "an approved but unused request becomes EXPIRED")

    # 8. nothing an agent was told, no audit row, no run event and no log carries the intent or its hash
    hashes = [hash_of(b) for b in (base, exact, waiting, guarded, short_body)]
    agent_text = "\n".join(seen)
    audit_text = json.dumps(person("owner").get("/v1/audit", params={"limit": 200}).json())
    events_text = json.dumps([run_events(r) for r in (base["run_id"], exact["run_id"], guarded["run_id"])])
    logs = log_text()
    for where, text in (("agent responses", agent_text), ("the audit log", audit_text), ("run events", events_text), ("server logs", logs)):
        for h in hashes:
            expect(h not in text, f"an intent hash appears in {where}")
        expect(sentinel not in text, f"the intent's content appears in {where}")
        expect("git push --force" not in text, f"a command appears in {where}")
    for h in hashes:  # the approver's view of an approval never carries the hash either
        expect(h not in json.dumps(detail(approval)), "the approval detail carries the hash")
    add_log_needles(sentinel, *hashes)
    emit(
        gate=2,
        mismatches=codes,
        single_use=["200", "APPROVAL_CONSUMED"],
        redecide=[error_code(r) for r in deciding_again],
        fresh_after_consume=True,
        not_found_for=crossing,
        before_approval="APPROVAL_NOT_APPROVED",
        denied_and_cancelled="APPROVAL_NOT_APPROVED",
        after_policy_deny="APPROVAL_POLICY_DENIED",
        after_expiry="APPROVAL_EXPIRED",
        blocked_events_on_refused_consume=len(events),
        leak_checked=["agent responses", "audit log", "run events", "server logs"],
        hashes_checked=len(hashes),
    )


def client_wait(client: httpx.Client, approval: str) -> httpx.Response:
    return client.get(f"/v1/approvals/{approval}/wait", params={"timeout": 0})


# ------------------------------------------------------------------------------------------------- gate 5


def audit_rows(approval: str, action: str) -> int:
    rows = person("owner").get("/v1/audit", params={"limit": 200}).json()["items"]
    return sum(1 for a in rows if a["resource_id"] == approval and a["action"] == action)


def event_count(run: str, event_type: str) -> int:
    return sum(1 for e in run_events(run) if e["event_type"] == event_type)


def burst(calls: list[Callable[[], Any]]) -> list[Any]:
    """Run the calls at the same moment (a barrier releases every thread together)."""
    barrier = threading.Barrier(len(calls))

    def go(call: Callable[[], Any]) -> Any:
        barrier.wait()
        return call()

    with ThreadPoolExecutor(len(calls)) as pool:
        return list(pool.map(go, calls))


def gate5() -> None:
    from policy_e2e_lib import to_uuid  # noqa: PLC0415

    dev_agent = agent("races_dev")  # created by the developer, so the owner AND the admin may decide its requests
    deciders = [person("admin"), person("owner")]
    short = intent(capability="test.short", resource="race", risk_class=None, arguments={"n": 1})
    short_id = ask_ok(dev_agent, short)  # a one-minute request whose last second is raced below
    expires_at = datetime.fromisoformat(detail(short_id)["expires_at"].replace("Z", "+00:00"))

    # 1. 20 concurrent decisions (approve and deny, from two signed-in people) on one request: one winner, five trials
    trials = []
    for trial in range(5):
        body = intent()
        approval = ask_ok(dev_agent, body)
        calls = [
            (lambda i=i: deciders[i % 2].post(f"/v1/approvals/{approval}/decision", json={"decision": "approve" if i % 3 else "deny"}))
            for i in range(20)
        ]
        responses = burst(calls)
        codes = sorted((r.status_code, error_code(r)) for r in responses)
        winners = [r for r in responses if r.status_code == 200]
        expect(len(winners) == 1, f"trial {trial}: {len(winners)} winners")
        expect(all(c == (409, "APPROVAL_NOT_REQUESTED") for c in codes if c[0] != 200), f"trial {trial}: losers answered {set(codes)}")
        state_now = detail(approval)["state"]
        expect(state_now == winners[0].json()["state"], "the stored state is not the winner's")
        granted, denied = event_count(body["run_id"], "approval.granted"), event_count(body["run_id"], "approval.denied")
        expect(granted + denied == 1 and (granted == 1) == (state_now == "APPROVED"), f"trial {trial}: decision events {granted}+{denied} for {state_now}")
        expect(audit_rows(approval, "approval.decide") == 1, f"trial {trial}: audit rows {audit_rows(approval, 'approval.decide')}")
        trials.append({"winners": 1, "losers": 19, "state": state_now})
    expect(len({t["state"] for t in trials}) >= 1, "trials")

    # 2. 30 concurrent consumes of one approved request: exactly one spends it
    body = intent()
    approval = ask_ok(dev_agent, body)
    expect(decide("admin", approval, "approve").status_code == 200, "approve")
    responses = burst([(lambda: consume(dev_agent, approval, body)) for _ in range(30)])
    ok = [r for r in responses if r.status_code == 200]
    others = {(r.status_code, error_code(r)) for r in responses if r.status_code != 200}
    expect(len(ok) == 1 and others == {(409, "APPROVAL_CONSUMED")}, f"consumes: {len(ok)} ok, others {others}")
    expect(event_count(body["run_id"], "approval.consumed") == 1 and audit_rows(approval, "approval.consume") == 1, "one consume event and one audit row")
    consumed_id = approval

    # 3. approve versus cancel (the agent withdraws while a person decides): one winner, consistent events, five trials
    race = []
    for trial in range(5):
        body = intent()
        approval = ask_ok(dev_agent, body)
        approve_call = lambda a=approval: person("admin").post(f"/v1/approvals/{a}/decision", json={"decision": "approve"})  # noqa: E731
        cancel_call = lambda a=approval: dev_agent.delete(f"/v1/approvals/{a}")  # noqa: E731
        results = burst([approve_call, cancel_call, approve_call, cancel_call])
        wins = [r for r in results if r.status_code in (200, 204)]
        expect(len(wins) == 1, f"approve/cancel trial {trial}: {[r.status_code for r in results]}")
        final = detail(approval)["state"]
        expect(final in ("APPROVED", "CANCELLED"), f"final state {final}")
        want = "approval.granted" if final == "APPROVED" else "approval.cancelled"
        expect(event_count(body["run_id"], want) == 1, f"trial {trial}: {want} events")
        expect(event_count(body["run_id"], "approval.cancelled" if final == "APPROVED" else "approval.granted") == 0, "both outcomes recorded")
        race.append(final)

    # 4. approve versus expiry: fire decisions across the last instants of the one-minute request
    wait_for = (expires_at - datetime.now(expires_at.tzinfo)).total_seconds() - 0.4
    if wait_for > 0:
        time.sleep(wait_for)
    pool = ThreadPoolExecutor(12)
    futures = []
    for i in range(24):
        futures.append(pool.submit(lambda: person("admin" if i % 2 else "owner").post(f"/v1/approvals/{short_id}/decision", json={"decision": "approve"})))
        time.sleep(0.04)
    answers = [f.result() for f in futures]
    pool.shutdown()
    won = [r for r in answers if r.status_code == 200]
    expect(len(won) <= 1, f"{len(won)} winners against the clock")
    expect(all(error_code(r) == "APPROVAL_NOT_REQUESTED" for r in answers if r.status_code != 200), f"losers: {[(r.status_code, error_code(r)) for r in answers if r.status_code != 200][:3]}")
    time.sleep(1.5)
    final = wait_until_state(short_id, {"EXPIRED"})
    expect(final["consumed"] is False, "an expired request was spent")
    granted, expired = event_count(short["run_id"], "approval.granted"), event_count(short["run_id"], "approval.expired")
    expect(granted == len(won) and expired == 1, f"events: granted {granted}, expired {expired}")
    expect(audit_rows(short_id, "approval.decide") == len(won), "decide audit rows")
    expect(audit_rows(short_id, "approval.expire") == 1, f"expire audit rows {audit_rows(short_id, 'approval.expire')}")
    r = consume(dev_agent, short_id, short)
    expect((r.status_code, error_code(r)) == (409, "APPROVAL_EXPIRED"), "an expired request can be consumed")

    # 5. illegal SQL as the application's runtime role (not the owner): every attempt fails; the rows are unchanged
    sql_report = asyncio.run(illegal_sql(to_uuid(consumed_id), to_uuid(short_id)))
    expect(detail(consumed_id)["consumed"] is True and detail(consumed_id)["state"] == "APPROVED", "the consumed approval changed")
    expect(detail(short_id)["state"] == "EXPIRED", "the expired approval changed")
    emit(gate=5, concurrent_deciders=trials, concurrent_consumes={"requests": 30, "ok": 1, "APPROVAL_CONSUMED": 29},
         approve_vs_cancel=race, approve_vs_expiry={"requests": 24, "winners": len(won), "final": "EXPIRED"}, illegal_sql=sql_report)


def wait_until_state(approval: str, states: set[str]) -> dict[str, Any]:
    from policy_e2e_lib import wait_until  # noqa: PLC0415

    return dict(wait_until(f"{approval} to be {states}", lambda: (d := detail(approval))["state"] in states and d, 30))


async def illegal_sql(consumed: Any, expired: Any) -> dict[str, Any]:
    import asyncpg  # noqa: PLC0415

    base = os.environ["ABB_DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    head, _, tail = base.partition("@")
    url = f"postgresql://{os.environ['E2E_RUNTIME_ROLE']}:{os.environ['E2E_RUNTIME_PASSWORD']}@{tail}"
    conn = await asyncpg.connect(url)
    try:
        who = await conn.fetchval("select current_user")
        expect(who == os.environ["E2E_RUNTIME_ROLE"], "not connected as the runtime role")
        expect(await conn.fetchval("select count(*) from approvals") > 0, "the runtime role cannot read approvals")
        attempts = {
            "change the intent hash of a consumed approval": ("update approvals set intent_hash = repeat('0', 64) where id = $1", consumed),
            "un-consume": ("update approvals set consumed_at = null, consumed_run_id = null where id = $1", consumed),
            "consume again (later time)": ("update approvals set consumed_at = now() where id = $1", consumed),
            "revive an expired approval": ("update approvals set state = 'REQUESTED', closed_at = null where id = $1", expired),
            "approve an expired approval": ("update approvals set state = 'APPROVED', decided_at = now(), decided_by_user_id = (select id from users limit 1) where id = $1", expired),
            "rewrite the bound resource": ("update approvals set resource_scrubbed = 'develop' where id = $1", consumed),
            "rewrite the capability": ("update approvals set capability = 'shell.execute' where id = $1", consumed),
            "delete one approval": ("delete from approvals where id = $1", consumed),
            "delete every approval": ("delete from approvals", None),
            "truncate approvals": ("truncate approvals", None),
            "insert an approval that is born approved": (
                "insert into approvals (id, workspace_id, project_id, state) select gen_random_uuid(), workspace_id, project_id, 'APPROVED' from approvals limit 1", None),
            "rewrite an event": ("update events set event_type = 'run.completed' where event_type = 'approval.requested'", None),
            "delete an audit row": ("delete from audit_log", None),
            "rewrite an audit row": ("update audit_log set action = 'x'", None),
        }
        report: dict[str, Any] = {}
        for name, (sql, arg) in attempts.items():
            try:
                async with conn.transaction():
                    await (conn.execute(sql, arg) if arg is not None else conn.execute(sql))
                fail_message = f"illegal SQL succeeded: {name}"
                expect(False, fail_message)
            except asyncpg.PostgresError as error:
                report[name] = error.sqlstate
        return {"connected_as": "the runtime role", "attempts": len(report), "refused_with": sorted(set(report.values())), "each": report}
    finally:
        await conn.close()


# ------------------------------------------------------------------------------------------------- gate 7


def start_demo(label: str, *extra: str) -> tuple[Any, Any, Any]:
    from pathlib import Path  # noqa: PLC0415

    from policy_e2e_check import bare_head, demo_env, pending_line  # noqa: PLC0415
    from policy_e2e_lib import WORK, spawn  # noqa: PLC0415

    workdir, log = WORK / f"demo-{label}", WORK / f"demo-{label}.log"
    argv = ["uv", "run", "--project", "packages/sdk-python", "python", "examples/coding-agent/run_demo.py", "--policy-demo", "--workdir", str(workdir), *extra]
    proc = spawn(argv, demo_env("full"), log)
    return proc, Path(workdir), (log, bare_head, pending_line)


def gate7() -> None:
    import ast  # noqa: PLC0415
    import signal  # noqa: PLC0415
    import sys  # noqa: PLC0415
    import tomllib  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from policy_e2e_lib import ROOT, WORK, wait_until  # noqa: PLC0415

    report: dict[str, Any] = {"gate": 7}
    # 1. SIGINT during an approval wait (what Ctrl-C does: the signal goes to the foreground process group)
    proc, workdir, (log, bare_head, pending_line) = start_demo("sigint")
    pending = wait_until("the SIGINT demo to pause", lambda: pending_line(log), 90)
    approval = pending["approval_id"]
    expect(detail(approval)["state"] == "REQUESTED", "the request is waiting")
    time.sleep(1.5)  # inside a long-poll round
    sent = time.monotonic()
    os.killpg(proc.pid, signal.SIGINT)
    try:
        code = proc.wait(timeout=30)
    except Exception:
        fail_now("the demo did not exit within 30 s of SIGINT")
        raise
    exit_seconds = time.monotonic() - sent
    text = log.read_text(errors="replace")
    expect(code != 0, f"the interrupted demo exited {code}")
    expect("KeyboardInterrupt" in text, "the interrupt did not surface as KeyboardInterrupt")
    traceback_types = [line.split(":")[0].strip() for line in text.splitlines() if line and not line.startswith((" ", "{", "Waiting", "Traceback")) and "Error" in line.split(":")[0] and "." not in line.split(":")[0][:1]]
    expect(not any(t.startswith(("Policy", "Approval")) for t in traceback_types), f"a policy exception escaped: {traceback_types}")
    final = wait_until("the approval to be cancelled", lambda: (d := detail(approval))["state"] == "CANCELLED" and d, 20)
    expect(final["consumed"] is False, "an interrupted wait spent the approval")
    run_id = final["run_id"]
    wait_until("the cancel event", lambda: event_count(run_id, "approval.cancelled") == 1, 30)
    expect(event_count(run_id, "approval.consumed") == 0 and event_count(run_id, "approval.granted") == 0, "the interrupted run recorded a grant")
    expect(audit_rows(approval, "approval.cancel") == 1, "the cancel is audit-logged once")
    expect(bare_head(workdir / "origin.git") is None, "the push happened")
    report["sigint"] = {"exit_code": code, "seconds_to_exit": round(exit_seconds, 2), "approval": "CANCELLED", "cancelled_events": 1, "keyboard_interrupt": True}

    # 2. a wait is bounded by the caller's own cap: --approval-timeout 5 against a 15-minute approval
    proc, workdir, (log, bare_head, pending_line) = start_demo("capped", "--approval-timeout", "5")
    started = time.monotonic()
    summary = wait_until("the capped demo to finish", lambda: __import__("policy_e2e_check").demo_summary(log), 90)
    ended = proc.wait(timeout=30)
    pending = pending_line(log)
    expect(pending is not None and ended == 0, f"capped demo exit {ended}")
    expect(summary["pushed"] is False and "git_push" in summary["blocked"], f"capped demo: {summary}")
    capped = detail(pending["approval_id"])
    expect(capped["state"] == "CANCELLED", f"the capped wait left the approval {capped['state']}")
    report["capped_wait"] = {"policy_wait_max_s": 5, "approval": "CANCELLED", "pushed": False, "demo_total_seconds": round(time.monotonic() - started, 1)}

    # 3. only documented exceptions, from every scenario that ran
    gate4 = Path(WORK / "gate4.json")
    if gate4.exists():
        results = json.loads(gate4.read_text())
        expect(set(results["exception_types"]) <= {"PolicyDenied", "ApprovalDenied", "ApprovalExpired", "PolicyUnavailable"}, f"exceptions: {results['exception_types']}")
        report["gate4_guard_calls"] = results["calls"]
        report["gate4_exception_types"] = results["exception_types"]
        bounds = results["bounds"]
        expect(bounds["timeouts_max_s"] < 1.6 and bounds["wait_broken_s"] < 20 and 50 <= bounds["expiry_min_s"] and bounds["expiry_max_s"] <= 80, f"bounds {bounds}")
        report["bounds_s"] = bounds
    else:
        expect(bool(os.environ.get("POLICY_E2E_STAGES")), "gate 4 output is missing (run stage 4 before 7)")
    for variant in ("approve", "deny", "expiry", "sigint", "capped"):
        path = WORK / f"demo-{variant}.log"
        if path.exists():
            body = path.read_text(errors="replace")
            expect(variant == "sigint" or "Traceback" not in body, f"the {variant} demo crashed")
    report["demo_runs_without_traceback"] = [v for v in ("approve", "deny", "expiry", "capped") if (WORK / f"demo-{v}.log").exists()]

    # 4. the SDK imports only the standard library (an AST audit of the policy modules and the package metadata)
    sdk = ROOT / "packages" / "sdk-python"
    allowed = set(sys.stdlib_module_names) | {"blackbox"}
    outside: list[str] = []
    audited = 0
    for path in sorted((sdk / "src" / "blackbox").glob("*.py")):
        audited += 1
        for node in ast.walk(ast.parse(path.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
            outside += [f"{path.name}:{n}" for n in names if n.split(".")[0] not in allowed]
    expect(not outside, f"imports outside the standard library: {outside}")
    meta = tomllib.loads((sdk / "pyproject.toml").read_text())
    expect(meta["project"].get("dependencies", []) == [], f"runtime dependencies: {meta['project'].get('dependencies')}")
    report["sdk_imports"] = {"modules_audited": audited, "outside_stdlib": 0, "runtime_dependencies": 0}
    emit(**report)


def fail_now(message: str) -> None:
    from policy_e2e_lib import fail  # noqa: PLC0415

    fail(message)


GATES: dict[str, Callable[[], None]] = {"gate2": gate2, "gate5": gate5, "gate7": gate7}

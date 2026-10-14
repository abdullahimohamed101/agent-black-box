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
    for trial in range(12):
        body = intent()
        approval = ask_ok(dev_agent, body)
        approve_call = lambda a=approval: person("admin").post(f"/v1/approvals/{a}/decision", json={"decision": "approve"})  # noqa: E731
        jitter = (trial % 6) * 0.004  # shifts the cancel by 0-20 ms so both orders occur
        cancel_call = lambda a=approval: (time.sleep(jitter), dev_agent.delete(f"/v1/approvals/{a}"))[1]  # noqa: E731
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
    import re  # noqa: PLC0415

    raised = re.findall(r"^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Interrupt|Denied|Expired|Unavailable))\b", text, re.M)
    expect(set(raised) <= {"KeyboardInterrupt"}, f"an exception other than KeyboardInterrupt escaped the interrupted demo: {sorted(set(raised))}")
    final = wait_until("the approval to be cancelled", lambda: (d := detail(approval))["state"] == "CANCELLED" and d, 20)
    expect(final["consumed"] is False, "an interrupted wait spent the approval")
    run_id = final["run_id"]
    wait_until("the cancel event", lambda: event_count(run_id, "approval.cancelled") == 1, 30)
    expect(event_count(run_id, "approval.consumed") == 0 and event_count(run_id, "approval.granted") == 0, "the interrupted run recorded a grant")
    expect(audit_rows(approval, "approval.cancel") == 1, "the cancel is audit-logged once")
    expect(bare_head(workdir / "origin.git") is None, "the push happened")
    report["sigint"] = {"exit_code": code, "seconds_to_exit": round(exit_seconds, 2), "approval": "CANCELLED", "cancelled_events": 1, "keyboard_interrupt": True}

    # 1b. SIGTERM (an orchestrator stopping the agent) is not a Ctrl-C: Python's default action ends the process at once, nothing runs,
    # and the request stays waiting until it expires (KI-153). Evidence for the limit, not a claim of cleanup; a person is told nothing.
    proc, workdir, (log, bare_head, pending_line) = start_demo("sigterm")
    pending = wait_until("the SIGTERM demo to pause", lambda: pending_line(log), 90)
    orphan = pending["approval_id"]
    time.sleep(1.0)
    os.killpg(proc.pid, signal.SIGTERM)
    proc.wait(timeout=30)
    time.sleep(2.0)
    still = detail(orphan)
    expect(still["state"] == "REQUESTED" and still["consumed"] is False, f"after SIGTERM the request is {still['state']}")
    expect(bare_head(workdir / "origin.git") is None, "the push happened after SIGTERM")
    expect(decide("admin", orphan, "deny", "the agent was stopped").status_code == 200, "an operator can close the orphaned request")
    report["sigterm"] = {"exit_code": proc.returncode, "approval_left_waiting": True, "closed_by_an_operator": True}

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


# ------------------------------------------------------------------------------------------------- gate 6


def gate6_prepare() -> None:
    from policy_e2e_check import publish  # noqa: PLC0415

    project = ids()["ui"]
    foreign_project = state()["foreign_project"]
    ui_agent = agent("ui")
    # requests the browser will work on, made by the key the OWNER created
    admin_only = ask_ok(ui_agent, intent(arguments={"remote": "origin", "branch": "main"}))
    self_ok = ask_ok(ui_agent, intent(capability="notes.write", resource="notes/today.md", risk_class=None, arguments={"append": True}))
    aws, password = os.environ["E2E_AWS"], os.environ["E2E_PASSWORD"]
    secret_body = intent(
        capability="db.connect",
        resource=f"postgres://deploy:{password}@db.internal/app?key={aws}",
        risk_class="R3",
        arguments={"aws_access_key_id": aws, "note": "rotate after use", "tag": os.environ["E2E_PLANT"], "env": {"DB_PASSWORD": password}},
    )
    secret = ask_ok(ui_agent, secret_body)
    dev_ok = ask_ok(ui_agent, intent(capability="lint.fix", resource="src", risk_class="R1", arguments={"fix": True}))
    owner_only = ask_ok(ui_agent, intent(capability="billing.change", resource="plan", risk_class="R2", arguments={"plan": "team"}))

    matrix: dict[str, Any] = {}

    def probe(who: str, client: Any, method: str, path: str, want: int, body: Any = None, code: str | None = None) -> None:
        response = client.request(method, path, json=body) if body is not None else client.request(method, path)
        got = (response.status_code, error_code(response))
        ok = got[0] == want and (code is None or got[1] == code)
        expect(ok, f"{who} {method} {path.split('?')[0]} -> {got}, expected {want} {code or ''}")
        matrix.setdefault(who, []).append(f"{method} {path.split('?')[0].replace(admin_only, '{id}')} {got[0]}")

    doc = {"document": "version: 1\npolicies: []\n"}
    dry = {"project_id": project, **doc}
    pub = {"project_id": project, **doc}
    person_routes = [
        ("GET", "/v1/approvals", None), ("GET", "/v1/approvals/pending-count", None), ("GET", f"/v1/approvals/{admin_only}", None),
        ("POST", f"/v1/approvals/{admin_only}/decision", {"decision": "approve"}),
        ("GET", f"/v1/policies/current?project_id={project}", None), ("GET", f"/v1/policies/versions?project_id={project}", None),
        ("POST", "/v1/policies/validate", doc), ("POST", "/v1/policies/dry-run", dry), ("POST", "/v1/policies/publish", pub),
    ]
    for role in ("viewer", "billing"):  # neither reads approvals nor policies, nor decides, nor publishes
        for method, path, body in person_routes:
            probe(role, person(role), method, path, 403, body)
    for method, path, body in person_routes[:3] + person_routes[4:6]:  # a developer reads...
        probe("developer", person("developer"), method, path, 200, body)
    for method, path, body in [person_routes[6], person_routes[7], person_routes[8]]:  # ...but edits nothing
        probe("developer", person("developer"), method, path, 403, body)
    for name in ("ui", "reader", "check_only", "wide"):  # an API key never calls the person routes
        for method, path, body in person_routes:
            probe(f"key:{name}", agent(name), method, path, 403, body)
        for method, path, body in (("GET", "/v1/members", None), ("GET", "/v1/api-keys", None), ("GET", "/v1/audit", None)):
            probe(f"key:{name}", agent(name), method, path, 403, body)
    # a key with no project binding cannot ask (the run's project must be known)
    wide = ask(agent("wide"), intent())
    expect(wide.status_code in (403, 422) and error_code(wide) == "PROJECT_KEY_REQUIRED", f"wide key asked -> {wide.status_code} {error_code(wide)}")
    matrix["key:wide"].append(f"POST /v1/policy/decide {wide.status_code} PROJECT_KEY_REQUIRED")
    # nothing crosses workspaces: the other workspace's admin
    other = person("other_admin")
    for method, path, body in (
        ("GET", f"/v1/approvals/{admin_only}", None), ("POST", f"/v1/approvals/{admin_only}/decision", {"decision": "approve"}),
        ("GET", f"/v1/policies/current?project_id={project}", None), ("GET", f"/v1/policies/versions?project_id={project}", None),
        ("GET", f"/v1/policies/fail-modes?project_id={project}", None),
        ("POST", "/v1/policies/dry-run", dry), ("POST", "/v1/policies/publish", pub),
    ):
        probe("other workspace admin", other, method, path, 404, body)
    listed = other.get("/v1/approvals", params={"state": "all", "limit": 100}).json()["items"]
    expect(all(a["id"] not in (admin_only, self_ok, secret) for a in listed), "another workspace lists this workspace's approvals")
    expect(person("admin").get("/v1/policies/current", params={"project_id": foreign_project}).status_code == 404, "this workspace reads a foreign project's policy")
    me_ids = person("admin").headers["x-abb-workspace"]
    sneaky = person("other_admin").get("/v1/approvals", headers={"x-abb-workspace": me_ids})
    expect(sneaky.status_code in (403, 404), f"other admin with this workspace's header -> {sneaky.status_code}")
    matrix["other workspace admin"].append(f"GET /v1/approvals with the foreign workspace header {sneaky.status_code}")
    # the roles that decide: a developer needs a rule that allows developers; an owner-only rule needs an owner
    r = decide("developer", admin_only, "approve")
    expect((r.status_code, error_code(r)) == (403, "APPROVER_ROLE_REQUIRED"), f"developer on an admin rule -> {r.status_code} {error_code(r)}")
    r = decide("admin", owner_only, "approve")
    expect((r.status_code, error_code(r)) == (403, "APPROVER_ROLE_REQUIRED"), f"admin on an owner-only rule -> {r.status_code} {error_code(r)}")
    r = decide("owner", admin_only, "approve")
    expect((r.status_code, error_code(r)) == (403, "SELF_APPROVAL_FORBIDDEN"), f"owner on their own key's request -> {r.status_code} {error_code(r)}")
    r = decide("owner", dev_ok, "approve", "the owner may not approve their own key's request even if a developer could")
    expect((r.status_code, error_code(r)) == (403, "SELF_APPROVAL_FORBIDDEN"), f"owner on lint.fix -> {r.status_code} {error_code(r)}")
    r = decide("developer", dev_ok, "approve", "developer rule")
    expect(r.status_code == 200, f"developer on a developer rule -> {r.status_code} {error_code(r)}")
    for approval in (admin_only, owner_only):
        expect(detail(approval)["state"] == "REQUESTED", "a refused decision changed the request")
    # an owner whose key it is may approve when the rule allows self approval (the API; the browser does the same below)
    # (kept for the browser: it approves self_ok)
    # the approver's view is scrubbed; so is every stored event
    shown = detail(secret)
    shown_text = json.dumps(shown)
    for value in (aws, password):
        expect(value not in shown_text, "a planted secret is visible to the approver")
    expect(shown["redactions"] >= 1 and not shown["content_withheld"], f"redactions {shown['redactions']} withheld={shown['content_withheld']}")
    events_text = json.dumps(run_events(secret_body["run_id"]))
    for value in (aws, password):
        expect(value not in events_text, "a planted secret is in a stored event")
    # policy edits: owner and admin only, audit-logged
    version = publish("ui", RACES_POLICY_FOR_UI(), "an edit by the admin (policy-e2e)", role="admin")
    expect(version["version"] >= 2, "admin publish")
    audit = person("owner").get("/v1/audit", params={"limit": 200}).json()["items"]
    mine = [a for a in audit if a["action"] == "policy.publish" and a["resource_id"] == project]
    expect(mine and mine[0]["outcome"] == "allowed" and mine[0]["actor_kind"] == "user", f"policy.publish audit row: {mine[:1]}")
    denied = [a for a in audit if a["outcome"] == "denied" and a["action"] in ("policy.write", "policy.publish", "approval.decide", "approval.read", "policy.read")]
    save(gate6={"admin_only": admin_only, "self_ok": self_ok, "secret": secret, "secret_run": secret_body["run_id"], "owner_only": owner_only, "dev_ok": dev_ok}, planted=True)
    save(scan_extra=[aws, password])
    emit(gate=6, phase="api", roles_probed=sorted(matrix), probes=sum(len(v) for v in matrix.values()), refusals_audited=len(denied),
         approver_role_required=True, self_approval_forbidden=True, redactions=shown["redactions"], matrix={k: v[:3] for k, v in matrix.items()})


def RACES_POLICY_FOR_UI() -> str:  # noqa: N802  (a function so the import stays lazy)
    from policy_e2e_check import RACES_POLICY  # noqa: PLC0415

    return RACES_POLICY + "\n# edited by the admin\n"


def gate6_verify() -> None:
    g = state()["gate6"]
    audit = person("owner").get("/v1/audit", params={"limit": 200}).json()["items"]
    decides = [a for a in audit if a["action"] == "approval.decide"]
    refused = [a for a in decides if a["outcome"] == "denied"]
    allowed = [a for a in decides if a["outcome"] == "allowed"]
    reasons = sorted({json.dumps(a["details"].get("reason") or a["details"].get("code") or a["details"], sort_keys=True)[:80] for a in refused})
    expect(len(refused) >= 3, f"audit-logged refusals: {len(refused)} ({reasons})")
    expect(any(a["resource_id"] == g["self_ok"] for a in allowed), "no audit row for the owner's self-approved request")
    text = json.dumps(audit)
    for value in (os.environ["E2E_AWS"], os.environ["E2E_PASSWORD"]):
        expect(value not in text, "a planted secret is in the audit log")
    expect(detail(g["self_ok"])["state"] == "APPROVED", "the self-approved request")
    expect(detail(g["admin_only"])["state"] == "REQUESTED", "the admin-only request must still wait")
    emit(gate=6, phase="verified", refused_decisions_audited=len(refused), allowed_decisions_audited=len(allowed), no_secret_in_audit=True)


# ------------------------------------------------------------------------------------------------- gate 8


def policy_text(rules: list[dict[str, Any]]) -> str:
    """Render rules as policy YAML (hand-written so the text is the same on every machine)."""
    lines = ["version: 1", "policies:"]
    for rule in rules:
        when = ", ".join(f"{k}: {json.dumps(v)}" for k, v in rule["when"].items() if k != "attributes")
        if "attributes" in rule["when"]:
            when += ", attributes: {" + ", ".join(f"{k}: {json.dumps(v)}" for k, v in rule["when"]["attributes"].items()) + "}"
        lines += [f"  - id: {rule['id']}", f"    description: {rule['id'].replace('-', ' ')}.", f"    when: {{{when}}}", f"    decision: {rule['decision']}"]
        if rule["decision"] == "require_approval":
            lines += ["    approver_role: admin", "    expires_in: 15m"]
    return "\n".join(lines) + "\n"


def big_rules() -> list[dict[str, Any]]:
    """60 rules, in an order where matches land at every depth: 16 path denies, 10 approvals, 15 shell allows, 5 R4 denies,
    8 tool denies, then attribute rules (a push to main or production, a force-less `operation`) and a catch-all."""
    rules: list[dict[str, Any]] = []
    for i in range(16):
        rules.append({"id": f"deny-secret-{i}", "when": {"capability": "filesystem.read", "resource": f"**/secret-{i}.txt"}, "decision": "deny"})
    rules.append({"id": "deny-env", "when": {"capability": "filesystem.read", "resource": "**/.env*"}, "decision": "deny"})
    for i in range(10):
        rules.append({"id": f"approve-deploy-{i}", "when": {"capability": f"deploy.t{i}"}, "decision": "require_approval"})
    for i in range(15):
        rules.append({"id": f"allow-tool{i}", "when": {"capability": "shell.execute", "resource": f"tool{i}"}, "decision": "allow"})
    for i in range(5):
        rules.append({"id": f"deny-r4-{i}", "when": {"capability": "shell.execute", "risk_class": "R4", "resource": f"tool{15 + i}"}, "decision": "deny"})
    for i in range(5):
        rules.append({"id": f"deny-tool-{i}", "when": {"capability": f"tool.foo{i}"}, "decision": "deny"})
    rules.append({"id": "deny-tool-glob", "when": {"capability": "tool.my_tool_x"}, "decision": "deny"})
    rules.append({"id": "approve-push-main", "when": {"capability": "github.push", "attributes": {"branch": "main"}}, "decision": "require_approval"})
    rules.append({"id": "approve-push-production", "when": {"capability": "github.push", "attributes": {"branch": "production"}}, "decision": "require_approval"})
    rules.append({"id": "approve-r3-shell", "when": {"capability": "shell.execute", "risk_class": "R3"}, "decision": "require_approval"})
    rules.append({"id": "deny-deletes", "when": {"capability": "filesystem.write", "attributes": {"operation": "deleted"}}, "decision": "deny"})
    rules.append({"id": "allow-reads-src", "when": {"capability": "filesystem.read", "resource": "src/*.py"}, "decision": "allow"})
    rules.append({"id": "approve-prod-db", "when": {"capability": "database.execute", "risk_class": "R3"}, "decision": "require_approval"})
    for i in range(60 - len(rules) - 1):
        rules.append({"id": f"allow-misc-{i}", "when": {"capability": f"misc.c{i}"}, "decision": "allow"})
    rules.append({"id": "deny-everything-else-in-x", "when": {"capability": "x.*"}, "decision": "deny"})
    assert len(rules) == 60, len(rules)
    return rules


def indep_glob(pattern: str, text: str) -> bool:
    """A second implementation of the glob (a regular expression; the server's is a bitset NFA)."""
    import re  # noqa: PLC0415

    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i) and (i == 0 or pattern[i - 1] == "/"):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.fullmatch(out, text, re.S) is not None


def indep_intent(event_type: str, a: dict[str, Any]) -> tuple[str, str, str | None, dict[str, Any]] | None:
    """The intent an event stands for, written from the contract in `abb_api/policies/mapping.py`'s docstring (not its code)."""
    import re  # noqa: PLC0415

    if event_type == "shell.command.started":
        words = [w for w in str(a.get("shell.command", "")).split() if not re.match(r"[A-Za-z_][A-Za-z0-9_]*=", w) and w not in ("sudo", "env", "nohup", "time", "nice")]
        exe = words[0].rsplit("/", 1)[-1][:128] if words else ""
        risk = a.get("shell.risk_class")
        attrs = {"category": a["shell.category"]} if a.get("shell.category") else {}
        command = str(a.get("shell.command", ""))
        if command and len(command) <= 4096:  # the SDK sends the exact command too (review P2-3)
            attrs["command"] = command
        return "shell.execute", exe, risk if risk in ("R0", "R1", "R2", "R3", "R4") else None, attrs
    if event_type == "file.read":
        return "filesystem.read", str(a.get("file.path", "")), None, {}
    if event_type in ("file.created", "file.modified", "file.deleted"):
        return "filesystem.write", str(a.get("file.path", "")), None, {"operation": event_type.split(".")[1]}
    if event_type == "git.push":
        return "github.push", str(a.get("git.push_target", "")), None, ({"branch": a["git.branch"]} if a.get("git.branch") else {})
    if event_type == "tool.call.started":
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", str(a.get("tool.name", "")))[:120].strip(".")
        return (f"tool.{name}", str(a.get("tool.operation", "")), None, {}) if name else None
    return None


def indep_decide(rules: list[dict[str, Any]], intent_: tuple[str, str, str | None, dict[str, Any]]) -> tuple[str, str | None]:
    capability, resource, risk, attrs = intent_
    for rule in rules:
        when = rule["when"]
        want = when.get("capability")
        if want is not None and not (capability == want or (want.endswith(".*") and capability.startswith(want[:-1]))):
            continue
        if "resource" in when and not indep_glob(when["resource"], resource):
            continue
        if "risk_class" in when and risk != when["risk_class"]:
            continue
        if any(attrs.get(k) != v for k, v in when.get("attributes", {}).items()):
            continue
        return rule["decision"], rule["id"]
    return "allow", None


def expected_dry_run(rules: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    from collections import Counter  # noqa: PLC0415

    by_decision: Counter[str] = Counter()
    by_rule: Counter[tuple[str | None, str]] = Counter()
    hits: dict[str, tuple[str, str | None]] = {}
    evaluated = 0
    for event in events:
        mapped = indep_intent(event["event_type"], event.get("attributes", {}))
        if mapped is None:
            continue
        evaluated += 1
        decision, rule = indep_decide(rules, mapped)
        by_decision[decision] += 1
        by_rule[(rule, decision)] += 1
        if decision != "allow":
            hits[event["event_id"]] = (decision, rule)
    return {"evaluated": evaluated, "by_decision": dict(by_decision), "by_rule": dict(by_rule), "hits": hits}


def craft_events(run: str, trace: str, agent_id: str, base: Any, count: int, salt: int) -> list[dict[str, Any]]:
    from datetime import timedelta  # noqa: PLC0415

    from abb_event_schema.ids import IdKind, new_id  # noqa: PLC0415

    events: list[dict[str, Any]] = []
    for n in range(count):
        k = (n + salt) % 30
        kind = n % 6
        if kind == 0:
            event_type, attrs = "file.read", {"file.path": f"src/secret-{k}.txt" if n % 12 else f"config/.env.local" if n % 4 else "README.md"}
        elif kind == 1:
            event_type, attrs = "shell.command.started", {"shell.command": f"FOO=1 sudo /usr/bin/tool{k % 22} --flag", "shell.risk_class": ("R1", "R3", "R4", "R2")[n % 4], "shell.category": "NETWORK"}
        elif kind == 2:
            event_type, attrs = "git.push", {"git.push_target": f"origin {('main', 'dev', 'production')[n % 3]}", "git.branch": ("main", "dev", "production")[n % 3]}
        elif kind == 3:
            event_type, attrs = "tool.call.started", {"tool.name": ("foo0", "foo3", "foo9", "my tool/x", "bar")[n % 5]}
        elif kind == 4:
            event_type, attrs = ("file.created", "file.modified", "file.deleted")[n % 3], {"file.path": f"src/m{n}.py"}
        else:
            event_type, attrs = "file.read", {"file.path": f"src/m{n}.py"}
        events.append(
            {
                "schema_version": "1.0", "event_id": new_id(IdKind.EVENT), "run_id": run, "trace_id": trace, "span_id": new_id(IdKind.SPAN),
                "agent_id": agent_id, "event_type": event_type, "sequence": n + 1,
                "occurred_at": (base + timedelta(milliseconds=n)).isoformat().replace("+00:00", "Z"), "attributes": attrs,
            }
        )
    return events


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def stats_ms(values: list[float]) -> dict[str, float]:
    import statistics  # noqa: PLC0415

    return {"n": len(values), "p50_ms": round(percentile(values, 0.5), 1), "p95_ms": round(percentile(values, 0.95), 1), "p99_ms": round(percentile(values, 0.99), 1),
            "mean_ms": round(statistics.fmean(values), 1), "max_ms": round(max(values), 1)}


def gate8() -> None:
    from datetime import UTC  # noqa: PLC0415

    from abb_event_schema.ids import IdKind, new_id  # noqa: PLC0415
    from policy_e2e_check import publish  # noqa: PLC0415
    from policy_e2e_lib import wait_until  # noqa: PLC0415
    import security_bench as sb  # noqa: PLC0415  (the Phase 13 ingestion benchmark: same builders, same bounds)

    rules = big_rules()
    document = policy_text(rules)
    admin = person("admin")
    valid = admin.post("/v1/policies/validate", json={"document": document}).json()
    expect(valid["valid"] and valid["rule_count"] == 60, f"the 60-rule policy: {valid}")
    report: dict[str, Any] = {"gate": 8, "rules": 60}
    bench_agent = agent("bench")
    bench_project = ids()["bench"]

    # 1. real ingested runs of the project (hand-crafted so the answer is known) against an independent evaluation
    base = datetime.now(UTC)
    ingested: list[dict[str, Any]] = []
    for r in range(3):
        run, trace = new_id(IdKind.RUN), new_id(IdKind.TRACE)
        events = craft_events(run, trace, "dry-run-agent", base, 240, r * 7)
        response = bench_agent.post("/v1/events/batch", json={"events": events})
        expect(response.status_code in (200, 202) and response.json()["accepted"] == len(events), f"ingest -> {response.status_code} {response.text[:150]}")
        ingested += events
    dry = admin.post("/v1/policies/dry-run", json={"project_id": bench_project, "document": document})
    expect(dry.status_code == 200, f"dry run -> {dry.status_code} {dry.text[:200]}")
    got = dry.json()
    want = expected_dry_run(rules, ingested)
    expect(got["truncated"] is None, f"truncated: {got['truncated']}")
    expect(got["runs_considered"] == 3, f"runs considered {got['runs_considered']}")
    expect(got["events_evaluated"] == want["evaluated"], f"events evaluated {got['events_evaluated']} vs independent {want['evaluated']}")
    expect(got["by_decision"] == want["by_decision"], f"by decision {got['by_decision']} vs independent {want['by_decision']}")
    got_rules = {(r["rule_id"], r["decision"]): r["count"] for r in got["by_rule"]}
    expect(got_rules == want["by_rule"], f"by rule differs: {set(got_rules.items()) ^ set(want['by_rule'].items())}")
    samples = got["samples"]
    expect(0 < len(samples) <= got["limits"]["max_samples"], "samples")
    for sample in samples:
        expect(sample["event_id"] in want["hits"] and want["hits"][sample["event_id"]] == (sample["decision"], sample["rule_id"]), "a sample is not what the independent evaluation says")
        expect(set(sample) == {"run_id", "event_id", "capability", "risk_class", "decision", "rule_id"}, f"sample fields {sorted(sample)}")
    text = json.dumps(got)
    for event in ingested:  # a tool name is part of the capability (`tool.<name>`), the policy author's vocabulary; paths, commands and push targets are content
        for key in ("file.path", "shell.command", "git.push_target"):
            value = event["attributes"].get(key)
            expect(value is None or str(value) not in text, f"the dry run carries content: {key}")
    expect("src/" not in text and "config/" not in text and "sudo" not in text and "FOO=1" not in text, "the dry run carries a path or command")
    report["crafted_runs"] = {"runs": 3, "events": len(ingested), "evaluated": got["events_evaluated"], "by_decision": got["by_decision"], "rules_hit": len(got_rules), "samples": len(samples), "matches_independent_evaluation": True}

    # 2. the demo's own recorded runs (project `full`), a draft that differs from the published policy
    starter = (ROOT_STARTER()).read_text()
    draft = starter + "\n  - id: deny-tool-edit\n    description: Editing is paused.\n    when: {capability: tool.edit_file}\n    decision: deny\n"
    import yaml  # noqa: PLC0415  (the independent evaluator reads the draft with PyYAML, not the server's parser)

    draft_rules = [{"id": r["id"], "when": r["when"], "decision": r["decision"]} for r in yaml.safe_load(draft)["policies"]]
    runs = person("owner").get("/v1/runs", params={"project_id": ids()["full"], "limit": 100}).json()["items"]
    events_all: list[dict[str, Any]] = []
    for run in runs:
        events_all += [e for e in run_events(run["run_id"] if "run_id" in run else run["id"]) if not e["event_type"].startswith(("policy.", "approval."))]
    demo_dry = admin.post("/v1/policies/dry-run", json={"project_id": ids()["full"], "document": draft})
    expect(demo_dry.status_code == 200, f"demo dry run -> {demo_dry.status_code} {demo_dry.text[:200]}")
    d = demo_dry.json()
    if runs:
        want_demo = expected_dry_run(draft_rules, events_all)
        expect(d["runs_considered"] == len(runs), f"runs {d['runs_considered']} vs {len(runs)}")
        expect(d["events_evaluated"] == want_demo["evaluated"], f"demo events {d['events_evaluated']} vs {want_demo['evaluated']}")
        expect(d["by_decision"] == want_demo["by_decision"], f"demo by_decision {d['by_decision']} vs {want_demo['by_decision']}")
        expect({(r["rule_id"], r["decision"]): r["count"] for r in d["by_rule"]} == want_demo["by_rule"], "demo by_rule differs")
        expect(want_demo["by_decision"].get("deny", 0) >= 1 and want_demo["by_decision"].get("require_approval", 0) >= 1 or not os.environ.get("POLICY_E2E_STAGES"), "the demo runs hold no denied or approval-needing action")
        report["demo_runs"] = {"runs": len(runs), "evaluated": d["events_evaluated"], "by_decision": d["by_decision"], "matches_independent_evaluation": True}
    for sample in d["samples"]:
        expect(set(sample) == {"run_id", "event_id", "capability", "risk_class", "decision", "rule_id"}, "demo sample fields")
    expect("origin main" not in json.dumps(d) and "session.py" not in json.dumps(d), "the demo dry run carries content")

    # 3. latency through real HTTP: the 60-rule policy published for the bench project
    publish("bench", document, "60 rules (policy-e2e benchmark)")
    run_ids = [new_run() for _ in range(10)]
    plan: list[dict[str, Any]] = []
    for i in range(1000):
        kind = i % 3
        if kind == 0:
            plan.append({"capability": "filesystem.read", "resource": f"src/secret-{i % 25}.txt", "risk_class": None, "arguments": {}})
        elif kind == 1:
            plan.append({"capability": "shell.execute", "resource": f"tool{i % 20}", "risk_class": "R1", "arguments": {"category": "NETWORK"}})
        else:
            plan.append({"capability": f"tool.foo{i % 12}", "resource": "", "risk_class": None, "arguments": {}})

    def one(item: dict[str, Any], i: int, client: Any) -> float:
        body = intent(run_ids[i % 10], agent_id="bench-agent", **item)
        started = time.perf_counter()
        response = client.post("/v1/policy/decide", json=body)
        elapsed = (time.perf_counter() - started) * 1000
        expect(response.status_code == 200 and response.json()["decision"] in ("allow", "deny"), f"decide -> {response.status_code} {response.text[:120]}")
        return elapsed

    for i in range(30):  # warm up (connection, caches)
        one(plan[i], i, bench_agent)
    sequential = [one(plan[i], i, bench_agent) for i in range(500)]
    report["decide_sequential"] = stats_ms(sequential)

    def worker(offset: int) -> list[float]:
        import httpx as _httpx  # noqa: PLC0415

        with _httpx.Client(base_url=bench_agent.base_url, headers=dict(bench_agent.headers), timeout=60) as c:
            return [one(plan[(offset + j) % 1000], offset + j, c) for j in range(250)]

    wall = time.perf_counter()
    with ThreadPoolExecutor(4) as pool:
        concurrent = [x for chunk in pool.map(worker, [0, 250, 500, 750]) for x in chunk]
    wall = time.perf_counter() - wall
    report["decide_4_concurrent"] = {**stats_ms(concurrent), "requests_per_second": round(len(concurrent) / wall)}

    # 4. ingestion batch latency alone, then while four callers hammer the decision endpoint
    reader = agent("reader")
    floor = sb.measure_floor(reader)
    writer = sb.Writer(bench_agent.base_url.__str__().rstrip("/"), state()["keys"]["bench"])
    writer.post([sb.event(new_id(IdKind.RUN), new_id(IdKind.TRACE), 1, "tool", datetime.now(UTC), payload=sb.payload_1kb(0))])
    alone = sb.measure_batches(writer, 20)
    stop = threading.Event()
    loads: list[float] = []

    def hammer(offset: int) -> None:
        import httpx as _httpx  # noqa: PLC0415

        with _httpx.Client(base_url=bench_agent.base_url, headers=dict(bench_agent.headers), timeout=60) as c:
            j = 0
            while not stop.is_set():
                loads.append(one(plan[(offset + j) % 1000], offset + j, c))
                j += 1

    threads = [threading.Thread(target=hammer, args=(o,)) for o in (0, 250, 500, 750)]
    for t in threads:
        t.start()
    under = sb.measure_batches(writer, 20)
    stop.set()
    for t in threads:
        t.join(30)
    report["ingestion_batch_100"] = {"alone": alone, "while_4_callers_decide": under, "decides_during": len(loads), "machine_floor_ms": round(floor, 1)}
    # generous bounds (shared CI runners): scaled up on a machine whose trivial request is slower than the reference
    scale = max(1.0, floor / 5.0)
    bounds = {"decide_p50_ms": 50.0 * scale, "decide_p95_ms": 250.0 * scale, "decide_p99_ms": 800.0 * scale, "concurrent_p95_ms": 800.0 * scale,
              "ingest_p50_ms": 250.0 * scale, "ingest_p95_ms": 1000.0 * scale}
    measured = {"decide_p50_ms": report["decide_sequential"]["p50_ms"], "decide_p95_ms": report["decide_sequential"]["p95_ms"], "decide_p99_ms": report["decide_sequential"]["p99_ms"],
                "concurrent_p95_ms": report["decide_4_concurrent"]["p95_ms"], "ingest_p50_ms": alone["p50_ms"], "ingest_p95_ms": alone["p95_ms"]}
    over = {k: (measured[k], round(bounds[k], 1)) for k in bounds if measured[k] > bounds[k]}
    expect(not over, f"over the generous bound (measured, bound): {over}")
    # ingestion must not regress because decisions are being asked: under load within a generous factor of alone
    expect(under["p50_ms"] <= max(3 * alone["p50_ms"], 250.0 * scale) and under["p95_ms"] <= 1500.0 * scale, f"ingestion under decide load: {under} vs alone {alone}")
    report["bounds"] = {k: round(v, 1) for k, v in bounds.items()}
    Path_write_json(report)
    emit(**report)
    wait_until("bench settle", lambda: True, 1)


def ROOT_STARTER() -> Any:  # noqa: N802
    from policy_e2e_check import STARTER  # noqa: PLC0415

    return STARTER


def Path_write_json(report: dict[str, Any]) -> None:  # noqa: N802
    from policy_e2e_lib import WORK  # noqa: PLC0415

    (WORK / "gate8.json").write_text(json.dumps(report))
    target = os.environ.get("POLICY_E2E_REPORT")
    if target:
        with open(target, "w") as handle:
            json.dump(report, handle, indent=2)


# ------------------------------------------------------------------------------------------- real-stack UI


def ui_prepare() -> None:
    from datetime import UTC  # noqa: PLC0415

    from abb_event_schema.ids import IdKind, new_id  # noqa: PLC0415
    from policy_e2e_check import RACES_POLICY, publish  # noqa: PLC0415

    ui_agent = agent("ui")
    # recorded runs for the dry run: actions of every kind, so a draft denies some and sends some for approval
    events: list[dict[str, Any]] = []
    for r in range(2):
        run, trace = new_id(IdKind.RUN), new_id(IdKind.TRACE)
        batch = craft_events(run, trace, "ui-agent", datetime.now(UTC), 60, r * 5)
        response = ui_agent.post("/v1/events/batch", json={"events": batch})
        expect(response.status_code in (200, 202) and response.json()["accepted"] == len(batch), "ingest")
        events += batch
    # a policy with a few versions, so the history and the diff have something to show
    current = person("owner").get("/v1/policies/current", params={"project_id": ids()["ui"]}).json()["policy"]["version"]
    for n in range(2):
        current = publish("ui", RACES_POLICY + f"\n# revision {n} (policy-e2e)\n", f"revision {n}", base=current)["version"]
    done = ask_ok(ui_agent, intent(capability="lint.fix", resource="src", risk_class="R1", arguments={"fix": True}))
    expect(decide("admin", done, "approve", "Formatting only.").status_code == 200, "a decided request for the Decided tab")
    waiting_body = intent(arguments={"remote": "origin", "branch": "main", "for": "ui"})
    waiting = ask_ok(ui_agent, waiting_body)
    save(ui={"run": waiting_body["run_id"], "approval": waiting, "version": current, "events": len(events)})
    emit(ui_prepare={"events": len(events), "policy_version": current, "waiting_approval": waiting})


def expiry_probe() -> None:
    from datetime import UTC  # noqa: PLC0415

    client = agent("races")
    body = intent(capability="test.short", resource="probe", risk_class=None, arguments={"n": 1})
    approval = ask_ok(client, body)
    asked = datetime.now(UTC)
    rounds = []
    while True:
        started = datetime.now(UTC)
        r = client.get(f"/v1/approvals/{approval}/wait", params={"timeout": 25}, timeout=40)
        ended = datetime.now(UTC)
        rounds.append((started.strftime("%H:%M:%S.%f")[:-3], ended.strftime("%H:%M:%S.%f")[:-3], r.json()["state"]))
        if r.json()["state"] != "REQUESTED":
            break
    d = detail(approval)
    events = [(e["event_type"], e["occurred_at"]) for e in run_events(body["run_id"]) if e["event_type"].startswith("approval.")]
    emit(probe=True, asked=asked.isoformat(), rounds=rounds, expires_at=d["expires_at"], closed_at=d["closed_at"], events=events)


GATES: dict[str, Callable[[], None]] = {"expiry-probe": expiry_probe, "ui-prepare": ui_prepare, "gate2": gate2, "gate5": gate5, "gate7": gate7, "gate6-prepare": gate6_prepare, "gate6-verify": gate6_verify, "gate8": gate8}

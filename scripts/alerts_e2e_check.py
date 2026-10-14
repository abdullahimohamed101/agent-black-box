"""API-side half of scripts/alerts-e2e.sh (Phase 17 step 4). Run with `uv run --project apps/api python`.

Subcommands, in the order the script calls them (each prints PASS/FAIL lines with the observed values and exits 1 if one failed):

    provision        projects, keys, the approval policy
    gate1-start      destinations (webhook, Slack, email) and one rule per trigger; every trigger is caused through the real API / SDK
    gate1-finish     each trigger fired exactly once to every destination; signed, current, content-free bodies
    gate2            receiver down -> backoff -> delivered once; dead letter; auto-pause; resume; retry storm
    gate3            outbound safety: refusals at save, a rebinding name, link-local, IPv6, redirects, send-test
    gate6            roles, API keys, a second workspace, audit
    gate7-start/-finish   dedupe, cooldown, rate limit, daily cap (the cap needs the rate window to pass)
    gate8            ingestion latency without and with enabled rules
    ui-fire          one failed run for the rule the browser created
    gate4            a planted sentinel is nowhere in any alert artefact, row or log
    gate5            no secret in the database in clear; masked; shown once; HMAC verified, stale secret rejected
    summary          the pass/fail table (exit 1 if anything failed)

Secrets (signing secrets, URL tokens, keys) are held in the state file / memory only and never printed.
"""

import json
import os
import platform
import secrets as pysecrets
import statistics
import subprocess
import sys
import time
from typing import Any

import alerts_e2e_lib as L
from alerts_e2e_lib import (
    FLAKY_PORT,
    MAIN_PORT,
    PLANT,
    ROOT,
    SLACK_PORT,
    WORK,
    check,
    die,
    gate,
    history,
    info,
    new_destination,
    new_rule,
    person,
    post_run,
    projects,
    recv_mail,
    recv_mode,
    recv_requests,
    save,
    state,
    wait_alerts,
    wait_until,
    web_url,
)

PROJECT_SLUGS = os.environ.get("E2E_PROJECTS", "").split()
HOOK = "hooks.example.test"
SLACK = "slack.example.test"
POLICY = """\
version: 1
policies:
  - id: expiring-soon
    description: A short-lived approval (alert about to expire).
    when: {capability: test.expiring}
    decision: require_approval
    approver_role: admin
    expires_in: 2m
  - id: decided-quickly
    description: An approval a person decides at once.
    when: {capability: test.decided}
    decision: require_approval
    approver_role: admin
    expires_in: 15m
"""
SEVERITY = {"info": 0, "warning": 1, "medium": 2, "high": 3, "critical": 4}


def token(n: int = 12) -> str:
    return pysecrets.token_hex(n)


def hook_url(key: str, listener_port: int = MAIN_PORT, host: str = HOOK) -> str:
    return web_url(host, listener_port, "hook", f"{key}-{token(10)}")


def hook(name: str, key: str, port: int = MAIN_PORT, host: str = HOOK) -> dict[str, Any]:
    url = hook_url(key, port, host)
    # the receiver is addressed by the full last path segment (key plus token), so register under that
    full = url.rsplit("/", 1)[-1]
    created = new_destination(name, "webhook", url, key=full)
    created["key"] = full
    return created


def ask(client: Any, capability: str, note: str) -> str:
    body = {
        "run_id": L.ids()["run_id"], "agent_id": "coding-agent", "capability": capability, "resource": f"x-{note}",
        "risk_class": None, "arguments": {"note": note},
    }
    response = client.post("/v1/policy/decide", json=body)
    if response.status_code != 200 or response.json().get("decision") != "require_approval":
        die(f"policy decide -> {response.status_code} {response.text[:160]}")
    return str(response.json()["approval_id"])


# ------------------------------------------------------------------------------------------------ provision


def provision() -> None:
    gate("provision")
    owner = person("owner")
    by_slug = {p["slug"]: p["id"] for p in owner.get("/v1/projects").json()["items"]}
    check(all(s in by_slug for s in PROJECT_SLUGS), "projects exist", projects=sorted(by_slug))
    keys = {s: L.create_key(owner, f"agent-{s}", ["events:write", "artifacts:write"], by_slug[s]) for s in PROJECT_SLUGS}
    keys["reader"] = L.create_key(owner, "script-read", ["runs:read"], None)
    foreign = person("other_admin")
    foreign_projects = {p["slug"]: p["id"] for p in foreign.get("/v1/projects").json()["items"]}
    keys["foreign"] = L.create_key(foreign, "agent-foreign", ["events:write"], foreign_projects["foreign"])
    save(projects=by_slug, keys=keys, foreign_project=foreign_projects["foreign"])
    published = owner.post("/v1/policies/publish", json={"document": POLICY, "comment": "alerts-e2e", "project_id": by_slug["trig"]})
    check(published.status_code == 201, "approval policy published for the trig project", status=published.status_code)
    receivers = L._recv.get("/state")  # noqa: SLF001
    check(receivers.status_code == 200, "receivers answer on the control port")
    L.finish()


# ------------------------------------------------------------------------------------------------ gate 1

TRIGGER_RULES = ("approval_requested", "approval_expiring", "security_finding", "run_failed", "spend_over")


def run_sdk_plant(key: str) -> str:
    """The real Python SDK and its coding helpers plant a run (secret file read, credentials, risky git, an off-list host)."""
    plant = WORK / "plant.json"
    plant.write_text(json.dumps({"plant": PLANT, "aws": os.environ["E2E_AWS"], "pem_body": os.environ["E2E_PEM_BODY"]}))
    plant.chmod(0o600)
    env = {"HOME": os.environ["HOME"], "PATH": os.environ["PATH"], "BLACKBOX_API_KEY": key, "BLACKBOX_ENDPOINT": L.API}
    done = subprocess.run(  # noqa: S603
        ["uv", "run", "--project", "packages/sdk-python", "python", "scripts/security_e2e_plant.py", "planted", str(plant)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=240, check=False,
    )
    if done.returncode != 0:
        die(f"SDK planted run failed (exit {done.returncode}): {done.stderr[-300:]}")
    return str(json.loads(done.stdout.strip().splitlines()[-1])["run_id"])


def gate1_start() -> None:
    gate("gate1")
    p = projects()
    d_hook = hook("g1 webhook", "g1")
    d_slack = new_destination("g1 slack", "slack", web_url(SLACK, SLACK_PORT, "slack", f"g1-{token(10)}"))
    recipient = f"ops-{token(4)}@alerts.test"
    d_mail = new_destination("g1 email", "email", recipients=[recipient])
    dest = [d_hook["id"], d_slack["id"], d_mail["id"]]
    check(d_hook["signing_secret"] is not None and d_slack["signing_secret"] is None, "a webhook gets a signing secret, Slack does not")
    rules = {
        "approval_requested": new_rule("g1 approval requested", "approval_requested", dest, project="trig"),
        "approval_expiring": new_rule("g1 approval about to expire", "approval_expiring", dest, project="trig",
                                      filters={"expiring_lead_seconds": 60}),
        "security_finding": new_rule("g1 security finding", "security_finding", dest, project="trig", filters={"min_severity": "medium"}),
        "run_failed": new_rule("g1 run failed", "run_failed", dest, project="trig"),
        "spend_over": new_rule("g1 spend over", "spend_over", dest, project="spend", filters={"threshold_usd": 0.01, "period": "day"}),
    }
    keys = state()["keys"]
    from httpx import Client  # noqa: PLC0415
    agent = Client(base_url=L.API, headers={"Authorization": f"Bearer {keys['trig']}"}, timeout=60, limits=L.NO_KEEPALIVE)
    started = time.time()
    ap_exp = ask(agent, "test.expiring", PLANT)
    ap_long = ask(agent, "test.decided", f"{PLANT}-long")  # stays pending: requested -> one alert, never 'about to expire' (15 min)
    ap_dec = ask(agent, "test.expiring", f"{PLANT}-quick")  # a 2-minute approval that a person decides at once
    decided = person("admin").post(f"/v1/approvals/{ap_dec}/decision", json={"decision": "approve", "comment": "ok"})
    check(decided.status_code == 200, "one approval is decided at once", status=decided.status_code)
    runs = {k: post_run("trig", k, sentinel=PLANT) for k in ("FAILED", "TIMED_OUT", "BLOCKED", "SUCCESS")}
    planted = run_sdk_plant(keys["trig"])
    spend_run = post_run("spend", "SUCCESS", cost=0.02, sentinel=PLANT)
    save(g1={
        "dest": {"hook": d_hook["id"], "slack": d_slack["id"], "mail": d_mail["id"]}, "hook_key": d_hook["key"], "slack_key": state()["urls"][d_slack["id"]].rsplit("/", 1)[-1], "recipient": recipient,
        "rules": {k: v["id"] for k, v in rules.items()}, "approvals": {"expiring": ap_exp, "decided": ap_dec, "long": ap_long}, "runs": runs,
        "planted": planted, "spend_run": spend_run, "started": started,
    })
    info(approvals_requested=3, runs=runs, planted_run=planted, rule_ids=list(rules.values()).__len__())
    L.finish()


def gate1_finish() -> None:
    gate("gate1")
    g = state()["g1"]
    owner = person("owner")
    for run_id in [*g["runs"].values(), g["planted"], g["spend_run"]]:
        L.wait_until(f"run {run_id[:12]} summarised", lambda r=run_id: L.run_current(r), 60)
    findings = L.wait_until("findings of the planted run", lambda: _findings(g["planted"]), 60)
    wanted = {f["id"] for f in findings if SEVERITY.get(f["severity"], -1) >= SEVERITY["medium"]}
    check(len(wanted) >= 2, "the planted run has security findings at or above medium", total=len(findings), at_or_above=len(wanted))
    expected = {
        # the decided one is announced too: the alert is about the request, whenever the job ran (review P2-3)
        "approval_requested": {g["approvals"]["expiring"], g["approvals"]["long"], g["approvals"]["decided"]},
        "approval_expiring": {g["approvals"]["expiring"]},
        "security_finding": wanted,
        "run_failed": {g["runs"]["FAILED"], g["runs"]["TIMED_OUT"], g["runs"]["BLOCKED"]},
        "spend_over": {None},
    }
    # the 'about to expire' alert comes ~60 s after the approval was requested (2 min expiry, 60 s lead)
    wait_for = max(5.0, g["started"] + 66 - time.time())
    info(waiting_for_the_expiry_window_seconds=round(wait_for, 1))
    dest_ids = g["dest"]
    total = sum(len(v) for v in expected.values())

    def all_in() -> bool:
        return all(len(history(rule_id=rid, status="delivered")) >= len(expected[t]) * 3 for t, rid in g["rules"].items())
    def where() -> dict[str, Any]:
        out: dict[str, Any] = {"expected": {t: len(v) * 3 for t, v in expected.items()}}
        for t, rid in g["rules"].items():
            tally: dict[str, int] = {}
            for r in history(rule_id=rid):
                k = f"{r['status']}/{r['error_code'] or r['skip_reason'] or '-'}"
                tally[k] = tally.get(k, 0) + 1
            out[t] = tally
        out["sweep_jobs"] = L.sweep_jobs()
        out["worker_log_failures"] = L.worker_log_lines("job failed", "alert check failed")[-5:]
        return out

    L.wait_until("every alert delivered to every destination", all_in, 150 + wait_for, diag=where)
    L.settle(4)  # then prove nothing more arrives
    info(sweep_jobs=L.sweep_jobs(), worker_log_failures=L.worker_log_lines("job failed", "alert check failed")[-5:])
    for trigger, rid in g["rules"].items():
        rows = history(rule_id=rid)
        subjects: dict[str, list[Any]] = {}
        for r in rows:
            subject = r.get({"security_finding": "finding_id", "run_failed": "run_id"}.get(trigger, "approval_id" if trigger.startswith("approval") else "_none"))
            subjects.setdefault(r["destination"]["id"], []).append(subject)
        per_dest = {d: sorted(map(str, v)) for d, v in subjects.items()}
        ok_sets = all(set(v) == expected[trigger] for v in subjects.values()) and set(subjects) == set(dest_ids.values())
        once = all(len(v) == len(set(v)) == len(expected[trigger]) for v in subjects.values())
        check(ok_sets and once, f"{trigger}: each of {len(expected[trigger])} subject(s) alerted exactly once to each of 3 destinations",
              rows=len(rows), statuses=sorted({r["status"] for r in rows}), per_destination=len(per_dest))
    success_rows = [r for r in history() if r.get("run_id") == g["runs"]["SUCCESS"]]
    check(not success_rows, "a successful run alerts nothing")
    decided_expiring = [r for r in history(trigger="approval_expiring") if r.get("approval_id") == g["approvals"]["decided"]]
    long_expiring = [r for r in history(trigger="approval_expiring") if r.get("approval_id") == g["approvals"]["long"]]
    check(not decided_expiring and not long_expiring,
          "an approval that was decided is never reported as about to expire, and a 15-minute one is not 'about to expire' yet")
    # one more spend run: the rule fires once per UTC day
    post_run("spend", "SUCCESS", cost=0.03)
    L.settle(6)
    spend_rows = history(rule_id=g["rules"]["spend_over"])
    check(len(spend_rows) == 3, "spend over the threshold fires once per day per destination (a second costly run adds none)", rows=len(spend_rows))

    # the receivers' side
    hook_rows = {r["id"]: r for r in history(destination_id=dest_ids["hook"])}
    bodies = recv_requests(key=g["hook_key"])
    alert_ids = [b["alert_id"] for b in bodies]
    extra = [(json.loads(b["body"])["kind"], json.loads(b["body"])["subject"], json.loads(b["body"])["details"], json.loads(b["body"])["created_at"]) for b in bodies
             if b["alert_id"] not in hook_rows]
    if extra:
        info(webhook_alerts_not_in_history=extra, approvals=g["approvals"], started=g["started"], now=time.time())
    check(len(alert_ids) == len(set(alert_ids)) == total and set(alert_ids) == set(hook_rows),
          "the signed webhook receiver saw each alert id once, equal to the history's delivered ids", requests=len(bodies), expected=total)
    check(all(b["signature_ok"] is True and b["timestamp_ok"] is True for b in bodies), "every webhook body verified: HMAC valid, timestamp fresh",
          valid=sum(b["signature_ok"] is True for b in bodies))
    parsed = [json.loads(b["body"]) for b in bodies]
    check({p["kind"] for p in parsed} == set(TRIGGER_RULES), "webhook bodies cover all five triggers", kinds=sorted({p["kind"] for p in parsed}))
    states = {p["subject"].get("approval_id"): p["details"].get("approval_state") for p in parsed if p["kind"] == "approval_requested"}
    check(states.get(g["approvals"]["decided"]) in ("REQUESTED", "APPROVED", "DENIED", "EXPIRED", "CANCELLED") and states.get(g["approvals"]["long"]) == "REQUESTED",
          "the quickly decided approval is announced as requested, with the state the job saw in the details (a race decides which)", states=states)
    check(all((p.get("link") or "").startswith(os.environ["E2E_ORIGIN"]) for p in parsed), "every webhook body links back to the app", sample=parsed[0]["link"])
    slack_rows = history(destination_id=dest_ids["slack"])
    slack_requests = recv_requests(key=g["slack_key"], listener="slack")
    slack_ids = [r["alert_id"] for r in slack_requests]
    repeated = {a: [(r["attempt"], round(r["at"], 2)) for r in slack_requests if r["alert_id"] == a] for a in set(slack_ids) if slack_ids.count(a) > 1}
    check(len(slack_requests) == len(slack_rows) == total and set(slack_ids) == {r["id"] for r in slack_rows},
          "the Slack-shaped receiver got one request per alert", requests=len(slack_requests), repeated=repeated,
          not_in_history=[(r["alert_id"], r["attempt"], r["path"][:14], r["body"][:60]) for r in slack_requests if r["alert_id"] not in {x["id"] for x in slack_rows}],
          history_statuses=sorted({x["status"] for x in slack_rows}))
    check(all("text" in json.loads(r["body"]) for r in slack_requests), "Slack bodies are Slack-shaped (text)")
    mail_rows = history(destination_id=dest_ids["mail"])
    mail = [m for m in recv_mail() if g["recipient"] in " ".join(m["to"])]
    check(len(mail) == len(mail_rows) == total and {m["alert_id"] for m in mail} == {r["id"] for r in mail_rows},
          "the SMTP sink got one email per alert, X-ABB-Alert-Id equal to the history ids", messages=len(mail))
    check(all(os.environ["E2E_ORIGIN"] in m["raw"] for m in mail), "every email links back to the app")
    info(by_trigger={t: len(v) for t, v in expected.items()}, per_destination=total, webhook_requests=len(bodies), slack_requests=len(slack_requests),
         emails=len(mail))
    L.finish()


def _findings(run_id: str) -> list[dict[str, Any]] | None:
    response = person("owner").get(f"/v1/runs/{run_id}/findings")
    if response.status_code != 200 or response.json()["state"]["status"] != "current":
        return None
    items = [i for i in response.json()["items"] if i["category"] == "security"]
    return items or None


# ------------------------------------------------------------------------------------------------ gate 2


def attempts_of(alert_id: str) -> dict[str, Any]:
    return next(r for r in history() if r["id"] == alert_id)


def gate2() -> None:
    gate("gate2")
    # (a) the receiver is down: bounded retries with backoff, then delivered once with the same alert id
    flaky = hook("g2 flaky", "g2flaky", FLAKY_PORT)
    new_rule("g2 flaky", "run_failed", [flaky["id"]], project="rel", filters={"agent": "ag-flaky"})
    L.recv_listener("flaky", False)
    run = post_run("rel", "FAILED", "ag-flaky")
    first = wait_alerts("a first failed attempt", 1, statuses=("pending",), destination_id=flaky["id"])[0]
    alert_id = first["id"]
    timeline: list[tuple[float, int]] = []
    t0 = time.monotonic()
    def reached() -> bool:
        a = attempts_of(alert_id)["attempts"]
        if not timeline or timeline[-1][1] != a:
            timeline.append((round(time.monotonic() - t0, 1), a))
        return a >= 2
    wait_until("a second attempt (backoff)", reached, 30, every=0.2)
    L.recv_listener("flaky", True)  # back before the cap (4 attempts)
    wait_alerts("delivery once the receiver is back", 1, statuses=("delivered",), destination_id=flaky["id"])
    final = attempts_of(alert_id)
    seen = recv_requests(key=flaky["key"])
    check(final["status"] == "delivered" and 2 <= final["attempts"] <= 4, "receiver down: retried with backoff, delivered after it came back",
          attempts=final["attempts"], attempt_timeline=timeline, run=run[:12])
    check(len(seen) == 1 and seen[0]["alert_id"] == alert_id and seen[0]["signature_ok"] is True,
          "the receiver saw the alert once, under the same alert_id, signed", requests=len(seen), attempt_header=seen[0]["attempt"])

    # (b) a receiver that keeps failing: dead letter at the cap, then auto-pause, then resume
    dead = hook("g2 dead", "g2dead")
    new_rule("g2 dead", "run_failed", [dead["id"]], project="rel", filters={"agent": "ag-dead"})
    recv_mode(dead["key"], "500")
    post_run("rel", "FAILED", "ag-dead")
    row1 = wait_alerts("a dead letter", 1, 60, statuses=("dead",), destination_id=dead["id"])[0]
    requests_one = len(recv_requests(key=dead["key"]))
    check(row1["attempts"] == 4 and row1["error_code"] == "http_5xx" and requests_one == 4,
          "a failing receiver: exactly 4 attempts (the cap), then a dead letter with a fixed code", attempts=row1["attempts"],
          code=row1["error_code"], requests=requests_one)
    post_run("rel", "FAILED", "ag-dead")
    row2 = wait_alerts("the second alert closed by the pause", 1, 60, statuses=("skipped",), destination_id=dead["id"])[0]
    dest = person("owner").get(f"/v1/alerts/destinations/{dead['id']}").json()
    requests_two = len(recv_requests(key=dead["key"]))
    check(dest["state"] == "paused" and dest["paused_reason"] == "failures" and row2["skip_reason"] == "destination_paused",
          "6 consecutive failures auto-pause the destination; the rest of the alert is skipped", state=dest["state"], failures=dest["consecutive_failures"],
          requests_total=requests_two)
    check(requests_two == 6, "no request was made after the pause (6 total = 4 + 2)", requests=requests_two)
    audit = [a for a in L.audit_rows() if a["action"] == "alert.destination.auto_pause"]
    check(len(audit) >= 1 and audit[0]["actor_kind"] == "system", "the auto-pause is audited as a system action", rows=len(audit))
    recv_mode(dead["key"], "ok")
    resumed = person("owner").patch(f"/v1/alerts/destinations/{dead['id']}", json={"state": "active"})
    check(resumed.status_code == 200 and resumed.json()["state"] == "active" and resumed.json()["consecutive_failures"] == 0,
          "resuming clears the failure count")
    post_run("rel", "FAILED", "ag-dead")
    wait_alerts("delivery after resume", 1, 60, destination_id=dead["id"])
    L.settle(2)

    # (c) retry storm: many alerts, one failing destination
    storm = hook("g2 storm", "g2storm")
    new_rule("g2 storm", "run_failed", [storm["id"]], project="rel", filters={"agent": "ag-storm"})
    recv_mode(storm["key"], "500")
    n = 30
    start = time.monotonic()
    for _ in range(n):
        post_run("rel", "FAILED", "ag-storm")
    def settled() -> bool:
        rows = history(destination_id=storm["id"])
        return len(rows) >= n and all(r["status"] in ("dead", "skipped") for r in rows)
    wait_until("the storm to settle", settled, 120)
    L.settle(2)
    rows = history(destination_id=storm["id"])
    requests = len(recv_requests(key=storm["key"]))
    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r["status"] + (":" + r["skip_reason"] if r["skip_reason"] else "")] = by_status.get(r["status"] + (":" + r["skip_reason"] if r["skip_reason"] else ""), 0) + 1
    storm_state = person("owner").get(f"/v1/alerts/destinations/{storm['id']}").json()["state"]
    check(requests <= 6 and storm_state == "paused", f"retry storm: {n} alerts to one failing destination made {requests} requests (bound: the pause at 6)",
          requests=requests, statuses=by_status, seconds=round(time.monotonic() - start, 1), destination=storm_state)
    check(len(rows) == n, "every alert is accounted for in the history", rows=len(rows))

    # (d) a receiver that answers too slowly: the 2 s deadline (ABB_ALERTS_REQUEST_TIMEOUT_SECONDS) ends the attempt with a fixed code
    slow = hook("g2 slow", "g2slow")
    recv_mode(slow["key"], "slow")
    began = time.monotonic()
    result = person("owner").post(f"/v1/alerts/destinations/{slow['id']}/test").json()
    elapsed = time.monotonic() - began
    check(result == {"ok": False, "error_code": "timeout"} and elapsed < 4.5,
          "a slow receiver is cut off at the 2 s deadline with the fixed code `timeout`", result=result, seconds=round(elapsed, 1))
    L.finish()


# ------------------------------------------------------------------------------------------------ gate 3

REFUSED = [
    ("plain HTTP", "http://hooks.example.test:8213/hook/x"),
    ("IPv4 loopback", "https://127.0.0.1/hook/x"),
    ("localhost", "https://localhost/hook/x"),
    ("IPv6 loopback", "https://[::1]/hook/x"),
    ("IPv4-mapped IPv6 loopback", "https://[::ffff:127.0.0.1]/hook/x"),
    ("cloud metadata address", "https://169.254.169.254/latest/meta-data/"),
    ("link-local", "https://169.254.1.1/hook/x"),
    ("private 10/8", "https://10.0.0.5/hook/x"),
    ("private 192.168/16", "https://192.168.1.1/hook/x"),
    ("private 172.16/12", "https://172.16.0.1/hook/x"),
    ("internal name", "https://metadata.google.internal/hook/x"),
    ("decimal IP spelling", "https://2130706433/hook/x"),
    ("hex IP spelling", "https://0x7f.1/hook/x"),
    ("credentials in the URL", "https://user:pw@hooks.example.test/hook/x"),
]


def gate3() -> None:
    gate("gate3")
    owner = person("owner")
    before = len(owner.get("/v1/alerts/destinations").json()["items"])
    outcomes = {}
    for label, url in REFUSED:
        response = owner.post("/v1/alerts/destinations", json={"name": f"refused {label}", "type": "webhook", "target": {"url": url}})
        outcomes[label] = response.status_code
        echoed = url.split("//", 1)[1].split("/", 1)[0] in response.text and "hooks.example.test" not in url
        check(response.status_code in (400, 422) and not echoed, f"refused at save: {label}", status=response.status_code,
              code=(response.json().get("error") or {}).get("code") if response.headers.get("content-type", "").startswith("application/json") else None)
    after = len(owner.get("/v1/alerts/destinations").json()["items"])
    check(before == after, "no refused destination was stored", destinations=after)

    # names that look public when saved and are not when delivered (the sender resolves at delivery)
    d_ok = hook("g3 control", "g3ok")
    d_rebind = hook("g3 rebind", "g3rebind", host="rebind.example.test")
    d_meta = hook("g3 metadata", "g3meta", host="meta.example.test")
    d_v6 = hook("g3 ipv6", "g3v6", host="v6.example.test")
    d_redirect = hook("g3 redirect", "g3redirect", host="redirect.example.test")
    first = owner.post(f"/v1/alerts/destinations/{d_rebind['id']}/test").json()
    check(first["ok"] is True and len(recv_requests(key=d_rebind["key"])) == 1,
          "a name that resolves publicly at save time is accepted and reachable (send-test hit the receiver once)", result=first)
    recv_mode(d_redirect["key"], "302")
    dests = [d_ok["id"], d_rebind["id"], d_meta["id"], d_v6["id"], d_redirect["id"]]
    new_rule("g3 safety", "run_failed", dests, project="safety")
    L.net_set(**{"rebind.example.test": ["10.0.0.9"], "meta.example.test": ["169.254.169.254"], "v6.example.test": ["::1"]})
    run = post_run("safety", "FAILED")
    rows = {r["destination"]["id"]: r for r in wait_alerts("every destination closed", 5, 60, statuses=("delivered", "dead"), run_id=run)}
    check(rows[d_ok["id"]]["status"] == "delivered", "the control destination still receives the alert", status=rows[d_ok["id"]]["status"])
    for label, d in (("public at save, private (10.0.0.9) at delivery", d_rebind), ("link-local / cloud metadata (169.254.169.254)", d_meta),
                     ("IPv6 loopback (::1)", d_v6)):
        row = rows[d["id"]]
        extra = 1 if d is d_rebind else 0
        seen = len(recv_requests(key=d["key"]))
        check(row["status"] == "dead" and row["error_code"] == "blocked_address" and row["attempts"] == 1 and seen == extra,
              f"refused at delivery, no connection made: {label}", code=row["error_code"], attempts=row["attempts"], receiver_requests=seen)
    redirect_row = rows[d_redirect["id"]]
    followed = recv_requests(key=d_redirect["key"])
    check(redirect_row["status"] == "dead" and redirect_row["error_code"] == "http_3xx" and redirect_row["attempts"] == 1 and len(followed) == 1
          and not any(r["path"].startswith("/redirected") for r in recv_requests()),
          "a redirect is a failure and is never followed", code=redirect_row["error_code"], requests=len(followed))
    after_flip = owner.post(f"/v1/alerts/destinations/{d_rebind['id']}/test").json()
    check(after_flip == {"ok": False, "error_code": "blocked_address"}, "send-test refuses the rebound name as well", result=after_flip)
    redirect_test = owner.post(f"/v1/alerts/destinations/{d_redirect['id']}/test").json()
    check(redirect_test["error_code"] == "http_3xx", "send-test does not follow redirects either", result=redirect_test)
    L.net_set(**{"rebind.example.test": [L.PUBLIC_IP], "meta.example.test": [L.PUBLIC_IP], "v6.example.test": [L.PUBLIC_IP]})
    L.finish()


# ------------------------------------------------------------------------------------------------ gate 6


def status_of(response: Any) -> int:
    return int(response.status_code)


def gate6() -> None:
    gate("gate6")
    owner, admin = person("owner"), person("admin")
    d = hook("g6 destination", "g6own")
    rule = new_rule("g6 rule", "run_failed", [d["id"]], project="authz")
    rid, did = rule["id"], d["id"]
    rule_body = {"name": "x", "trigger": "run_failed", "destination_ids": [did]}
    # people who may not manage
    matrix: dict[str, dict[str, int]] = {}
    for role in ("developer", "viewer", "security", "billing"):
        c = person(role)
        reads_ok = role != "billing"
        got = {
            "GET rules": status_of(c.get("/v1/alerts/rules")), "GET rule": status_of(c.get(f"/v1/alerts/rules/{rid}")),
            "GET preview": status_of(c.get(f"/v1/alerts/rules/{rid}/preview")), "GET history": status_of(c.get("/v1/alerts/notifications")),
            "GET destinations": status_of(c.get("/v1/alerts/destinations")), "GET destination": status_of(c.get(f"/v1/alerts/destinations/{did}")),
            "GET history by destination": status_of(c.get("/v1/alerts/notifications", params={"destination_id": did})),
            "POST destination": status_of(c.post("/v1/alerts/destinations", json={"name": "n", "type": "webhook", "target": {"url": hook_url("nope")}})),
            "PATCH destination": status_of(c.patch(f"/v1/alerts/destinations/{did}", json={"name": "renamed"})),
            "POST test": status_of(c.post(f"/v1/alerts/destinations/{did}/test")), "DELETE destination": status_of(c.delete(f"/v1/alerts/destinations/{did}")),
            "POST rule": status_of(c.post("/v1/alerts/rules", json=rule_body)), "PATCH rule": status_of(c.patch(f"/v1/alerts/rules/{rid}", json={"enabled": False})),
            "DELETE rule": status_of(c.delete(f"/v1/alerts/rules/{rid}")),
        }
        matrix[role] = got
        expected = {k: (200 if k.startswith("GET") and k in ("GET rules", "GET rule", "GET preview", "GET history") and reads_ok else 403) for k in got}
        check(got == expected, f"{role}: reads rules and history ({'yes' if reads_ok else 'no: billing holds no run access'}), never destinations or secrets, changes nothing",
              denied=sum(v == 403 for v in got.values()), allowed=sum(v == 200 for v in got.values()))
    still = owner.get(f"/v1/alerts/rules/{rid}").json()
    check(still["enabled"] is True and still["name"] == "g6 rule" and owner.get(f"/v1/alerts/destinations/{did}").json()["name"] == "g6 destination",
          "the refused changes changed nothing")
    # what a reader sees of a rule and the history
    viewer_rule = person("viewer").get(f"/v1/alerts/rules/{rid}").json()
    check(viewer_rule["destinations"] == [] and viewer_rule["destination_count"] == 1, "a viewer sees the destination count, never the destinations")
    run = post_run("authz", "FAILED")
    wait_alerts("the authz alert", 1, 60, run_id=run)
    for role in ("viewer", "developer", "security", "admin", "owner"):
        rows = history(role, run_id=run)
        hidden = role in ("viewer", "developer", "security")
        shape_ok = bool(rows) and all((r["destination"]["id"] is None and r["destination"]["name"] is None) == hidden and r["destination"]["type"] == "webhook" for r in rows)
        check(shape_ok, f"{role}: history rows {'withhold' if hidden else 'show'} the destination id and name", rows=len(rows))
    check(all(r["approval_id"] is None for r in history("viewer")), "a viewer's history never carries an approval id")
    # API keys hold no alert action
    results = {}
    for name in ("authz", "reader"):
        c = L.key_client(name)
        results[name] = [status_of(c.get("/v1/alerts/rules")), status_of(c.get("/v1/alerts/notifications")), status_of(c.get("/v1/alerts/destinations")),
                         status_of(c.post("/v1/alerts/destinations", json={"name": "n", "type": "webhook", "target": {"url": hook_url("nope")}})),
                         status_of(c.post("/v1/alerts/rules", json=rule_body))]
    check(all(s == 403 for v in results.values() for s in v), "API keys (ingest and runs:read) get 403 on every alert endpoint", statuses=results)
    # a second workspace
    foreign = person("other_admin")
    fd = new_destination("g6 foreign destination", "webhook", hook_url("g6foreign"), key=None, role="other_admin")
    f_url_key = state()["urls"][fd["id"]].rsplit("/", 1)[-1]
    f_rule = foreign.post("/v1/alerts/rules", json={"name": "g6 foreign rule", "trigger": "run_failed", "destination_ids": [fd["id"]],
                                                    "project_id": state()["foreign_project"]})
    check(f_rule.status_code == 201, "the other workspace creates its own destination and rule", status=f_rule.status_code)
    foreign_seen = {r["id"] for r in foreign.get("/v1/alerts/rules").json()["items"]}
    check(rid not in foreign_seen and all(r["name"].startswith("g6 foreign") for r in foreign.get("/v1/alerts/rules").json()["items"]),
          "the other workspace sees none of our rules", seen=len(foreign_seen))
    cross = {
        "GET destination": foreign.get(f"/v1/alerts/destinations/{did}"), "PATCH destination": foreign.patch(f"/v1/alerts/destinations/{did}", json={"name": "taken"}),
        "DELETE destination": foreign.delete(f"/v1/alerts/destinations/{did}"), "POST test": foreign.post(f"/v1/alerts/destinations/{did}/test"),
        "GET rule": foreign.get(f"/v1/alerts/rules/{rid}"), "PATCH rule": foreign.patch(f"/v1/alerts/rules/{rid}", json={"enabled": False}),
        "DELETE rule": foreign.delete(f"/v1/alerts/rules/{rid}"), "GET preview": foreign.get(f"/v1/alerts/rules/{rid}/preview"),
        "rule on our destination": foreign.post("/v1/alerts/rules", json={"name": "steal", "trigger": "run_failed", "destination_ids": [did]}),
    }
    check(all(r.status_code in (404, 422) for r in cross.values()) and all(r.status_code == 404 for k, r in cross.items() if k != "rule on our destination"),
          "ids of another workspace are 404 for every verb (never 403, never a leak)", statuses={k: r.status_code for k, r in cross.items()})
    check(len(foreign.get("/v1/alerts/notifications").json()["items"]) == 0 and still["name"] == "g6 rule",
          "the other workspace's history is empty and nothing of ours changed")
    own_key = hook_key_of(did)
    mine_before = len(recv_requests(key=own_key))
    theirs_before = len(recv_requests(key=f_url_key))
    foreign_run = L.post_run("foreign", "FAILED")
    wait_until("foreign delivery", lambda: len(recv_requests(key=f_url_key)) > theirs_before, 60)
    L.settle(3)
    check(len(recv_requests(key=f_url_key)) == theirs_before + 1 and len(recv_requests(key=own_key)) == mine_before,
          "a failed run in the other workspace alerts only the other workspace", foreign_run=foreign_run[:12])
    post_run("authz", "FAILED")
    wait_until("our delivery", lambda: len(recv_requests(key=own_key)) > mine_before, 60)
    L.settle(3)
    check(len(recv_requests(key=f_url_key)) == theirs_before + 1 and len(recv_requests(key=own_key)) == mine_before + 1,
          "a failed run in our workspace alerts only us")
    # every change is audited: do each kind once as an admin and find it in the audit log
    a = hook("g6 admin destination", "g6admin")
    steps = [
        admin.patch(f"/v1/alerts/destinations/{a['id']}", json={"name": "g6 admin renamed"}),
        admin.patch(f"/v1/alerts/destinations/{a['id']}", json={"state": "paused"}),
        admin.patch(f"/v1/alerts/destinations/{a['id']}", json={"state": "active"}),
        admin.patch(f"/v1/alerts/destinations/{a['id']}", json={"rotate_signing_secret": True}),
        admin.post(f"/v1/alerts/destinations/{a['id']}/test"),
    ]
    r2 = admin.post("/v1/alerts/rules", json={"name": "g6 admin rule", "trigger": "run_failed", "destination_ids": [a["id"]], "project_id": projects()["authz"]})
    steps += [r2, admin.patch(f"/v1/alerts/rules/{r2.json()['id']}", json={"enabled": False}), admin.delete(f"/v1/alerts/rules/{r2.json()['id']}"),
              admin.delete(f"/v1/alerts/destinations/{a['id']}")]
    check(all(s.status_code in (200, 201, 204) for s in steps), "an admin can manage (rename, pause, resume, rotate, test, rules, delete)",
          statuses=[s.status_code for s in steps])
    actions = {x["action"] for x in L.audit_rows()}
    need = {"alert.destination.create", "alert.destination.update", "alert.destination.pause", "alert.destination.resume", "alert.destination.rotate_secret",
            "alert.destination.test", "alert.destination.delete", "alert.rule.create", "alert.rule.update", "alert.rule.delete"}
    check(need <= actions, "every kind of change is in the audit log", missing=sorted(need - actions))
    unauthorised = [x for x in L.audit_rows() if x["action"].startswith("alert.") and x.get("actor_id") in (None, "")]
    check(not unauthorised, "every alert audit row names its actor")
    L.finish()


def hook_key_of(destination_id: str) -> str:
    return state()["urls"][destination_id].rsplit("/", 1)[-1]


# ------------------------------------------------------------------------------------------------ gate 7


def gate7_start() -> None:
    gate("gate7")
    cases = {"dedupe": "ag-dedupe", "cool": "ag-cool", "rate": "ag-rate", "cap": "ag-cap"}
    made: dict[str, Any] = {}
    for name, agent in cases.items():
        d = hook(f"g7 {name}", f"g7{name}")
        r = new_rule(f"g7 {name}", "run_failed", [d["id"]], project="noise", filters={"agent": agent}, cooldown=600 if name == "cool" else 0)
        made[name] = {"destination": d["id"], "key": d["key"], "rule": r["id"], "agent": agent}
    # dedupe: the same run seen again (late events, a repeated final event, an identical batch) never alerts again
    run_id, events = L.run_events("FAILED", "ag-dedupe")
    L.post_events("noise", events)
    wait_alerts("the first alert", 1, 60, run_id=run_id)
    again = L.post_events("noise", events)  # identical event ids: idempotent
    late = [L.event({"run_id": run_id, "trace_id": events[0]["trace_id"]}, 99, "tool.call.completed", "ag-dedupe", status="success", duration_ms=1,
                    attributes={"tool.name": "shell"}),
            L.event({"run_id": run_id, "trace_id": events[0]["trace_id"]}, 100, "run.failed", "ag-dedupe", span_id=..., status="error")]
    L.post_events("noise", late)
    L.settle(1)
    L.wait_until("the run summarised again", lambda: L.run_current(run_id), 60)
    L.settle(4)
    rows = history(run_id=run_id)
    seen = recv_requests(key=made["dedupe"]["key"])
    check(again.status_code == 202 and len(rows) == 1 and len(seen) == 1,
          "dedupe: re-sending the batch, a late event and a second terminal event re-summarise the run and alert nothing more",
          history_rows=len(rows), receiver_requests=len(seen))
    # cooldown: three failures in a row, one alert
    for _ in range(3):
        post_run("noise", "FAILED", "ag-cool")
    # rate limit: a burst of 60 against 40 a minute
    for _ in range(60):
        post_run("noise", "FAILED", "ag-rate")
    # daily cap, first half: 40 now, 40 more after the minute window has passed (cap 60 a day)
    for _ in range(40):
        post_run("noise", "FAILED", "ag-cap")
    save(g7={**made, "burst1": time.time()})
    info(cooldown_runs=3, rate_burst=60, cap_burst_one=40)
    L.finish()


def counts(destination: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in history(destination_id=destination):
        k = r["status"] + (f":{r['skip_reason']}" if r["skip_reason"] else "")
        out[k] = out.get(k, 0) + 1
    return out


def settled(destination: str, total: int) -> bool:
    rows = history(destination_id=destination)
    return len(rows) >= total and all(r["status"] in ("delivered", "skipped", "dead") for r in rows)


def gate7_finish() -> None:
    gate("gate7")
    g = state()["g7"]
    wait_until("cooldown destination settled", lambda: settled(g["cool"]["destination"], 3), 90)
    wait_until("rate-limited destination settled", lambda: settled(g["rate"]["destination"], 60), 120)
    wait_until("cap destination settled (burst one)", lambda: settled(g["cap"]["destination"], 40), 90)
    cool, rate = counts(g["cool"]["destination"]), counts(g["rate"]["destination"])
    check(cool == {"delivered": 1, "skipped:cooldown": 2} and len(recv_requests(key=g["cool"]["key"])) == 1,
          "cooldown: three failures inside the cooldown send one alert; the other two are recorded as skipped (cooldown)", counts=cool)
    check(rate == {"delivered": 40, "skipped:rate_limited": 20} and len(recv_requests(key=g["rate"]["key"])) == 40,
          "rate limit: a burst of 60 reaches the destination 40 times (limit 40 a minute); 20 are recorded as skipped (rate_limited)", counts=rate,
          receiver_requests=len(recv_requests(key=g["rate"]["key"])))
    wait_for = g["burst1"] + 63 - time.time()
    if wait_for > 0:
        info(waiting_for_the_rate_window_seconds=round(wait_for, 1))
        time.sleep(wait_for)
    for _ in range(40):
        post_run("noise", "FAILED", "ag-cap")
    wait_until("cap destination settled (burst two)", lambda: settled(g["cap"]["destination"], 80), 120)
    cap = counts(g["cap"]["destination"])
    requests = len(recv_requests(key=g["cap"]["key"]))
    check(cap == {"delivered": 60, "skipped:daily_cap": 20} and requests == 60,
          "daily cap: 80 alerts over two minutes reach the destination 60 times (cap 60 a day); 20 are recorded as skipped (daily_cap)", counts=cap, receiver_requests=requests)
    L.finish()


# ------------------------------------------------------------------------------------------------ gate 8


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def gate8() -> None:
    gate("gate8")
    batches, size = 30, 100

    def phase() -> tuple[list[float], float]:
        client = L.key_client("bench")
        times, runs = [], []
        began = time.perf_counter()
        for _ in range(batches):
            run_id, events = L.run_events("FAILED", "ag-bench", filler=size - 2)
            runs.append(run_id)
            body = {"events": events}
            t = time.perf_counter()
            response = client.post("/v1/events/batch", json=body)
            times.append((time.perf_counter() - t) * 1000)
            if response.status_code != 202:
                die(f"bench ingest -> {response.status_code}")
        for run_id in runs:
            wait_until("bench run summarised", lambda r=run_id: L.run_current(r), 120, every=0.1)
        return times, time.perf_counter() - began

    d = hook("g8 destination", "g8")
    rules = [new_rule("g8 run failed", "run_failed", [d["id"]], project="bench", filters={"agent": "ag-bench"}, cooldown=3600),
             new_rule("g8 approvals", "approval_requested", [d["id"]], project="bench"),
             new_rule("g8 expiring", "approval_expiring", [d["id"]], project="bench", filters={"expiring_lead_seconds": 600}),
             new_rule("g8 findings", "security_finding", [d["id"]], project="bench", filters={"min_severity": "info"}),
             new_rule("g8 spend", "spend_over", [d["id"]], project="bench", filters={"threshold_usd": 1000000.0, "period": "day"}),
             new_rule("g8 failures 2", "run_failed", [d["id"]], project="bench", filters={"agent": "ag-bench"}, cooldown=3600)]
    owner = person("owner")

    def set_enabled(on: bool) -> None:
        for rule in rules:
            owner.patch(f"/v1/alerts/rules/{rule['id']}", json={"enabled": on}).raise_for_status()

    load_before = os.getloadavg()
    off: list[float] = []
    on: list[float] = []
    drain = {"off": [], "on": []}
    for _ in range(2):  # interleaved, so drift hits both equally
        set_enabled(False)
        t, wall = phase()
        off += t
        drain["off"].append(wall)
        set_enabled(True)
        t, wall = phase()
        on += t
        drain["on"].append(wall)
    load_after = os.getloadavg()
    row = lambda xs: {"n": len(xs), "p50_ms": round(percentile(xs, 0.5), 1), "p95_ms": round(percentile(xs, 0.95), 1), "max_ms": round(max(xs), 1),  # noqa: E731
                      "mean_ms": round(statistics.fmean(xs), 1)}
    result = {"batch_events": size, "batches_per_phase": batches, "rounds": 2, "rules_disabled": row(off), "rules_enabled": row(on),
              "drain_seconds_per_phase": {k: [round(x, 1) for x in v] for k, v in drain.items()},
              "enabled_rules": 6, "machine": f"{platform.platform()} {platform.machine()}, {os.cpu_count()} cpus",
              "load_avg_before": [round(x, 2) for x in load_before], "load_avg_after": [round(x, 2) for x in load_after]}
    (WORK / "bench.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    a, b = result["rules_disabled"], result["rules_enabled"]
    check(b["p50_ms"] <= 1.5 * a["p50_ms"] + 10 and b["p95_ms"] <= 2 * a["p95_ms"] + 50,
          "ingestion batch latency with 6 enabled rules stays within the stated bound (p50 <= 1.5x + 10 ms, p95 <= 2x + 50 ms)",
          without=f"p50 {a['p50_ms']} / p95 {a['p95_ms']} ms", with_rules=f"p50 {b['p50_ms']} / p95 {b['p95_ms']} ms")
    L.finish()


# ------------------------------------------------------------------------------------------------ browser support


def ui_fire() -> None:
    gate("ui")
    owner = person("owner")
    rule = next((r for r in owner.get("/v1/alerts/rules").json()["items"] if r["name"] == "UI failed runs"), None)
    check(rule is not None and rule["trigger"] == "run_failed", "the rule created in the browser exists", name="UI failed runs")
    if rule is None:
        L.finish()
        return
    run = post_run("ui", "FAILED", "ui-agent", sentinel=PLANT)
    rows = wait_alerts("the browser-created rule to deliver", 1, 60, rule_id=rule["id"])
    check(rows[0]["run_id"] == run, "the alert for the failed run was delivered to the destination created in the browser", status=rows[0]["status"])
    save(ui_run=run, ui_rule=rule["id"])
    L.finish()


# ------------------------------------------------------------------------------------------------ gate 4


def all_alert_text() -> dict[str, str]:
    """Everything an alert produced or exposed: what receivers got, every role's history, the audit log, API responses."""
    served = recv_requests()
    mail = recv_mail()
    out = {
        "webhook and Slack bodies": "\n".join(r["body"] for r in served),
        "emails": "\n".join(m["raw"] for m in mail),
        "audit log (API)": json.dumps(L.audit_rows(), default=str),
    }
    for role in ("owner", "admin", "developer", "viewer", "security"):
        out[f"history ({role})"] = json.dumps(history(role), default=str)
    out["rules (owner)"] = json.dumps(person("owner").get("/v1/alerts/rules").json())
    out["destinations (owner)"] = json.dumps(person("owner").get("/v1/alerts/destinations").json())
    return out


def gate4() -> None:
    gate("gate4")
    needles = [PLANT, os.environ["E2E_AWS"], os.environ["E2E_PEM_BODY"], PLANT.lower()]
    texts = all_alert_text()
    delivered = len(recv_requests()) + len(recv_mail())
    check(delivered > 50, "there is a lot to scan: webhook, Slack and email deliveries", deliveries=delivered)
    for where, text in texts.items():
        hits = [n[:12] for n in needles if n.lower() in text.lower()]
        check(not hits, f"no planted content in {where}", bytes=len(text))
    tables = ["alert_notifications", "alert_rules", "alert_rule_destinations", "alert_destinations", "audit_log", "outbox_jobs"]
    hits, columns, rows = L.scan_database(needles, tables)
    check(all(not v for v in hits.values()), "no planted content in the alert tables, the audit table or the job queue", columns=columns, rows=rows)
    control, _, event_rows = L.scan_database([PLANT], ["events"])
    check(bool(control[PLANT]), "positive control: the sentinel IS stored in the events table (a path, a command, a host, an argument, error text)",
          columns=sorted(set(control[PLANT]))[:5], event_rows=event_rows)
    logs = L.log_text()
    check(not any(n.lower() in logs.lower() for n in needles), "no planted content in the API, worker or receiver logs", log_bytes=len(logs))
    # the same runs show the sentinel to people who may read content (so the absence above is the alert's doing, not the data's)
    run = state()["g1"]["runs"]["FAILED"]
    events = person("owner").get(f"/v1/runs/{run}/events", params={"limit": 500}).json()["items"]
    check(PLANT in json.dumps(events), "positive control: the failed run's events do contain the sentinel for an owner")
    L.finish()


# ------------------------------------------------------------------------------------------------ gate 5


def gate5() -> None:
    gate("gate5")
    st = state()
    urls, signing = st["urls"], st["secrets"]
    ui_file = os.environ.get("E2E_UI_SECRET_FILE", "")
    ui_secret = open(ui_file).read().strip() if ui_file and os.path.exists(ui_file) else None
    url_tokens = sorted({u.rsplit("/", 1)[-1].split("-", 1)[-1] for u in urls.values()} | {os.environ["E2E_UI_TOKEN"]})
    full = sorted(set(urls.values()))
    secrets_ = sorted(set(signing.values()) | ({ui_secret} if ui_secret else set()))
    recipients = [st["g1"]["recipient"]]
    needles = url_tokens + secrets_ + recipients  # a full URL contains its token, so the tokens find it too
    check(len(url_tokens) >= 10 and len(secrets_) >= 8, "secrets under test", url_tokens=len(url_tokens), signing_secrets=len(secrets_), recipients=len(recipients),
          ui_secret_included=ui_secret is not None)
    hits, columns, rows = L.scan_database(needles)
    found = {k[:10] + "…": v for k, v in hits.items() if v}
    check(not found, "no destination URL token, URL, signing secret or recipient is stored in clear anywhere in the database", columns=columns, rows=rows, found=found)
    control, _, _ = L.scan_database(["g1 webhook"], None)
    check(any(c.startswith("alert_destinations.") for c in control["g1 webhook"]),
          "positive control: the scan does find a plain value (the destination name)", columns=sorted(set(control["g1 webhook"])))
    blob = L.db("select octet_length(config_encrypted) as n, substr(encode(config_encrypted, 'hex'), 1, 8) as head from alert_destinations limit 3")
    info(ciphertext_sample=[{"bytes": b["n"], "head": b["head"]} for b in blob])
    # masked in responses
    owner = person("owner")
    listing = owner.get("/v1/alerts/destinations")
    detail = owner.get(f"/v1/alerts/destinations/{st['g1']['dest']['hook']}")
    history_text = json.dumps(history())
    texts = {"destination list": listing.text, "destination detail": detail.text, "history": history_text, "rules": owner.get("/v1/alerts/rules").text,
             "audit log": json.dumps(L.audit_rows(), default=str), "logs": L.log_text()}
    for where, text in texts.items():
        leaked = [n[:10] for n in needles if n in text]
        check(not leaked, f"no URL token, URL or signing secret in {where}")
    check("signing_secret" not in detail.json() and all("signing_secret" not in i for i in listing.json()["items"]),
          "the signing secret is not in a second GET (shown once, at creation)")
    masked = [i["masked_target"] for i in listing.json()["items"] if i["type"] == "webhook"][:3]
    check(all("…" in m or m.count("/") <= 2 for m in masked) and all(not any(t in m for t in url_tokens) for m in masked),
          "the target is masked in the API", sample=masked)
    # HMAC at the receiver; a stale secret fails; rotation gives a new secret once
    g = st["g1"]
    bodies = recv_requests(key=g["hook_key"])
    check(bodies and all(b["signature_ok"] is True for b in bodies), "the receiver verified the HMAC of every alert it was sent", verified=len(bodies))
    d = hook("g5 rotate", "g5rot")
    first = owner.post(f"/v1/alerts/destinations/{d['id']}/test").json()
    old = state()["secrets"][d["id"]]
    rotated = owner.patch(f"/v1/alerts/destinations/{d['id']}", json={"rotate_signing_secret": True}).json()
    new = rotated["signing_secret"]
    check(new and new != old and "signing_secret" not in owner.get(f"/v1/alerts/destinations/{d['id']}").json(), "rotation returns a new secret once", )
    owner.post(f"/v1/alerts/destinations/{d['id']}/test")
    stale = recv_requests(key=d["key"])[-1]
    L.recv_secret(d["key"], new)
    owner.post(f"/v1/alerts/destinations/{d['id']}/test")
    fresh = recv_requests(key=d["key"])[-1]
    check(first["ok"] and stale["signature_ok"] is False and fresh["signature_ok"] is True,
          "after rotation the old secret no longer verifies at the receiver and the new one does (the receiver really checks)",
          stale=stale["signature_ok"], fresh=fresh["signature_ok"])
    L.finish()


# ------------------------------------------------------------------------------------------------ summary


# Worker log lines that are expected somewhere in the suite (none today: a failed job or a failed scheduled check is a bug).
ALLOWED_WORKER_FAILURES: tuple[str, ...] = ()


def summary() -> None:
    gate("suite")
    bad_lines = [ln for ln in L.worker_log_lines("job failed", "alert check failed") if not any(a in ln for a in ALLOWED_WORKER_FAILURES)]
    check(not bad_lines, "the worker log has no 'job failed' or 'alert check failed' line", lines=bad_lines[:5], count=len(bad_lines))
    rows = [json.loads(line) for line in open(os.environ["E2E_RESULTS"]) if line.strip()]
    by_gate: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_gate.setdefault(r["gate"], []).append(r)
    print("\nALERTS-E2E RESULTS")
    bad = 0
    for name in ("provision", "gate1", "gate2", "gate3", "gate4", "gate5", "gate6", "gate7", "gate8", "ui", "suite"):
        group = by_gate.get(name, [])
        if not group:
            continue
        failed = [r for r in group if not r["ok"]]
        bad += len(failed)
        print(f"  {name:10} {len(group) - len(failed):3}/{len(group):<3} {'PASS' if not failed else 'FAIL'}")
        for r in failed:
            print(f"      FAIL {r['label']}")
    if bad:
        raise SystemExit(1)


COMMANDS = {
    "provision": provision, "gate1-start": gate1_start, "gate1-finish": gate1_finish, "gate2": gate2, "gate3": gate3, "gate4": gate4,
    "gate5": gate5, "gate6": gate6, "gate7-start": gate7_start, "gate7-finish": gate7_finish, "gate8": gate8, "ui-fire": ui_fire, "summary": summary,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        raise SystemExit(__doc__)
    COMMANDS[sys.argv[1]]()

"""Gates 5 and 6 of scripts/hardening-e2e.sh, and the data the browser spec needs.

Gate 5 (ADR-156/157): sessions through the real API (list, revoke one, revoke the others; a revoked session stops at once, through the
other API process too, and an open stream ends), no users row for an un-invited sign-in (checked after the browser signs in), inviter
demotion revokes their open invitations while the owner's stay. The __Host- login cookie is covered by scripts/auth-https-e2e.sh.
Gate 6 (ADR-158): an alert destination on a disallowed port is refused at save and, for a row saved while a wider list was in force,
at delivery with blocked_port; one actor (and one workspace) cannot take all the search permits of a process.
"""

import asyncio
import json
import os
import threading
import time
from typing import Any

import httpx

import hardening_e2e_lib as lib
from hardening_e2e_lib import check, gate, info


def _cookie_client(token: str, base: str, workspace_from: str = "owner") -> httpx.Client:
    """A second, independent session of the same person (own cookie, same workspace)."""
    client = httpx.Client(base_url=base, cookies={"abb_session": token}, headers={"Origin": lib.ORIGIN}, timeout=30, limits=lib.NO_KEEPALIVE)
    client.headers["x-abb-workspace"] = lib.person(workspace_from, base).headers["x-abb-workspace"]
    return client


def _own_id(client: httpx.Client) -> str:
    rows = client.get("/v1/auth/sessions").json()["items"]
    return next(r["id"] for r in rows if r["current"])


# ------------------------------------------------------------------------------------------------ gate 5


def gate5() -> None:
    gate("gate5-identity")
    a, b = lib.main_urls()
    address = lib.email("developer")
    tokens = [lib.session_token(address) for _ in range(4)]
    s1, s2, s3, s4 = (_cookie_client(t, a, "developer") for t in tokens)
    ids = {n: _own_id(c) for n, c in (("s1", s1), ("s2", s2), ("s3", s3), ("s4", s4))}
    listing = s1.get("/v1/auth/sessions").json()["items"]
    check(len(listing) >= 4 and sum(1 for r in listing if r["current"]) == 1 and next(r for r in listing if r["current"])["id"] == ids["s1"],
          "sessions: the list holds every session of the person and marks exactly one as current", count=len(listing))
    blob = json.dumps(listing)
    check(all(t not in blob for t in tokens), "sessions: the list never carries a cookie value")
    mine = s1.delete(f"/v1/auth/sessions/{ids['s1']}")
    check(mine.status_code == 409 and mine.json()["error"]["code"] == "CURRENT_SESSION", "sessions: the session in use is not revocable here (409 CURRENT_SESSION)")
    stranger = lib.person("owner", a).get("/v1/auth/sessions").json()["items"][0]["id"]
    foreign = s1.delete(f"/v1/auth/sessions/{stranger}")
    unknown = s1.delete("/v1/auth/sessions/00000000-0000-4000-8000-000000000000")
    check(foreign.status_code == unknown.status_code == 404 and lib.scrub(foreign.json()) == lib.scrub(unknown.json()), "sessions: another person's session and an unknown id are the same 404")

    # an open stream on s3 (a live run), then revoke s3 through the OTHER process
    project = lib.new_project("identity")
    key = lib.new_key(project, ["events:write"], "identity")
    run = lib.run_ids()
    started = httpx.post(f"{a}/v1/events/batch", json=lib.batch_of([lib.event(run, 1), lib.event(run, 2)]), headers=lib.bearer(key), timeout=30)
    check(started.status_code == 202, "a live run exists for the stream")
    stream_state: dict[str, Any] = {"events": 0, "ended_at": None, "status": None}

    def read_stream() -> None:
        try:
            with httpx.stream("GET", f"{a}/v1/runs/{run['run_id']}/stream", cookies={"abb_session": tokens[2]}, headers={"Origin": lib.ORIGIN, "x-abb-workspace": s3.headers["x-abb-workspace"]}, timeout=httpx.Timeout(60, read=60)) as response:
                stream_state["status"] = response.status_code
                for line in response.iter_lines():
                    if line.startswith("data:") or line.startswith("event:"):
                        stream_state["events"] += 1
        except httpx.HTTPError as error:
            stream_state["error"] = type(error).__name__
        stream_state["ended_at"] = time.monotonic()

    thread = threading.Thread(target=read_stream, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while stream_state["status"] is None and time.monotonic() < deadline:
        time.sleep(0.1)
    time.sleep(1.5)
    check(stream_state["status"] == 200 and stream_state["ended_at"] is None, "an open stream is running on the third session", status=stream_state["status"])
    s1b = _cookie_client(tokens[0], b, "developer")  # the same person's first session, spoken to the second process
    before = time.monotonic()
    revoked = s1b.delete(f"/v1/auth/sessions/{ids['s3']}")
    check(revoked.status_code == 204, "sessions: revoke one (through the other API process)", status=revoked.status_code)
    for label, client in (("process a", s3), ("process b", _cookie_client(tokens[2], b, "developer"))):
        check(client.get("/v1/me").status_code == 401, f"sessions: the revoked session stops working at once ({label})")
    thread.join(20)
    ended_after = (stream_state["ended_at"] or time.monotonic()) - before
    info(stream=stream_state, ended_after_seconds=round(ended_after, 1))
    check(stream_state["ended_at"] is not None and ended_after <= 8, "sessions: the revoked session's OPEN STREAM ends (re-checked every 2 s here)", seconds=round(ended_after, 1))
    # revoke the others
    result = s1.post("/v1/auth/sessions/revoke-others")
    check(result.status_code == 200 and result.json()["revoked"] >= 2, "sessions: sign out everywhere else", revoked=result.json())
    check(s2.get("/v1/me").status_code == 401 and s4.get("/v1/me").status_code == 401 and s1.get("/v1/me").status_code == 200, "sessions: the others stop at once and this session keeps working")
    rows = [r["action"] for r in lib.audit_rows()]
    check("session.revoke" in rows and "session.revoke_others" in rows, "sessions: both actions are in the workspace audit log", counts={a: rows.count(a) for a in ("session.revoke", "session.revoke_others")})

    # inviter demotion
    inviter = lib.person("inviter", a)
    owner = lib.person("owner", a)
    first = inviter.post("/v1/invitations", json={"email": f"by-inviter-{os.environ['E2E_SUFFIX']}@guest.test", "role": "VIEWER"})
    second = owner.post("/v1/invitations", json={"email": f"by-owner-{os.environ['E2E_SUFFIX']}@guest.test", "role": "VIEWER"})
    check(first.status_code == 201 and second.status_code == 201, "invitations: an admin and the owner each create one", statuses=[first.status_code, second.status_code])
    token_of = lambda created: created.json()["link"].split("#", 1)[1].split("=", 1)[-1]  # noqa: E731
    inviter_id = next(m["user_id"] for m in owner.get("/v1/members").json()["items"] if m["email"] == lib.email("inviter"))
    open_before = lib.db_one("select count(*) from invitations where invited_by = $1 and revoked_at is null and accepted_at is null", lib.uuid_of(inviter_id))
    demoted = owner.patch(f"/v1/members/{inviter_id}", json={"role": "DEVELOPER"})
    check(demoted.status_code == 200, "the owner demotes the admin to developer", status=demoted.status_code)
    after = lib.db("select invited_by, revoked_at is not null as revoked, accepted_at is not null as accepted from invitations where email like $1", f"%{os.environ['E2E_SUFFIX']}@guest.test")
    by_inviter = [r for r in after if str(r["invited_by"]) == str(lib.uuid_of(inviter_id))]
    others = [r for r in after if str(r["invited_by"]) != str(lib.uuid_of(inviter_id))]
    check(open_before == 1 and len(by_inviter) == 1 and by_inviter[0]["revoked"] and len(others) == 1 and not others[0]["revoked"],
          "invitations: demoting the inviter revoked their open invitation (database) and left the owner's open", before=open_before, revoked=[r["revoked"] for r in by_inviter], others_revoked=[r["revoked"] for r in others])
    gone = lib.person("other_admin", a).post("/v1/invitations/accept", json={"token": token_of(first)})
    check(gone.status_code == 404, "the revoked invitation's link no longer works (404, like an unknown token)", status=gone.status_code)
    audit = [r for r in lib.audit_rows() if r["action"] == "invitation.revoke_inviter_lost_rights"]
    check(len(audit) == 1 and audit[0]["details"].get("reason") == "inviter_role_changed", "invitations: one audit row names the reason", details=audit[0]["details"] if audit else None)
    lib.refresh_session("developer")  # "sign out everywhere else" ended the provisioned one too
    lib.finish()


# ------------------------------------------------------------------------------------------------ gate 6


def gate6() -> None:
    gate("gate6-alerts-search")
    a, b = lib.main_urls()
    owner = lib.person("owner", a)
    target = "https://93.184.216.34:8443/hook/hardening"  # a public literal: no name lookup; only the port is in question
    refused = owner.post("/v1/alerts/destinations", json={"name": "port refused", "type": "webhook", "target": {"url": target}})
    reason = refused.json().get("error", {})
    check(refused.status_code == 422 and "port_not_allowed" in json.dumps(reason), "alert ports: a destination on :8443 is refused at save (default list: 443 only)", status=refused.status_code, error=reason.get("details") or reason.get("message"))
    on_443 = owner.post("/v1/alerts/destinations", json={"name": "port ok", "type": "webhook", "target": {"url": "https://93.184.216.34/hook/hardening"}})
    check(on_443.status_code == 201, "alert ports: the same destination on 443 is accepted", status=on_443.status_code)

    # A row saved while a wider list was in force: a process with 443,8443 saves it; the stack's processes (443 only) must refuse to send.
    wide = lib.start_api("alerts-wide", lib.OIDC_PORT + 11, {"ABB_ALERTS_ALLOWED_PORTS": "443,8443"})
    try:
        saved = lib.person("owner", wide.url).post("/v1/alerts/destinations", json={"name": "legacy port", "type": "webhook", "target": {"url": target}})
        check(saved.status_code == 201, "alert ports: a process with ABB_ALERTS_ALLOWED_PORTS=443,8443 saves the :8443 destination", status=saved.status_code)
        legacy = saved.json()
    finally:
        wide.stop()
    sent = owner.post(f"/v1/alerts/destinations/{legacy['id']}/test")
    check(sent.status_code == 200 and sent.json() == {"ok": False, "error_code": "blocked_port"}, "alert ports: send-test on the pre-existing :8443 row is refused at delivery with blocked_port (no connection made)", result=sent.json())
    # and the worker (443 only): an alert that fires for that destination is a permanent failure with the same code, never retried
    project = lib.new_project("alerts-port")
    key = lib.new_key(project, ["events:write"], "alerts-port")
    rule = owner.post("/v1/alerts/rules", json={"name": "port rule", "trigger": "run_failed", "destination_ids": [legacy["id"]], "filters": {}, "cooldown_seconds": 0, "enabled": True, "project_id": project})
    check(rule.status_code == 201, "a rule routes run_failed alerts to it", status=rule.status_code)
    run = lib.run_ids()
    failed = lib.event(run, 2)
    failed.update({"event_type": "run.failed", "status": "error", "span_id": None})
    failed.pop("span_id")
    started = lib.event(run, 1)
    started.update({"event_type": "run.started", "attributes": {}})
    started.pop("span_id")
    posted = httpx.post(f"{a}/v1/events/batch", json=lib.batch_of([started, failed]), headers=lib.bearer(key), timeout=30)
    check(posted.status_code == 202, "a failed run is ingested", status=posted.status_code)
    deadline, rows = time.monotonic() + 60, []
    while time.monotonic() < deadline:
        rows = [r for r in owner.get("/v1/alerts/notifications", params={"limit": 50}).json()["items"] if (r.get("destination") or {}).get("id") == legacy["id"]]
        if rows and rows[0]["status"] != "pending":
            break
        time.sleep(1)
    info(history=[{k: r.get(k) for k in ("status", "error_code", "attempts")} for r in rows])
    check(bool(rows) and rows[0]["status"] == "dead" and rows[0]["error_code"] == "blocked_port" and rows[0]["attempts"] == 1, "alert ports: the worker's delivery to the pre-existing :8443 destination fails permanently with blocked_port", status=rows[0]["status"] if rows else None)
    lib.save(alerts_checked=True)
    _search_fairness(a, b)
    lib.finish()


def _search_fairness(a: str, b: str) -> None:
    project = lib.new_project("search-fair")
    key = lib.new_key(project, ["events:write"], "search")
    for i in range(30):  # something to find
        run = lib.run_ids()
        events = [lib.event(run, n + 1) for n in range(20)]
        events[0]["event_type"] = "run.started"
        events[0].pop("span_id")
        events[0]["attributes"] = {"run.name": f"hardening search fairness {i}"}
        httpx.post(f"{a}/v1/events/batch", json=lib.batch_of(events), headers=lib.bearer(key), timeout=30).raise_for_status()
    lib.quiet(timeout=120)  # the worker indexes them
    flooders = {role: lib.session_headers(role, a) for role in ("owner", "admin", "developer")}
    other = lib.session_headers("other_admin", a)

    async def search(client: httpx.AsyncClient, headers: dict[str, str], term: str = "hardening") -> lib.Reply:
        return await lib.fire(client, "GET", f"{a}/v1/runs", params={"q": term, "limit": 50}, headers=headers)

    async def scenario(actors: list[str]) -> tuple[list[lib.Reply], list[lib.Reply]]:
        async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=60)) as client:
            stop = time.monotonic() + 8

            async def flood(role: str) -> list[lib.Reply]:
                got: list[lib.Reply] = []
                while time.monotonic() < stop:
                    got.append(await search(client, flooders[role]))
                return got

            async def polite(headers: dict[str, str]) -> list[lib.Reply]:
                got: list[lib.Reply] = []
                await asyncio.sleep(1.0)
                while time.monotonic() < stop - 0.5:
                    got.append(await search(client, headers))
                    await asyncio.sleep(0.15)
                return got

            tasks = [flood(role) for role in actors for _ in range(10)]
            *flooded, calm = await asyncio.gather(*tasks, polite(other))
            return [r for part in flooded for r in part], calm

    # one actor floods; a colleague of the same workspace and another workspace keep searching
    flooded, calm = asyncio.run(scenario(["owner"]))
    reasons = sorted({(r.body or {}).get("error", {}).get("details", {}).get("reason") for r in flooded if r.code == "SEARCH_BUSY"})
    info(single_actor_flood=lib.tally(flooded), refusal_reasons=reasons, other_workspace=lib.tally(calm))
    check(lib.count(flooded, 503, "SEARCH_BUSY") > 0 and "actor_limit" in reasons, "search: one actor flooding with 10 concurrent searches is refused SEARCH_BUSY with reason actor_limit (the control: contention is real)", refused=lib.count(flooded, 503, "SEARCH_BUSY"))
    check(len(calm) >= 10 and all(r.status == 200 for r in calm), "search: another workspace's search always succeeds during the flood", searches=len(calm), tally=lib.tally(calm))
    # three actors of one workspace flood together
    flooded, calm = asyncio.run(scenario(["owner", "admin", "developer"]))
    reasons = sorted({(r.body or {}).get("error", {}).get("details", {}).get("reason") for r in flooded if r.code == "SEARCH_BUSY"})
    info(workspace_flood=lib.tally(flooded), refusal_reasons=reasons, other_workspace=lib.tally(calm))
    check(lib.count(flooded, 503, "SEARCH_BUSY") > 0, "search: three actors of one workspace flooding together are shed too", refused=lib.count(flooded, 503, "SEARCH_BUSY"))
    check(len(calm) >= 10 and all(r.status == 200 for r in calm), "search: the other workspace still always succeeds (one permit stays free for the next workspace to arrive)", searches=len(calm), tally=lib.tally(calm))
    check(lib.count(flooded, 200) > 0, "search: the flooding workspace was not shut out either (some searches succeeded)", succeeded=lib.count(flooded, 200))
    _ = b


# ------------------------------------------------------------------------------------------------ the browser spec


def ui_prepare() -> None:
    gate("ui-prepare")
    a, _ = lib.main_urls()
    project_id = lib.new_project("ui-quotas")
    key = lib.new_key(project_id, ["events:write"], "ui")
    assert lib.person("owner", a).put(f"/v1/projects/{project_id}/quotas", json={"runs_per_day": 10, "events_per_day": 12}).status_code == 200
    runs = [lib.run_ids() for _ in range(9)]
    events = [lib.event(run, 1) for run in runs] + [lib.event(runs[0], n + 2) for n in range(3)]
    reply = httpx.post(f"{a}/v1/events/batch", json=lib.batch_of(events), headers=lib.bearer(key), timeout=30)
    check(reply.status_code == 202 and reply.json()["accepted"] == 12, "ui data: 9 of 10 runs and 12 of 12 events used", accepted=reply.json()["accepted"])
    lib.quiet(timeout=120)
    lib.save(ui={"project": "ui-quotas", "run_id": runs[0]["run_id"], "stranger": f"stranger-{os.environ['E2E_SUFFIX']}@hardening.test"})
    lib.finish()


def ui_verify() -> None:
    gate("ui-verify")
    stranger = lib.state()["ui"]["stranger"]
    users = lib.db_one("select count(*) from users where lower(email) = lower($1)", stranger)
    sessions = lib.db_one("select count(*) from sessions s join users u on u.id = s.user_id where lower(u.email) = lower($1)", stranger)
    check(users == 0 and sessions == 0, "an un-invited sign-in left no users row and no session (database)", users=users, sessions=sessions)
    owner = lib.db_one("select count(*) from users where lower(email) = lower($1)", lib.email("owner"))
    check(owner == 1, "the CLI-made owner who signed in is still one row (linked, not duplicated)", users=owner)
    lib.refresh_session("owner")  # the browser spec signed out everywhere else, including the provisioned session
    lib.finish()

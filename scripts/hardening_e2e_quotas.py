"""Gate 3 of scripts/hardening-e2e.sh: project quotas (ADR-154) through two API processes over one database.

Each quota is set low through the real API as the workspace owner, filled to exactly its limit, and refused loudly at the next item;
existing runs and agents keep working, replays are never refused, usage matches the database, overrides are audited and role-gated, a
neighbouring project and another workspace are unaffected, and concurrent batches racing at the cap through BOTH processes never get
one item past it (the stated bound is zero over).
"""

import asyncio
import uuid
from typing import Any

import httpx

import hardening_e2e_lib as lib
from hardening_e2e_lib import check, gate, info

QUOTA_NAMES = ("agents", "runs_per_day", "events_per_day", "artifact_bytes")


def _ingest(base: str, token: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    response = httpx.post(f"{base}/v1/events/batch", json=lib.batch_of(events), headers=lib.bearer(token), timeout=60)
    if response.status_code != 202:
        lib.die(f"ingest -> {response.status_code} {response.text[:200]}")
    return dict(response.json())


def _usage(project: str) -> dict[str, dict[str, Any]]:
    response = lib.person("owner").get(f"/v1/projects/{project}/quotas")
    response.raise_for_status()
    return {row["quota"]: row for row in response.json()["quotas"]}


def _set(project: str, role: str = "owner", **limits: int | None) -> httpx.Response:
    return lib.person(role).put(f"/v1/projects/{project}/quotas", json=limits)


def _refused(reply: dict[str, Any], quota: str) -> bool:
    errors = reply["errors"]
    return reply["rejected"] >= 1 and bool(errors) and all(e["code"] == "QUOTA_EXCEEDED" and e["quota"] == quota for e in errors)


def _db_count(table: str, project: str) -> int:
    return int(lib.db_one(f"select count(*) from {table} where project_id = $1", lib.uuid_of(project)))  # noqa: S608


def _text(n: int, tag: str) -> bytes:
    """`n` bytes of ordinary log-like text (the server redacts what looks like a secret, which would change the stored size)."""
    line = f"{tag} build step finished without errors in the sandbox\n"
    return (line * (n // len(line) + 1)).encode()[:n]


def _artifact(base: str, token: str, body: bytes, artifact_id: str | None = None, run: str | None = None) -> tuple[httpx.Response, str]:
    from abb_event_schema.ids import IdKind, new_id  # noqa: PLC0415

    artifact_id = artifact_id or str(new_id(IdKind.ARTIFACT))
    response = httpx.put(f"{base}/v1/artifacts/{artifact_id}", content=body, params={"run_id": run or str(new_id(IdKind.RUN)), "kind": "stdout"},
                         headers={**lib.bearer(token), "content-type": "text/plain"}, timeout=60)
    return response, artifact_id


async def _race(urls: list[str], total: int, make: Any, concurrency: int) -> list[lib.Reply]:
    async with httpx.AsyncClient(timeout=60, limits=httpx.Limits(max_connections=concurrency + 2)) as client:
        return await lib.run_concurrent(total, concurrency, lambda i: make(client, urls[i % len(urls)], i))


def gate3() -> None:
    gate("gate3-quotas")
    a, b = lib.main_urls()
    urls = [a, b]

    # ------------------------------------------------------------------ agents
    p_agents = lib.new_project("q-agents")
    k_agents = lib.new_key(p_agents, ["events:write"], "agents")
    reply = _set(p_agents, agents=3)
    check(reply.status_code == 200, "the owner sets a per-project override through the real API", status=reply.status_code)
    run1 = lib.run_ids()
    first = [lib.event(run1, n + 1, agent=f"agent-{n + 1}") for n in range(3)]
    got = _ingest(a, k_agents, first)
    check(got["accepted"] == 3 and got["rejected"] == 0, "agents: three distinct agents are accepted (exactly the limit)", accepted=got["accepted"])
    usage = _usage(p_agents)["agents"]
    check(usage["used"] == 3 and usage["limit"] == 3 and usage["overridden"] and usage["status"] == "at_limit" and usage["percent"] == 100, "agents: usage says 3 of 3, overridden, at_limit", usage=usage)
    fourth = _ingest(b, k_agents, [lib.event(lib.run_ids(), 1, agent="agent-4")])
    check(_refused(fourth, "agents") and fourth["accepted"] == 0, "agents: a fourth agent is refused QUOTA_EXCEEDED naming 'agents' (through the other process)", errors=fourth["errors"][:1])
    again = _ingest(b, k_agents, [lib.event(run1, 10, agent="agent-1"), lib.event(lib.run_ids(), 1, agent="agent-2")])
    check(again["accepted"] == 2 and again["rejected"] == 0, "agents: existing agents keep working at the limit (an old run and a new run)")
    replay = _ingest(a, k_agents, first)
    check(replay["duplicates"] == 3 and replay["rejected"] == 0, "agents: a replay of accepted events is a duplicate, never refused", duplicates=replay["duplicates"])
    check(_db_count("agents", p_agents) == 3, "agents: the database holds exactly 3 agents for the project")

    # ------------------------------------------------------------------ runs per day
    p_runs = lib.new_project("q-runs")
    k_runs = lib.new_key(p_runs, ["events:write"], "runs")
    _set(p_runs, runs_per_day=3)
    runs = [lib.run_ids() for _ in range(3)]
    accepted = [_ingest(urls[i % 2], k_runs, [lib.event(run, 1)]) for i, run in enumerate(runs)]
    check(all(r["accepted"] == 1 for r in accepted), "runs: three runs are accepted (exactly the limit)")
    refused = _ingest(a, k_runs, [lib.event(lib.run_ids(), 1)])
    check(_refused(refused, "runs_per_day"), "runs: a fourth run is refused QUOTA_EXCEEDED naming 'runs_per_day'", errors=refused["errors"][:1])
    later = _ingest(b, k_runs, [lib.event(runs[0], 2), lib.event(runs[1], 2)])
    check(later["accepted"] == 2, "runs: later events of existing runs are still accepted at the limit")
    old = _ingest(a, k_runs, [lib.event(runs[2], 1)])
    check(old["accepted"] == 1 and old["rejected"] == 0, "runs: a new event of an existing run is not a new run")
    usage = _usage(p_runs)["runs_per_day"]
    check(usage["used"] == 3 == _db_count("runs", p_runs) and usage["status"] == "at_limit", "runs: usage equals the database run count (3)", usage=usage["used"])

    # ------------------------------------------------------------------ events per day
    p_events = lib.new_project("q-events")
    k_events = lib.new_key(p_events, ["events:write"], "events")
    _set(p_events, events_per_day=20)
    run = lib.run_ids()
    one = [lib.event(run, n + 1) for n in range(10)]
    two = [lib.event(run, n + 11) for n in range(10)]
    r1, r2 = _ingest(a, k_events, one), _ingest(b, k_events, two)
    check(r1["accepted"] + r2["accepted"] == 20, "events: exactly 20 events are accepted (the limit)")
    over = _ingest(a, k_events, [lib.event(run, 99)])
    check(_refused(over, "events_per_day"), "events: the 21st is refused QUOTA_EXCEEDED naming 'events_per_day'", errors=over["errors"][:1])
    replay = _ingest(b, k_events, one[:5])
    check(replay["duplicates"] == 5 and replay["rejected"] == 0, "events: a replay of accepted events at the limit is a duplicate, never refused")
    usage = _usage(p_events)["events_per_day"]
    check(usage["used"] == 20 == _db_count("events", p_events), "events: usage equals the database event count (20)", usage=usage["used"])

    # ------------------------------------------------------------------ artifact bytes
    p_bytes = lib.new_project("q-bytes")
    k_bytes = lib.new_key(p_bytes, ["artifacts:write"], "bytes")
    _set(p_bytes, artifact_bytes=4096)
    ok1, id1 = _artifact(a, k_bytes, _text(3000, "first"))
    refused_upload, _ = _artifact(b, k_bytes, _text(3000, "second"))
    error = refused_upload.json().get("error", {})
    check(ok1.status_code == 201, "artifacts: 3,000 of 4,096 bytes are stored")
    check(refused_upload.status_code == 413 and error.get("code") == "QUOTA_EXCEEDED" and error.get("details", {}).get("quota") == "artifact_bytes",
          "artifacts: the next 3,000 bytes are refused 413 QUOTA_EXCEEDED naming 'artifact_bytes'", status=refused_upload.status_code, details=error.get("details"))
    ok2, _ = _artifact(a, k_bytes, _text(1000, "third"))
    again, _ = _artifact(b, k_bytes, _text(3000, "first"), artifact_id=id1)
    check(ok2.status_code == 201, "artifacts: 1,000 more bytes fit (4,000 of 4,096)")
    check(again.status_code in {200, 201}, "artifacts: a retry of a stored artifact at the limit is answered, not refused", status=again.status_code)
    usage = _usage(p_bytes)["artifact_bytes"]
    stored = int(lib.db_one("select coalesce(sum(size_bytes), 0) from artifacts where project_id = $1", lib.uuid_of(p_bytes)))
    check(usage["used"] == stored == 4000, "artifacts: usage equals the stored bytes (4,000)", usage=usage["used"])

    # ------------------------------------------------------------------ overrides: audit, roles, isolation
    p_other = lib.new_project("q-neighbour")
    k_other = lib.new_key(p_other, ["events:write"], "neighbour")
    got = _ingest(a, k_other, [lib.event(lib.run_ids(), n + 1, agent=f"n-{n}") for n in range(8)])
    check(got["accepted"] == 8, "a neighbouring project (default limits) is unaffected while four others sit at their limits")
    other_usage = _usage(p_other)
    check(other_usage["agents"]["used"] == 8 and other_usage["events_per_day"]["used"] == 8 and not any(r["overridden"] for r in other_usage.values()), "usage never crosses projects: the neighbour counts only its own", used=other_usage["events_per_day"]["used"])
    check(lib.person("viewer").get(f"/v1/projects/{p_agents}/quotas").status_code == 200, "every role reads usage (viewer)")
    for role in ("viewer", "developer"):
        denied = _set(p_agents, role=role, agents=400)
        check(denied.status_code == 403, f"{role} cannot change a quota (403)", status=denied.status_code)
    allowed = _set(p_agents, role="admin", agents=3)
    check(allowed.status_code == 200, "an admin can (idempotent: same value)")
    by_key = httpx.put(f"{a}/v1/projects/{p_agents}/quotas", json={"agents": 400}, headers=lib.bearer(k_agents), timeout=30)
    check(by_key.status_code in {401, 403}, "an API key cannot change a quota", status=by_key.status_code)
    foreign = lib.person("other_admin").get(f"/v1/projects/{p_agents}/quotas")
    foreign_put = lib.person("other_admin").put(f"/v1/projects/{p_agents}/quotas", json={"agents": 400})
    check(foreign.status_code == 404 and foreign_put.status_code == 404 and lib.scrub(foreign.json()) == lib.scrub(foreign_put.json()), "another workspace's admin gets the same 404 on read and write", read=foreign.status_code, write=foreign_put.status_code)
    invalid = _set(p_agents, agents=0)
    check(invalid.status_code == 422, "an out-of-range value is 422", status=invalid.status_code, code=invalid.json().get("error", {}).get("code"))
    rows = [r for r in lib.audit_rows() if r["action"] == "project.quota.update"]
    mine = {r["details"].get("quota"): r for r in rows if r["resource_id"] == p_agents}
    check("agents" in mine and mine["agents"]["details"] == {"quota": "agents", "old": None, "new": 3}, "overrides are audited with {quota, old, new}", agents=mine.get("agents", {}).get("details"))
    changed_rows = [r for r in rows if r["resource_id"] == p_agents]
    check(len(changed_rows) == 1, "a request that changes nothing writes no audit row; a refused one neither (1 row for the project)", rows=len(changed_rows))
    check(len({r["resource_id"] for r in rows}) >= 4, "each project's overrides are audited under its own id", projects=len({r["resource_id"] for r in rows}))
    foreign_ws_usage = lib.person("other_admin").get("/v1/projects")
    check(foreign_ws_usage.status_code == 200 and p_agents not in {p["id"] for p in foreign_ws_usage.json()["items"]}, "another workspace does not see these projects")

    # ------------------------------------------------------------------ races through BOTH processes: zero over
    # events: 16 concurrent batches of 10 against a limit of 50
    p_race = lib.new_project("q-race-events")
    k_race = lib.new_key(p_race, ["events:write"], "race-events")
    _set(p_race, events_per_day=50)

    async def event_batch(client: httpx.AsyncClient, base: str, i: int) -> lib.Reply:
        run = lib.run_ids()
        return await lib.fire(client, "POST", f"{base}/v1/events/batch", json=lib.batch_of([lib.event(run, n + 1) for n in range(10)]), headers=lib.bearer(k_race))

    replies = asyncio.run(_race(urls, 16, event_batch, 16))
    accepted_events = sum(r.body["accepted"] for r in replies if r.status == 202)
    stored = _db_count("events", p_race)
    info(race="events", limit=50, attempted=160, statuses=lib.tally(replies), accepted=accepted_events, stored=stored)
    check(all(r.status == 202 for r in replies), "race (events): every batch was answered 202 (no 5xx under the project lock)", tally=lib.tally(replies))
    check(accepted_events == stored == 50, "race (events): 16 concurrent batches through two processes accepted exactly 50 = the limit, stored 50: ZERO over", accepted=accepted_events, stored=stored)

    # agents: 12 concurrent batches, each a new agent, limit 5
    p_ra = lib.new_project("q-race-agents")
    k_ra = lib.new_key(p_ra, ["events:write"], "race-agents")
    _set(p_ra, agents=5)

    async def agent_batch(client: httpx.AsyncClient, base: str, i: int) -> lib.Reply:
        return await lib.fire(client, "POST", f"{base}/v1/events/batch", json=lib.batch_of([lib.event(lib.run_ids(), 1, agent=f"racer-{i}")]), headers=lib.bearer(k_ra))

    replies = asyncio.run(_race(urls, 12, agent_batch, 12))
    ok = sum(r.body["accepted"] for r in replies if r.status == 202)
    agents = _db_count("agents", p_ra)
    info(race="agents", limit=5, attempted=12, accepted=ok, stored=agents, statuses=lib.tally(replies))
    check(ok == agents == 5 and all(r.status == 202 for r in replies), "race (agents): 12 new agents racing for 5 places through two processes: exactly 5, zero over", accepted=ok, stored=agents)

    # runs: 12 concurrent new runs, limit 4
    p_rr = lib.new_project("q-race-runs")
    k_rr = lib.new_key(p_rr, ["events:write"], "race-runs")
    _set(p_rr, runs_per_day=4)

    async def run_batch(client: httpx.AsyncClient, base: str, i: int) -> lib.Reply:
        return await lib.fire(client, "POST", f"{base}/v1/events/batch", json=lib.batch_of([lib.event(lib.run_ids(), 1)]), headers=lib.bearer(k_rr))

    replies = asyncio.run(_race(urls, 12, run_batch, 12))
    ok = sum(r.body["accepted"] for r in replies if r.status == 202)
    stored = _db_count("runs", p_rr)
    info(race="runs", limit=4, attempted=12, accepted=ok, stored=stored)
    check(ok == stored == 4 and all(r.status == 202 for r in replies), "race (runs): 12 new runs racing for 4 places through two processes: exactly 4, zero over", accepted=ok, stored=stored)

    # bytes: 8 concurrent uploads of 1,500 bytes, limit 4,096 (room for two)
    p_rb = lib.new_project("q-race-bytes")
    k_rb = lib.new_key(p_rb, ["artifacts:write"], "race-bytes")
    _set(p_rb, artifact_bytes=4096)

    async def upload(client: httpx.AsyncClient, base: str, i: int) -> lib.Reply:
        from abb_event_schema.ids import IdKind, new_id  # noqa: PLC0415

        return await lib.fire(client, "PUT", f"{base}/v1/artifacts/{new_id(IdKind.ARTIFACT)}", content=_text(1500, f"racer{i}"),
                              params={"run_id": str(new_id(IdKind.RUN)), "kind": "stdout"}, headers={**lib.bearer(k_rb), "content-type": "text/plain"})

    replies = asyncio.run(_race(urls, 8, upload, 8))
    created = lib.count(replies, 201)
    total = int(lib.db_one("select coalesce(sum(size_bytes), 0) from artifacts where project_id = $1", lib.uuid_of(p_rb)))
    info(race="bytes", limit=4096, attempted=8 * 1500, statuses=lib.tally(replies), stored_bytes=total)
    check(created == 2 and total == 3000 <= 4096 and lib.count(replies, 413, "QUOTA_EXCEEDED") == 6, "race (artifact bytes): 8 concurrent uploads through two processes: 2 stored (3,000 bytes), 6 refused, never over 4,096", tally=lib.tally(replies))
    _ = uuid
    lib.finish()

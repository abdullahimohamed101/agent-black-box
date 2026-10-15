"""Gates 1 and 2 of scripts/hardening-e2e.sh: the shared limiter across API processes (ADR-152) and the failed-auth flood (ADR-153)."""

import asyncio
import time
from typing import Any

import httpx

import hardening_e2e_lib as lib
from hardening_e2e_lib import check, gate, info

POLICY_DOC = """\
version: 1
policies:
  - id: deny-env-read
    description: Do not allow agents to read dotenv secret files.
    when:
      capability: filesystem.read
      resource: "**/.env*"
    decision: deny
"""
LIMIT = 6  # per-minute budgets in gate 1
EVENTS_WINDOW_LIMIT = 10  # 0.5 events/s over the 10 s ingestion window, burst 10
BYTES_WINDOW_LIMIT = 20480

TIGHT = {
    "ABB_INGEST_MAX_BATCH_EVENTS": "10", "ABB_INGEST_MAX_BODY_BYTES": "20480", "ABB_ARTIFACT_MAX_BYTES": "20480",
    "ABB_RATE_LIMIT_EVENTS_PER_SECOND": "0.5", "ABB_RATE_LIMIT_BURST_EVENTS": "10",
    "ABB_RATE_LIMIT_BYTES_PER_SECOND": "2048", "ABB_RATE_LIMIT_BURST_BYTES": str(BYTES_WINDOW_LIMIT),
    "ABB_POLICY_WRITES_PER_MINUTE": str(LIMIT), "ABB_ALERTS_WRITES_PER_MINUTE": str(LIMIT), "ABB_LOGIN_GLOBAL_PER_MINUTE": str(LIMIT),
}


# ------------------------------------------------------------------------------------------------ gate 1


async def _alternate(urls: list[str], total: int, make: Any, concurrency: int = 8) -> list[lib.Reply]:
    """`total` requests spread over both processes, concurrently: make(client, base_url, i)."""
    limits = httpx.Limits(max_connections=concurrency * 2)
    async with httpx.AsyncClient(timeout=60, limits=limits) as client:
        return await lib.run_concurrent(total, concurrency, lambda i: make(client, urls[i % len(urls)], i))



# A same-machine flood opens a new TCP connection for every request, so a few connections can be refused at the operating system's
# accept queue (macOS: kern.ipc.somaxconn = 128) before the API ever sees them. That is the load generator's environment, not the API
# failing; what the gate claims is that the API never answers a flood with anything but 401 or 429, so any other HTTP status is a
# failure and transport errors are tolerated only as a tiny fraction of the flood, and are named.
TRANSPORT_ERROR_SHARE = 0.005


def _only_401_429(replies: list[lib.Reply], message: str) -> None:
    other = [r for r in replies if r.status not in {401, 429} and r.status != 0]
    transport = [r for r in replies if r.status == 0]
    classes = sorted({str(r.code) for r in transport})
    check(not other and len(transport) <= max(5, int(TRANSPORT_ERROR_SHARE * len(replies))), message,
          tally=lib.tally(replies), other_http_statuses=len(other), transport_errors=len(transport), transport_error_classes=classes)

def _budgets(procs: tuple[lib.Proc, lib.Proc], label: str) -> dict[str, int]:
    """Hammer every shared budget through BOTH processes at once; returns how many requests each budget accepted."""
    urls = [p.url for p in procs]
    owner = {u: lib.session_headers("owner", u) for u in urls}
    accepted: dict[str, int] = {}

    # --- 60 s budgets: policy publishes, alert writes, sign-in starts (one fresh window, all three inside it)
    lib.room_in_window(60, 25)
    lib.person("owner", urls[0])  # sessions are resolved before the clock matters
    lib.person("owner", urls[1])

    async def publish(client: httpx.AsyncClient, base: str, i: int) -> lib.Reply:
        return await lib.fire(client, "POST", f"{base}/v1/policies/publish", json={"document": POLICY_DOC}, headers=owner[base])

    async def destination(client: httpx.AsyncClient, base: str, i: int) -> lib.Reply:
        body = {"name": f"{label}-{i}", "type": "email", "target": {"recipients": [f"ops{i}@example.test"]}}
        return await lib.fire(client, "POST", f"{base}/v1/alerts/destinations", json=body, headers=owner[base])

    async def sign_in(client: httpx.AsyncClient, base: str, i: int) -> lib.Reply:
        return await lib.fire(client, "GET", f"{base}/v1/auth/login", follow_redirects=False)

    for name, make, ok in (("policy_writes", publish, 201), ("alert_writes", destination, 201), ("sign_in_starts", sign_in, 302)):
        replies = asyncio.run(_alternate(urls, LIMIT * 3, make))
        accepted[name] = lib.count(replies, ok)
        refused = [r for r in replies if r.status == 429]
        info(budget=name, mode=label, tally=lib.tally(replies), retry_after=sorted({r.retry_after for r in refused}))
        check(accepted[name] + len(refused) == len(replies), f"{label}: {name}: every answer is accepted or 429 (nothing else)", tally=lib.tally(replies))
        if label == "shared":
            check(all(r.retry_after and 1 <= int(r.retry_after) <= 60 for r in refused), f"{label}: {name}: every 429 carries Retry-After within the window")

    # --- 10 s ingestion windows: events and bytes, each through a fresh project and key
    for name, pad, total in (("ingest_events", 0, 40), ("ingest_bytes", 4000, 24)):
        project = lib.new_project(f"{label}-{name}".replace("_", "-"), base=urls[0])
        token = lib.new_key(project, ["events:write"], name, base=urls[0])
        lib.room_in_window(10, 6.5)

        async def ingest(client: httpx.AsyncClient, base: str, i: int, pad: int = pad, token: str = token) -> lib.Reply:
            run = lib.run_ids()
            return await lib.fire(client, "POST", f"{base}/v1/events/batch", json=lib.batch_of([lib.event(run, 1, pad=pad)]), headers=lib.bearer(token))

        replies = asyncio.run(_alternate(urls, total, ingest))
        accepted[name] = lib.count(replies, 202)
        refused = [r for r in replies if r.status == 429]
        info(budget=name, mode=label, tally=lib.tally(replies), retry_after=sorted({r.retry_after for r in refused}))
        check(accepted[name] + len(refused) == len(replies), f"{label}: {name}: every answer is 202 or 429", tally=lib.tally(replies))
    return accepted


def gate1() -> None:
    gate("gate1-limits")
    ports = (lib.OIDC_PORT + 1, lib.OIDC_PORT + 2)
    divisor, processes = 20, 2

    # --- shared limiter on (the default): two processes, one database
    on = lib.start_pair("limits-on", ports, {**TIGHT, "ABB_OIDC_ISSUER": f"http://localhost:{lib.OIDC_PORT}"})
    try:
        got = _budgets(on, "shared")
    finally:
        lib.stop_all(*on)
    # The ADR-152 bound: admitted <= limit, exactly; at least limit - N(k-1) (early refusals: credit a quiet process holds).
    slack_per_minute = processes * (max(1, LIMIT // divisor) - 1)
    slack_events = processes * (max(1, EVENTS_WINDOW_LIMIT // divisor) - 1)
    info(bound="admitted <= limit; admitted >= limit - N*(k-1); k = max(1, limit // 20)", N=processes,
         slack_minute_budgets=slack_per_minute, slack_events=slack_events, bytes_unit=max(1, BYTES_WINDOW_LIMIT // divisor))
    for name in ("policy_writes", "alert_writes", "sign_in_starts"):
        check(LIMIT - slack_per_minute <= got[name] <= LIMIT, f"shared: {name}: combined accepted {got[name]} = the limit {LIMIT} (not ~{2 * LIMIT})", accepted=got[name])
    check(EVENTS_WINDOW_LIMIT - slack_events <= got["ingest_events"] <= EVENTS_WINDOW_LIMIT,
          f"shared: ingest_events: combined accepted {got['ingest_events']} = the window limit {EVENTS_WINDOW_LIMIT} (not ~{2 * EVENTS_WINDOW_LIMIT})")
    # Each 4,000-byte event is a little over 4.1 KB on the wire: the 20,480-byte window admits 4 at most; the early-refusal slack of
    # 2 x 1,023 bytes can cost one of them.
    check(3 <= got["ingest_bytes"] <= 4, f"shared: ingest_bytes: combined accepted {got['ingest_bytes']} requests of ~4.1 KB within {BYTES_WINDOW_LIMIT} bytes (not ~8)")
    # Table evidence: the rows exist, written by the runtime role.
    rows = lib.db("select scope, sum(count) as n from rate_limit_counters group by scope order by scope")
    info(counter_rows={r["scope"]: int(r["n"]) for r in rows})
    check({"policy_write", "alert_write", "login_start", "ingest_events", "ingest_bytes"} <= {r["scope"] for r in rows}, "shared: the counters of every budget are in rate_limit_counters")

    # --- control: the same run with the shared tier OFF: about 2x (this proves the test can see the problem)
    off = lib.start_pair("limits-off", ports, {**TIGHT, "ABB_LIMITS_SHARED_ENABLED": "0", "ABB_OIDC_ISSUER": f"http://localhost:{lib.OIDC_PORT}"})
    try:
        control = _budgets(off, "control")
    finally:
        lib.stop_all(*off)
    for name in ("policy_writes", "alert_writes", "sign_in_starts"):
        check(control[name] >= int(1.6 * LIMIT), f"control (sharing off): {name}: accepted {control[name]} ~ 2 x {LIMIT}", accepted=control[name])
    check(control["ingest_events"] >= int(1.6 * EVENTS_WINDOW_LIMIT), f"control (sharing off): ingest_events: accepted {control['ingest_events']} ~ 2 x {EVENTS_WINDOW_LIMIT}")
    check(control["ingest_bytes"] >= 7, f"control (sharing off): ingest_bytes: accepted {control['ingest_bytes']} ~ 2 x 4")
    finish_gate()


def finish_gate() -> None:
    lib.finish()


# ------------------------------------------------------------------------------------------------ gate 2

PER_ADDRESS, PER_CREDENTIAL, GLOBAL_CEILING = 30, 10, 600  # the defaults of ADR-153
PROCESSES = 2
CONCURRENCY = 16


async def _bad(client: httpx.AsyncClient, base: str, *, forwarded: str | None = None, token: str | None = None) -> lib.Reply:
    headers = {"Authorization": f"Bearer {token or lib.random_token()}"}
    if forwarded:
        headers["X-Forwarded-For"] = forwarded
    return await lib.fire(client, "GET", f"{base}/v1/runs", headers=headers)


class Valid:
    """The valid traffic that must never be refused: a warmed ingestion key and a signed-in session, through both processes."""

    def __init__(self, urls: list[str], token: str) -> None:
        self.urls, self.token = urls, token
        self.session = {u: lib.session_headers("owner", u) for u in urls}

    async def one(self, client: httpx.AsyncClient, i: int) -> lib.Reply:
        base = self.urls[i % len(self.urls)]
        if (i // len(self.urls)) % 2 == 0:
            run = lib.run_ids()
            events = [lib.event(run, n + 1) for n in range(20)]
            reply = await lib.fire(client, "POST", f"{base}/v1/events/batch", json=lib.batch_of(events), headers=lib.bearer(self.token))
            reply.code = "ingest" if reply.status == 202 else reply.code
            return reply
        reply = await lib.fire(client, "GET", f"{base}/v1/me", headers=self.session[base])
        reply.code = "session" if reply.status == 200 else reply.code
        return reply


async def _nothing() -> list[lib.Reply]:
    return []


def _light(valid: Valid) -> Any:
    """A session read about every 200 ms: a cheap probe that is not a load of its own."""
    async def one(client: httpx.AsyncClient, i: int) -> lib.Reply:
        await asyncio.sleep(0.2)
        base = valid.urls[i % len(valid.urls)]
        reply = await lib.fire(client, "GET", f"{base}/v1/me", headers=valid.session[base])
        reply.code = "session" if reply.status == 200 else reply.code
        return reply
    return one


def _p95(replies: list[lib.Reply]) -> float:
    return round(lib.percentile([r.elapsed_ms for r in replies], 0.95), 1)


def _valid_ok(replies: list[lib.Reply]) -> bool:
    return bool(replies) and all(r.code in {"ingest", "session"} for r in replies)


async def _phase(urls: list[str], valid: Valid, flood: Any, seconds: float, flood_concurrency: int, *, with_valid: bool = True) -> tuple[list[lib.Reply], list[lib.Reply], float, int]:
    """A flood and the valid traffic at the same time, in one event loop. Returns (flood, valid, seconds, database transactions)."""
    limits = httpx.Limits(max_connections=flood_concurrency + 8)
    async with httpx.AsyncClient(timeout=30, limits=limits) as client:
        before, started = await asyncio.to_thread(lib.transactions), time.monotonic()
        flooded, good = await asyncio.gather(
            lib.run_for(seconds, flood_concurrency, lambda i: flood(client, urls[i % len(urls)], i)),
            lib.run_for(seconds, 2, lambda i: valid.one(client, i)) if with_valid else _nothing(),
        )
        elapsed = time.monotonic() - started
        after = await asyncio.to_thread(lib.transactions)
    return flooded, good, elapsed, after - before - 1  # minus the counting query itself


def gate2() -> None:
    gate("gate2-auth-throttle")
    # A valid key that no process below has ever seen: created through the main API before the pair starts, so each process loads its
    # id at startup (the active key ids, ADR-153 point 3) but has never authenticated its token.
    cold_key = lib.new_key(lib.new_project("throttle-cold"), ["runs:read"], "cold")
    ports = (lib.OIDC_PORT + 3, lib.OIDC_PORT + 4)
    shared = {"ABB_TRUST_PROXY": "1"}
    a, b = lib.start_pair("throttle", ports, shared)
    c = lib.start_api("throttle-notrust", lib.OIDC_PORT + 5, {"ABB_TRUST_PROXY": "0"})
    d = lib.start_api("throttle-off", lib.OIDC_PORT + 6, {"ABB_AUTH_FAIL_ENABLED": "0"})
    urls = [a.url, b.url]
    try:
        _gate2(a, b, c, d, urls, cold_key)
    finally:
        lib.stop_all(a, b, c, d)
    lib.finish()


def _gate2(a: lib.Proc, b: lib.Proc, c: lib.Proc, d: lib.Proc, urls: list[str], cold_key: str) -> None:
    project = lib.new_project("throttle", base=a.url)
    token = lib.new_key(project, ["events:write", "runs:read"], "warm", base=a.url)
    victim = lib.new_key(project, ["events:write"], "victim", base=a.url)
    valid = Valid(urls, token)

    async def warm_and_baseline() -> tuple[list[lib.Reply], list[lib.Reply]]:
        async with httpx.AsyncClient(timeout=30) as client:
            for i in range(4):  # a valid key and session through each process: the known-valid cache (ADR-153, point 3)
                await valid.one(client, i)
            before = await lib.run_for(5.0, 2, lambda i: valid.one(client, i))
            # --- identical bodies for an unknown key and a wrong secret (before any throttle)
            unknown = await _bad(client, a.url)
            wrong = await _bad(client, a.url, token=lib.bad_token_for(victim))
            return before, [unknown, wrong]

    before, pair = asyncio.run(warm_and_baseline())
    check(_valid_ok(before), "baseline: every valid request is served", n=len(before), p95_ms=_p95(before))
    unknown, wrong = pair
    check(unknown.status == 401 and wrong.status == 401 and lib.scrub(unknown.body) == lib.scrub(wrong.body),
          "an unknown key and a wrong secret of a real key get the identical 401 body", status=[unknown.status, wrong.status], code=unknown.code)
    info(idle_database_transactions_per_second=lib.quiet())  # the worker derives what the baseline ingested first

    # --- a malformed key is cheap to refuse and is neither counted nor gated: no database work at all
    async def malformed() -> tuple[list[lib.Reply], int]:
        async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=CONCURRENCY + 2)) as client:
            before_tx = await asyncio.to_thread(lib.transactions)
            got = await lib.run_concurrent(300, CONCURRENCY, lambda i: lib.fire(client, "GET", f"{urls[i % 2]}/v1/runs", headers={"Authorization": f"Bearer abb_live_not-a-key-{i}"}))
            return got, await asyncio.to_thread(lib.transactions) - before_tx - 1

    junk, junk_tx = asyncio.run(malformed())
    check(lib.count(junk, 401) == 300 and junk_tx < 60, "300 malformed keys: 401 each, no key lookup (database transactions stay at idle level)", tally=lib.tally(junk), transactions=junk_tx)

    # one fresh 60 s window for every counted step below (a count taken across a boundary would be split)
    lib.room_in_window(60, 42)
    window_started = time.monotonic()

    # --- spoof resistance of X-Forwarded-For (ABB_TRUST_PROXY off, then on)
    async def spoof() -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=CONCURRENCY + 2)) as client:
            # (a) trust OFF: a header naming one address, 40 distinct bad keys: the header is ignored, nothing is counted against it
            off = await lib.run_concurrent(40, CONCURRENCY, lambda i: _bad(client, c.url, forwarded="192.0.2.77"))
            # (b) trust ON, the same address from the other process: the earlier 40 never charged it, so 5 more are still plain 401s
            framed = await lib.run_concurrent(5, 1, lambda i: _bad(client, a.url, forwarded="192.0.2.77"))
            # (c) trust ON: rotating the CLIENT-supplied (first) entries cannot evade the budget; the proxy-appended last entry is the key
            rotate = await lib.run_concurrent(
                120, CONCURRENCY, lambda i: _bad(client, urls[i % 2], forwarded=f"{lib.jitter_ip()}, 198.51.100.7"))
            # (d) a different address is not affected by (c)
            other = await lib.run_concurrent(3, 1, lambda i: _bad(client, a.url, forwarded="198.51.100.8"))
            return {"off": off, "framed": framed, "rotate": rotate, "other": other}

    spoofed = asyncio.run(spoof())
    info(spoof_trust_off=lib.tally(spoofed["off"]), spoof_framed=lib.tally(spoofed["framed"]), spoof_rotate=lib.tally(spoofed["rotate"]), other=lib.tally(spoofed["other"]))
    check(lib.count(spoofed["off"], 401) == 40, "ABB_TRUST_PROXY off: the header is ignored (40 bad keys naming one address are 40 plain 401s, none throttled)")
    check(lib.count(spoofed["framed"], 401) == 5, "a flood that named an address with trust off did not charge it on the trusting processes (5 of 5 still 401)")
    rotate = spoofed["rotate"]
    admitted = lib.count(rotate, 401)
    throttled = [r for r in rotate if r.status == 429]
    # The bound (ADR-153, point 2): about `limit` lookups then zero; requests already past the gate when it closed (<= the load
    # generator's concurrency) may still fail with 401. Anything near the 120 sent would mean the rotating header evaded the budget.
    check(PER_ADDRESS - 2 <= admitted <= PER_ADDRESS + PROCESSES + CONCURRENCY, f"ABB_TRUST_PROXY on: rotating client-supplied entries cannot evade the address budget (401s={admitted}, limit {PER_ADDRESS})", sent=len(rotate))
    check(len(throttled) >= len(rotate) - (PER_ADDRESS + PROCESSES + CONCURRENCY) and all(r.code == "AUTH_THROTTLED" for r in throttled), "the rest are 429 AUTH_THROTTLED", throttled=len(throttled))
    check(bool(throttled) and all(r.retry_after and 1 <= int(r.retry_after) <= 60 for r in throttled), "every 429 carries Retry-After within the window", sample=throttled[0].retry_after if throttled else None)
    check(lib.count(spoofed["other"], 401) == 3, "another address is unaffected by that flood")

    # --- credential flood on a REAL key id (wrong secrets) and on an unknown key id, from many addresses, through both processes
    unknown_id = lib.random_token()

    async def credential() -> dict[str, list[lib.Reply]]:
        async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=CONCURRENCY + 2)) as client:
            real = await lib.run_concurrent(80, CONCURRENCY, lambda i: _bad(client, urls[i % 2], forwarded=lib.jitter_ip(), token=lib.bad_token_for(victim)))
            ghost = await lib.run_concurrent(
                80, CONCURRENCY, lambda i: _bad(client, urls[i % 2], forwarded=lib.jitter_ip(), token=f"{lib.key_id_of(unknown_id)}.{lib.random_token().split('.')[1]}"))
            return {"real": real, "ghost": ghost}

    cred = asyncio.run(credential())
    for name in ("real", "ghost"):
        got = cred[name]
        ok401, too = lib.count(got, 401), [r for r in got if r.status == 429]
        info(credential_flood=name, tally=lib.tally(got))
        check(PER_CREDENTIAL - 2 <= ok401 <= PER_CREDENTIAL + PROCESSES + CONCURRENCY, f"one {'existing' if name == 'real' else 'unknown'} key id with 80 wrong secrets from 80 addresses: {ok401} lookups, then 429 (limit {PER_CREDENTIAL})")
        check(bool(too) and all(r.code == "AUTH_THROTTLED" and r.retry_after for r in too), f"{name}: the throttled answers are 429 AUTH_THROTTLED with Retry-After")
    throttled_bodies = {name: next((r.body for r in cred[name] if r.status == 429), None) for name in cred}
    check(all(isinstance(body, dict) and body.get("error", {}).get("code") == "AUTH_THROTTLED" for body in throttled_bodies.values()),
          "both floods produced a 429 body in the standard envelope with code AUTH_THROTTLED (so the comparison below is not vacuous)")
    bodies = {name: lib.scrub(body) for name, body in throttled_bodies.items()}
    check(bodies["real"] == bodies["ghost"], "throttled bodies are identical for an existing and an unknown key id", body=bodies["real"])
    first_401 = lib.scrub(next(r.body for r in cred["real"] if r.status == 401))
    check(first_401 == lib.scrub(unknown.body), "the 401 body of a wrong secret equals the unknown-key 401 body")

    # --- the audit row: exactly one for the window, with both processes flooding the same existing key id
    def throttled_rows() -> list[dict[str, Any]]:
        return [r for r in lib.person("owner", a.url).get("/v1/audit", params={"limit": 200, "action": "auth.throttled"}).json()["items"] if r["action"] == "auth.throttled"]

    deadline = time.monotonic() + 8
    rows = throttled_rows()
    while not rows and time.monotonic() < deadline:
        time.sleep(0.5)
        rows = throttled_rows()
    check(len(rows) == 1, "exactly one auth.throttled audit row for the window (two processes flooded the same key id)", rows=len(rows),
          detail={k: rows[0].get(k) for k in ("actor", "outcome", "resource_id")} if rows else None)

    # --- the spread flood (random keys, random addresses): fills the global ceiling. Flood only (plus a light session probe), so the
    # database figure is the flood's own.
    flood = lambda client, base, i: _bad(client, base, forwarded=lib.jitter_ip())  # noqa: E731
    probe = Valid(urls, token)
    probe.one = _light(probe)  # type: ignore[method-assign]
    flooded, good, seconds, tx = asyncio.run(_phase(urls, probe, flood, 8.0, 24))
    seen = lib.count(flooded, 401)
    total_failed = seen + lib.count(spoofed["rotate"], 401) + lib.count(cred["real"], 401) + lib.count(cred["ghost"], 401) + 40 + 5 + 3
    info(spread_flood=lib.tally(flooded), requests_per_s=round(len(flooded) / seconds), probe=lib.tally(good), db_tx=tx, db_tx_per_s=round(tx / seconds, 1),
         db_transactions_per_admitted_failure=round(tx / max(1, seen), 1))
    check(lib.count(flooded, 429) > 0 and all(r.code == "AUTH_THROTTLED" for r in flooded if r.status == 429), "spread flood: the global ceiling answers 429 AUTH_THROTTLED", throttled=lib.count(flooded, 429))
    check(total_failed <= GLOBAL_CEILING + PROCESSES + 24 + 2 * CONCURRENCY, f"failed authentications in the window ({total_failed}) stay within the global ceiling ({GLOBAL_CEILING}) plus in-flight requests")
    _only_401_429(flooded, "the flood saw only 401 and 429 (no 5xx)")
    check(_valid_ok(good), "a valid session was never refused while the global window closed", probes=len(good), tally=lib.tally(good))
    # The ceiling is closed. A flood now must cost counters at most: flood only, after the background work of the last steps settled.
    idle = lib.quiet()
    closed, _, seconds2, tx2 = asyncio.run(_phase(urls, Valid(urls, token), flood, 6.0, 24, with_valid=False))
    rate, req_rate = tx2 / seconds2, len(closed) / seconds2
    info(closed_window_flood=lib.tally(closed), requests_per_s=round(req_rate), db_tx=tx2, db_tx_per_s=round(rate, 1), idle_tx_per_s=idle)
    check(lib.count(closed, 401) <= 2 * PROCESSES, "closed window: zero lookups (a request that finds the window closed does no work)", lookups=lib.count(closed, 401))
    check(tx2 < 0.05 * len(closed) and rate < 60, f"closed window: database transactions ({tx2}, {rate:.0f}/s) are under 5% of the {len(closed)} flood requests and under 60/s (bound: counters, not requests)")
    # --- COLD KEY DURING A FLOOD (ADR-153 point 3, KI-175): a valid key that neither process has ever authenticated, first used while
    # the flood keeps the global window closed. It used to be refused with 429 until the window ended.
    async def closed_everywhere() -> list[lib.Reply]:
        async with httpx.AsyncClient(timeout=30) as client:
            return [await _bad(client, base) for base in urls]

    still_closed = asyncio.run(closed_everywhere())
    check(all(r.status == 429 and r.code == "AUTH_THROTTLED" for r in still_closed), "before the cold key is used the global window is closed on both processes (a fresh random key gets 429)", tally=lib.tally(still_closed))

    class ColdKey(Valid):
        async def one(self, client: httpx.AsyncClient, i: int) -> lib.Reply:
            base = self.urls[i % len(self.urls)]
            reply = await lib.fire(client, "GET", f"{base}/v1/runs", headers=lib.bearer(cold_key))
            reply.code = "cold" if reply.status == 200 else reply.code
            return reply

    cold_flooded, cold_replies, _, _ = asyncio.run(_phase(urls, ColdKey(urls, token), flood, 6.0, 24))
    info(cold_key=lib.tally(cold_replies), flood=lib.tally(cold_flooded))
    check(bool(cold_replies) and all(r.code == "cold" for r in cold_replies),
          "a valid key never used by either API process authenticated on every request while the flood kept the global window closed", requests=len(cold_replies), tally=lib.tally(cold_replies))
    check(lib.count(cold_flooded, 429) > 0 and all(r.status in {401, 429} for r in cold_flooded), "and the flood stayed closed throughout (only 401s from in-flight requests and 429s)", tally=lib.tally(cold_flooded))

    # Valid traffic at full rate (ingestion with 20-event batches and sessions, both processes) during a flood against the closed window.
    full = Valid(urls, token)
    closed2, good2, seconds3, _ = asyncio.run(_phase(urls, full, flood, 8.0, 24))
    check(_valid_ok(good2), "a warmed ingestion key and a valid session were NEVER refused during the flood", valid_requests=len(good2), tally=lib.tally(good2))
    _only_401_429(closed2, "the flood saw only 401 and 429")
    during = _p95(good2)
    info(valid_p95_ms_before=_p95(before), valid_p95_ms_during_flood=during, ratio=round(during / max(1.0, _p95(before)), 2), flood_requests_per_s=round(len(closed2) / seconds3),
         note="the load generator, the API processes and the database share one machine; the ratio is honest, not a claim of no effect")
    check(during < 5000, "valid p95 during the flood stays bounded (under 5 s)", p95_ms=during)

    # --- the control: the same kind of flood with the throttle OFF costs a database transaction or two per request
    lib.quiet()

    async def control() -> tuple[list[lib.Reply], int]:
        async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=CONCURRENCY + 2)) as client:
            before_tx = await asyncio.to_thread(lib.transactions)
            got = await lib.run_for(4.0, CONCURRENCY, lambda i: _bad(client, d.url, forwarded=lib.jitter_ip()))
            return got, await asyncio.to_thread(lib.transactions) - before_tx - 1

    got, ctx = asyncio.run(control())
    info(control_throttle_off=lib.tally(got), db_tx=ctx, transactions_per_request=round(ctx / max(1, len(got)), 2))
    check(lib.count(got, 429) == 0 and ctx >= 0.5 * len(got), "control (throttle off): every flood request is a lookup, ~1+ transaction each: the test can see the load", requests=len(got), tx=ctx)

    # --- the real SDK with a throttled key: no crash, bounded time, bounded queue
    elapsed_window = time.monotonic() - window_started
    info(seconds_into_the_window=round(elapsed_window))
    sdk_env = {"E2E_SDK_ENDPOINT": a.url, "E2E_SDK_KEY": lib.bad_token_for(victim), "E2E_SDK_PROJECT": project}
    import json  # noqa: PLC0415
    import os  # noqa: PLC0415
    import subprocess  # noqa: PLC0415

    done = subprocess.run(["uv", "run", "--project", "packages/sdk-python", "python", "scripts/hardening_e2e_sdk.py"], cwd=lib.ROOT,  # noqa: S603, S607
                          env={**os.environ, **sdk_env}, capture_output=True, text=True, check=False, timeout=120)
    result: dict[str, Any] = {}
    for line in done.stdout.splitlines():
        if line.startswith("{"):
            result = json.loads(line)
    info(sdk=result, exit=done.returncode)
    check(done.returncode == 0 and result.get("host_finished") is True, "the host agent with a throttled key finished normally (exit 0, no exception)", stderr=done.stderr[-200:] if done.returncode else "")
    check(bool(result) and result["elapsed_seconds"] < 25, "shutdown stayed within its bound", elapsed_seconds=result.get("elapsed_seconds"))
    check(bool(result) and result["queue_size"] <= result["max_queue"], "the SDK queue stayed bounded", queue=result.get("queue_size"), max_queue=result.get("max_queue"))
    check(bool(result) and result["events_sent"] == 0 and result["dropped_total"] > 0 and result["internal_errors"] == 0,
          "events were dropped and counted, nothing was sent, no internal error", dropped=result.get("dropped_total"))

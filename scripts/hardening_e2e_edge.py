"""Gate 4 of scripts/hardening-e2e.sh: the edge (ADR-155) on a real stack.

Headers on API and web responses; /docs and /openapi.json absent in production and present in development; the request-body cap
sheds load with 503 BUSY (a declared size is reserved at admission, so those refusals come before any byte is read; chunked bodies are refused while they stream; concurrent large bodies, bounded process memory, recovery); production refuses to start
connected as a privileged database role (and starts with the documented override). The browser half (zero CSP violations across the
main pages) is e2e/hardening-real.spec.ts; the fixtures spec e2e/csp.spec.ts walks every route.
"""

import asyncio
import json
import threading
import time
from typing import Any

import httpx
from sqlalchemy.engine import make_url

import hardening_e2e_lib as lib
from hardening_e2e_lib import check, gate, info

MIB = 1024 * 1024
PRODUCTION: dict[str, str | None] = {
    "ABB_ENVIRONMENT": "production", "ABB_WEB_ORIGIN": "https://abb.example.test", "ABB_OIDC_ISSUER": None, "ABB_OIDC_CLIENT_ID": None,
    "ABB_ALERTS_DELIVERY_ENABLED": "false", "ABB_ALLOW_DEV_SESSIONS": "0",
}


def _headers_of(response: httpx.Response) -> dict[str, str]:
    return {k.lower(): v for k, v in response.headers.items()}


def gate4() -> None:
    gate("gate4-edge")
    api_url = lib.main_urls()[0]
    web = f"http://localhost:{lib.os.environ['E2E_WEB_PORT']}"

    # ------------------------------------------------------------------ API headers (development, http)
    api_expected = {
        "x-content-type-options": "nosniff", "x-frame-options": "DENY", "referrer-policy": "no-referrer", "cache-control": "no-store",
        "content-security-policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    }
    for label, response in (("200 /healthz", httpx.get(f"{api_url}/healthz")), ("401 /v1/me", httpx.get(f"{api_url}/v1/me")),
                            ("404 unknown path", httpx.get(f"{api_url}/v1/nope")), ("401 bad key", httpx.get(f"{api_url}/v1/runs", headers=lib.bearer(lib.random_token())))):
        got = _headers_of(response)
        missing = {k: (got.get(k), v) for k, v in api_expected.items() if got.get(k) != v}
        check(not missing and "camera=()" in got.get("permissions-policy", ""), f"API {label}: nosniff, frame denial, referrer, no-store, deny-all CSP, permissions-policy", missing=missing)
        check("strict-transport-security" not in got, f"API {label}: no HSTS over http (the deployment did not say it is behind HTTPS)")

    # ------------------------------------------------------------------ web headers
    nonces = set()
    for path in ("/login", "/api/health", "/w/anything"):
        response = httpx.get(f"{web}{path}", follow_redirects=False)
        got = _headers_of(response)
        csp = got.get("content-security-policy", "")
        base_ok = (got.get("x-content-type-options") == "nosniff" and got.get("x-frame-options") == "DENY" and got.get("referrer-policy") == "no-referrer"
                   and got.get("cross-origin-opener-policy") == "same-origin" and got.get("cross-origin-resource-policy") == "same-origin"
                   and "camera=()" in got.get("permissions-policy", "") and "strict-transport-security" not in got)
        check(base_ok, f"web {path} ({response.status_code}): nosniff, frame denial, referrer, COOP/CORP, permissions-policy, no HSTS over http")
        if path == "/login":
            marker = "'nonce-"
            nonces.add(csp.split(marker, 1)[1].split("'", 1)[0] if marker in csp else "")
            script_src = next((d for d in csp.split(";") if d.strip().startswith("script-src")), "")
            check("'strict-dynamic'" in script_src and "'unsafe-inline'" not in script_src and "'unsafe-eval'" not in script_src and "default-src 'self'" in csp
                  and "frame-ancestors 'none'" in csp and "connect-src 'self'" in csp and "object-src 'none'" in csp, "web /login: nonce CSP, strict-dynamic, no unsafe-inline or unsafe-eval for scripts", script_src=script_src[:90])
        else:
            check(csp.startswith("default-src 'none'"), f"web {path}: routes and redirects get the deny-all policy", csp=csp[:60])
    for _ in range(2):
        csp = _headers_of(httpx.get(f"{web}/login")).get("content-security-policy", "")
        nonces.add(csp.split("'nonce-", 1)[1].split("'", 1)[0] if "'nonce-" in csp else "")
    check(len(nonces) == 3 and "" not in nonces, "web: a fresh nonce for every response (3 requests, 3 different nonces)")

    # ------------------------------------------------------------------ /docs per environment
    dev = {path: httpx.get(f"{api_url}{path}").status_code for path in ("/docs", "/redoc", "/openapi.json")}
    check(all(code == 200 for code in dev.values()), "development: /docs, /redoc and /openapi.json are served", status=dev)
    runtime_role = lib.start_api("production-runtime", lib.OIDC_PORT + 7, PRODUCTION, log_prefix="api")
    try:
        prod = {path: httpx.get(f"{runtime_role.url}{path}").status_code for path in ("/docs", "/redoc", "/openapi.json")}
        check(all(code == 404 for code in prod.values()), "production (as the least-privilege role): /docs, /redoc and /openapi.json are absent (404)", status=prod)
        hsts = _headers_of(httpx.get(f"{runtime_role.url}/healthz")).get("strict-transport-security")
        check(hsts == "max-age=31536000; includeSubDomains", "production with an https origin: HSTS is sent (1 year, includeSubDomains, no preload)", hsts=hsts)
        check(httpx.get(f"{runtime_role.url}/readyz").status_code == 200, "production starts as the runtime role (not privileged)")
    finally:
        runtime_role.stop()

    # ------------------------------------------------------------------ production refuses a privileged role
    password = make_url(lib.os.environ["E2E_OWNER_DB_URL"]).password or "-"
    refused = lib.start_api("production-owner", lib.OIDC_PORT + 8, PRODUCTION, owner_role=True, wait=False, log_prefix="expected")
    try:
        code = refused.popen.wait(timeout=40)
    except lib.subprocess.TimeoutExpired:
        code = None
    text = refused.log.read_text(errors="replace")
    check(code not in (None, 0), "production connected as the database OWNER exits non-zero at startup", exit_code=code)
    check("Refusing to start" in text and "least-privilege runtime role" in text, "the fixed refusal message is logged", reasons=[r for r in ("superuser", "bypass_rls", "events_rewritable", "events_owner", "schema_create") if r in text])
    leaked = [needle for needle in (password, "postgresql", "asyncpg://", lib.os.environ["E2E_OWNER_DB_URL"].split("@")[-1]) if needle and needle in text]
    check(not leaked, "the refusal and the log carry no credentials, role name, host or connection string", leaked=len(leaked))
    override = lib.start_api("production-override", lib.OIDC_PORT + 9, {**PRODUCTION, "ABB_ALLOW_PRIVILEGED_DB_ROLE": "1"}, owner_role=True, wait=True, log_prefix="api")
    try:
        check(httpx.get(f"{override.url}/readyz").status_code == 200, "ABB_ALLOW_PRIVILEGED_DB_ROLE=1 starts it")
        warned = lib.log_lines(override.log, "ABB_ALLOW_PRIVILEGED_DB_ROLE is set")
        text2 = override.log.read_text(errors="replace")
        named = [r for r in ("events_rewritable", "events_owner", "schema_create", "superuser") if any(r in line for line in text2.splitlines() if "ABB_ALLOW_PRIVILEGED_DB_ROLE is set" in line)]
        check(bool(warned) and "events_rewritable" in named, "and logs a warning naming the accepted reasons on every start", reasons=named)
        check(password not in text2 and "asyncpg://" not in text2, "the override's log carries no credentials either")
    finally:
        override.stop()

    # ------------------------------------------------------------------ the body-bytes cap
    _body_cap()
    lib.finish()


def _bodies(count: int, events: int = 1000) -> list[bytes]:
    """`count` distinct ~4.5 MiB ingestion batches (valid events; every body parses and is stored on first sight)."""
    import sys  # noqa: PLC0415

    sys.path.insert(0, str(lib.ROOT / "scripts"))
    from bench_ingest import make_events  # noqa: PLC0415

    out = []
    for i in range(count):
        run = lib.run_ids()
        batch = make_events(run, 1, events)
        for event in batch:
            event["payload"] = {"response": "x" * 4300}
        out.append(json.dumps({"events": batch}).encode())
    return out


async def _hammer(url: str, token: str, bodies: list[bytes], seconds: float, clients: int, rss: Any) -> tuple[list[lib.Reply], list[lib.Reply]]:
    headers = {**lib.bearer(token), "Content-Type": "application/json"}
    small_headers = {**lib.bearer(token)}
    limits = httpx.Limits(max_connections=clients + 8)
    async with httpx.AsyncClient(timeout=120, limits=limits) as client:
        deadline = time.monotonic() + seconds

        async def big(i: int) -> lib.Reply:
            reply = await lib.fire(client, "POST", f"{url}/v1/events/batch", content=bodies[i % len(bodies)], headers=headers)
            if reply.status == 503:
                await asyncio.sleep(0.25)  # what an SDK's backoff does, roughly
            return reply

        async def small(i: int) -> lib.Reply:
            run = lib.run_ids()
            reply = await lib.fire(client, "POST", f"{url}/v1/events/batch", json=lib.batch_of([lib.event(run, 1)]), headers=small_headers)
            await asyncio.sleep(0.1)
            return reply

        async def loop(make: Any, offset: int) -> list[lib.Reply]:
            got: list[lib.Reply] = []
            n = offset
            while time.monotonic() < deadline:
                got.append(await make(n))
                n += clients
            return got

        results = await asyncio.gather(*[loop(big, k) for k in range(clients)], *[loop(small, k) for k in range(2)])
    large = [r for part in results[:clients] for r in part]
    little = [r for part in results[clients:] for r in part]
    rss["stop"] = True
    return large, little


def _sample(proc: lib.Proc, state: dict[str, Any]) -> threading.Thread:
    def run() -> None:
        while not state.get("stop"):
            state["peak"] = max(state.get("peak", 0.0), proc.rss_mib())
            time.sleep(0.02)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def _body_cap() -> None:
    project = lib.new_project("bodycap")
    token = lib.new_key(project, ["events:write"], "bodycap")
    generous = {
        "ABB_RATE_LIMIT_EVENTS_PER_SECOND": "1000000", "ABB_RATE_LIMIT_BURST_EVENTS": "10000000", "ABB_RATE_LIMIT_BYTES_PER_SECOND": "10000000000",
        "ABB_RATE_LIMIT_BURST_BYTES": "100000000000", "ABB_QUOTAS_ENABLED": "0", "ABB_LOG_LEVEL": "ERROR",
    }
    bodies = _bodies(6)
    size = len(bodies[0])
    clients, seconds = 48, 10.0
    outcome: dict[str, dict[str, Any]] = {}
    for name, cap in (("capped", str(8 * MIB)), ("uncapped", str(8 * 1024 * MIB))):
        proc = lib.start_api(f"bodycap-{name}", lib.OIDC_PORT + 10, {**generous, "ABB_MAX_BODY_BYTES_IN_FLIGHT": cap})
        try:
            httpx.post(f"{proc.url}/v1/events/batch", content=bodies[0], headers={**lib.bearer(token), "Content-Type": "application/json"}, timeout=60)  # warm
            baseline = proc.rss_mib()
            state: dict[str, Any] = {"peak": baseline}
            sampler = _sample(proc, state)
            large, little = asyncio.run(_hammer(proc.url, token, bodies, seconds, clients, state))
            sampler.join(5)
            lib.time.sleep(1.0)
            # recovery: a normal batch is accepted at once and readiness holds
            after = [httpx.post(f"{proc.url}/v1/events/batch", json=lib.batch_of([lib.event(lib.run_ids(), n + 1) for n in range(100)]), headers=lib.bearer(token), timeout=30) for _ in range(5)]
            outcome[name] = {"baseline_mib": round(baseline, 1), "peak_mib": round(state["peak"], 1), "growth_mib": round(state["peak"] - baseline, 1), "large": lib.tally(large),
                             "small_alongside": lib.tally(little), "recovery": [r.status_code for r in after], "ready": httpx.get(f"{proc.url}/readyz").status_code,
                             "rss_after_mib": round(proc.rss_mib(), 1), "retry_after": sorted({r.retry_after for r in large if r.status == 503}),
                             "busy_code": sorted({r.code for r in large if r.status == 503})}
        finally:
            proc.stop()
    info(body_cap=outcome, body_mib=round(size / MIB, 2), clients=clients, seconds=seconds)
    capped, uncapped = outcome["capped"], outcome["uncapped"]
    busy = sum(v for k, v in capped["large"].items() if k.startswith("503"))
    check(busy > 0 and capped["busy_code"] == ["BUSY"] and all(r for r in capped["retry_after"]), f"capped: concurrent {size / MIB:.1f} MiB bodies are shed with 503 BUSY and Retry-After ({busy} refusals)", tally=capped["large"], retry_after=capped["retry_after"])
    check(all(k in {"202"} or k.startswith("503") for k in capped["large"]), "capped: nothing but 202 and 503 (no 5xx crash, no connection errors)", tally=capped["large"])
    check(not any(k == "503 BUSY" for k in uncapped["large"]) and uncapped["growth_mib"] > 300, "control (cap effectively off): nothing is shed as BUSY and memory balloons (the test can see the problem); the process degrades to DEPENDENCY_UNAVAILABLE instead", tally=uncapped["large"], growth_mib=uncapped["growth_mib"])
    check(capped["growth_mib"] < 0.6 * uncapped["growth_mib"], f"memory is bounded: capped growth {capped['growth_mib']} MiB vs uncapped {uncapped['growth_mib']} MiB (peak {capped['peak_mib']} vs {uncapped['peak_mib']})")
    check(all(code == 202 for code in capped["recovery"]) and capped["ready"] == 200, "recovery: normal 100-event batches are accepted at once after the flood and /readyz holds", recovery=capped["recovery"])
    small_bad = {k: v for k, v in capped["small_alongside"].items() if k != "202"}
    check(not small_bad, "small requests alongside (a 64 KiB allowance) were not starved by the large bodies", tally=capped["small_alongside"])

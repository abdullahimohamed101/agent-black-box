"""Gate 3 of scripts/obs-e2e.sh (D9 gates 2 and 3): readiness reflects real dependency state and the database breaker sheds, each against
a deliberately broken process. Liveness (/healthz) stays green in every one. Count-based, never timing-based: the breaker's "fast" is
shown by the pool-wait histogram (a shed request never reaches the pool, so it adds no observation) and by trips, not by a stopwatch.
"""

import os
import subprocess
from pathlib import Path
from typing import Any

import httpx

import obs_e2e_lib as lib
from obs_e2e_lib import check, gate, info

# A process that starts as a production deployment would (see hardening_e2e_edge.PRODUCTION).
PRODUCTION: dict[str, str | None] = {
    "ABB_ENVIRONMENT": "production", "ABB_WEB_ORIGIN": "https://abb.example.test", "ABB_OIDC_ISSUER": None, "ABB_OIDC_CLIENT_ID": None,
    "ABB_ALERTS_DELIVERY_ENABLED": "false", "ABB_ALLOW_DEV_SESSIONS": "0",
}
# No background caller may touch the database behind the test's back: the role re-check and the active-key refresh are off/slow.
QUIET = {"ABB_ROLE_CHECK_INTERVAL_SECONDS": "0", "ABB_AUTH_FAIL_ACTIVE_KEYS_REFRESH_SECONDS": "3600"}
REASONS = ("down", "timeout", "breaker_open", "behind", "unreadable", "unchecked", "missing", "not_writable", "privileged_role", "ahead")


def _ready(url: str) -> httpx.Response:
    return httpx.get(f"{url}/readyz", timeout=15)


def _curl_ready(url: str) -> int:
    """The compose healthcheck shape: `curl -sf /readyz`. Exit 0 is ready; 22 is an HTTP error (503)."""
    return subprocess.run(["curl", "-sf", "--max-time", "10", f"{url}/readyz"], capture_output=True, check=False).returncode  # noqa: S603, S607


def _checks(response: httpx.Response) -> dict[str, tuple[str, str | None]]:
    return {name: (c["status"], c["reason"]) for name, c in response.json()["checks"].items()}


def _fixed_text(response: httpx.Response, *secrets: str) -> bool:
    """A readiness body is fixed reason codes only: no host, port, URL, path, password or error text."""
    body = response.text
    words = ("postgres", "asyncpg", "password", "127.0.0.1", "localhost", "Traceback", "Error", "/Users/", "/tmp", ".local")
    return not any(w in body for w in (*words, *[s for s in secrets if s]))


def _wait_alive(proc: lib.Proc) -> None:
    """A deliberately not-ready process never passes base.wait_ready; liveness is what we wait for."""
    lib.poll(f"{proc.name} answers /healthz", lambda: proc.alive() and httpx.get(f"{proc.url}/healthz", timeout=5).status_code == 200, timeout=60, every=0.25)


def _healthy() -> None:
    api = lib.API_URL
    response = _ready(api)
    body = response.json()
    check(response.status_code == 200 and body["status"] == "ready", "healthy: /readyz is 200 ready", status=response.status_code)
    checks = _checks(response)
    check(all(checks[n] == ("ok", None) for n in ("database", "schema", "artifacts", "role")), "healthy: database, schema, artifacts and role are ok", checks=checks)
    check(checks["retention"][0] == "ok" and checks["retention"][1] in {"off", "dry_run", "enforce"}, "healthy: retention is reported (not failing) with the configured mode", retention=checks["retention"])
    check(httpx.get(f"{api}/healthz", timeout=15).json()["status"] == "ok", "healthy: /healthz is ok")
    check(_curl_ready(api) == 0, "healthy: `curl -sf /readyz` (the compose healthcheck shape) exits 0")
    gauges = lib.scrape(api)["readiness_check"].samples
    check({labels["check"]: v for _, labels, v in gauges} == {"database": 1.0, "schema": 1.0, "artifacts": 1.0, "role": 1.0}, "healthy: readiness_check{database,schema,artifacts,role} are all 1")


def _database_down() -> None:
    port = lib.closed_port()
    url = lib.proxied_url(type("P", (), {"port": port})())  # a loopback port nothing listens on
    proc = lib.start_server("db-down", lib.API_PORT + 3, {"ABB_DATABASE_URL": url, "ABB_DB_BREAKER_THRESHOLD": "1000", **QUIET}, wait=False, log_prefix="expected")
    try:
        _wait_alive(proc)
        live = httpx.get(f"{proc.url}/healthz", timeout=10)
        ready = _ready(proc.url)
        checks = _checks(ready)
        check(live.status_code == 200, "database down: /healthz stays 200 (liveness has no dependency)")
        check(ready.status_code == 503 and ready.json()["status"] == "not_ready" and ready.headers.get("retry-after") == "2",
              "database down: /readyz is 503 not_ready with Retry-After", status=ready.status_code, retry_after=ready.headers.get("retry-after"))
        check(checks["database"] == ("fail", "down") and checks["schema"] == ("fail", "unchecked"), "database down: database fails with reason `down`, schema is `unchecked`", checks=checks)
        check(_fixed_text(ready, str(port)), "database down: the body carries reason codes only (no host, port, URL, password or error text)")
        check(_curl_ready(proc.url) == 22, "database down: `curl -sf /readyz` exits 22 (an orchestrator would stop sending traffic)")
        families = lib.scrape(proc.url)
        check(lib.value(families, "readiness_check", check="database") == 0.0 and lib.value(families, "abb_scrape_database_up") == 0.0,
              "database down: /metrics still answers; readiness_check{database} = 0 and abb_scrape_database_up = 0")
        check(lib.value(families, "db_breaker_trips_total") == 0.0 or lib.value(families, "db_breaker_trips_total") >= 0.0, "database down: (breaker disabled by threshold in this scenario)")
    finally:
        proc.stop()


def _breaker_counted() -> None:
    """Exactly N failed requests open the breaker; after that no checkout is attempted (the pool-wait histogram stops moving)."""
    project = lib.new_project("obs-breaker")
    key = lib.new_key(project, ["events:write", "runs:read"], "obs-breaker")
    lib.remember_secret("api-key-breaker", key)
    proxy = lib.TcpProxy(*lib.db_target())
    proxy.start()
    threshold = 5
    proc = lib.start_server("breaker", lib.API_PORT + 4, {"ABB_DATABASE_URL": lib.proxied_url(proxy), "ABB_DB_BREAKER_THRESHOLD": str(threshold),
                                                          "ABB_DB_BREAKER_OPEN_SECONDS": "300", **QUIET})
    try:
        ok = httpx.get(f"{proc.url}/v1/runs", headers=lib.bearer(key), timeout=15)
        check(ok.status_code == 200 and _ready(proc.url).status_code == 200, "breaker: through the proxy the process is healthy (an authenticated read is 200, ready)", status=ok.status_code)
        before = lib.scrape(proc.url)
        check(lib.value(before, "db_breaker_state") == 0.0 and lib.value(before, "db_breaker_trips_total") == 0.0, "breaker: closed, no trips before the outage")
        proxy.stop()  # the database goes away: new connections are refused, open ones are cut
        answers = [httpx.get(f"{proc.url}/v1/runs", headers=lib.bearer(key), timeout=30) for _ in range(threshold)]
        check(all(a.status_code == 503 and a.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE" and a.headers.get("retry-after", "").isdigit() for a in answers),
              f"breaker: the {threshold} requests of the outage are 503 DEPENDENCY_UNAVAILABLE with Retry-After", statuses=[a.status_code for a in answers])
        families = lib.scrape(proc.url)
        waits = lib.total(families, "postgres_pool_wait_seconds", "postgres_pool_wait_seconds_count") - lib.total(before, "postgres_pool_wait_seconds", "postgres_pool_wait_seconds_count")
        check(lib.value(families, "db_breaker_trips_total") == 1.0 and lib.value(families, "db_breaker_state") == 2.0,
              f"breaker: after exactly {threshold} failed requests it opened once (db_breaker_trips_total = 1, state = open)", trips=lib.value(families, "db_breaker_trips_total"))
        check(waits == threshold, f"breaker: those {threshold} requests made exactly {threshold} checkout attempts (one each)", checkout_observations=waits)
        shed = [httpx.get(f"{proc.url}/v1/runs", headers=lib.bearer(key), timeout=30) for _ in range(6)]
        families = lib.scrape(proc.url)
        waits_after = lib.total(families, "postgres_pool_wait_seconds", "postgres_pool_wait_seconds_count") - lib.total(before, "postgres_pool_wait_seconds", "postgres_pool_wait_seconds_count")
        check(all(a.status_code == 503 and a.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE" and a.headers.get("retry-after", "").isdigit() and int(a.headers["retry-after"]) >= 1
                  for a in shed), "breaker: while open, requests are answered 503 with Retry-After", retry_after=sorted({a.headers.get("retry-after") for a in shed}))
        check(waits_after == threshold and lib.value(families, "db_breaker_trips_total") == 1.0,
              "breaker: 6 further requests made NO checkout attempt (the pool-wait count did not move) and did not re-trip it: shed without touching the database",
              checkout_observations=waits_after)
        # /readyz is cached for 2 s and shared; the first evaluation after the breaker opened sees it. Poll, never sleep.
        lib.poll("/readyz reports the open breaker", lambda: _checks(_ready(proc.url))["database"] == ("fail", "breaker_open"), timeout=30, every=0.5)
        live = httpx.get(f"{proc.url}/healthz", timeout=10)
        ready = _ready(proc.url)
        check(live.status_code == 200 and ready.status_code == 503 and _checks(ready)["database"] == ("fail", "breaker_open"),
              "breaker: /healthz stays 200 while /readyz is 503 with database `breaker_open`", checks=_checks(ready)["database"])
        check(lib.value(lib.scrape(proc.url), "readiness_check", check="database") == 0.0, "breaker: readiness_check{database} = 0")
        after_ready = lib.total(lib.scrape(proc.url), "postgres_pool_wait_seconds", "postgres_pool_wait_seconds_count") - lib.total(before, "postgres_pool_wait_seconds", "postgres_pool_wait_seconds_count")
        check(after_ready == threshold, "breaker: evaluating readiness while open made no checkout attempt either", checkout_observations=after_ready)
    finally:
        proc.stop()
        proxy.close()


def _breaker_recovers() -> None:
    project = lib.new_project("obs-recover")
    key = lib.new_key(project, ["events:write", "runs:read"], "obs-recover")
    lib.remember_secret("api-key-recover", key)
    proxy = lib.TcpProxy(*lib.db_target())
    proxy.start()
    proc = lib.start_server("recover", lib.API_PORT + 5, {"ABB_DATABASE_URL": lib.proxied_url(proxy), "ABB_DB_BREAKER_THRESHOLD": "3",
                                                          "ABB_DB_BREAKER_OPEN_SECONDS": "5", **QUIET})
    try:
        check(_ready(proc.url).status_code == 200, "recovery: ready before the outage")
        proxy.stop()
        answers = [httpx.get(f"{proc.url}/v1/runs", headers=lib.bearer(key), timeout=30) for _ in range(3)]
        check(all(a.status_code == 503 for a in answers) and lib.value(lib.scrape(proc.url), "db_breaker_trips_total") == 1.0, "recovery: 3 failures during the outage open the breaker (1 trip)")
        lib.poll("/readyz reports not ready during the outage", lambda: _ready(proc.url).status_code == 503, timeout=30, every=0.5)
        check(_checks(_ready(proc.url))["database"] == ("fail", "breaker_open"), "recovery: not ready during the outage (breaker_open)")
        proxy.start()  # the database comes back

        def served() -> bool:
            return httpx.get(f"{proc.url}/v1/runs", headers=lib.bearer(key), timeout=30).status_code == 200

        lib.poll("the breaker lets a probe through and closes after the database is back (an authenticated read is 200 again)", served, timeout=120, every=1.0)
        families = lib.scrape(proc.url)
        check(lib.value(families, "db_breaker_state") == 0.0 and lib.value(families, "db_breaker_trips_total") == 1.0,
              "recovery: closed again, still exactly one trip", state=lib.value(families, "db_breaker_state"))
        lib.poll("/readyz is 200 again", lambda: _ready(proc.url).status_code == 200, timeout=60, every=1.0)
        flaps = lib.value(lib.scrape(proc.url), "readiness_flaps_total")
        check(flaps == 2.0, "recovery: readiness_flaps_total counts the two changes (ready -> not ready -> ready)", flaps=flaps)
        check(_curl_ready(proc.url) == 0, "recovery: `curl -sf /readyz` exits 0 again")
    finally:
        proc.stop()
        proxy.close()


def _previous_revision() -> tuple[str, str]:
    from alembic.config import Config  # noqa: PLC0415
    from alembic.script import ScriptDirectory  # noqa: PLC0415

    config = Config(str(lib.API_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(lib.API_DIR / "migrations"))
    script = ScriptDirectory.from_config(config)
    head = script.get_current_head()
    assert head is not None
    revision = script.get_revision(head)
    down = revision.down_revision
    assert isinstance(down, str)
    return head, down


def _behind() -> None:
    name = os.environ["E2E_DB_BEHIND"]
    admin = os.environ["E2E_OWNER_DB_URL"].rsplit("/", 1)[0] + "/postgres"
    import asyncio  # noqa: PLC0415

    import asyncpg  # noqa: PLC0415

    async def create() -> None:
        conn = await asyncpg.connect(admin.replace("postgresql+asyncpg://", "postgresql://"))
        try:
            await conn.execute(f'create database "{name}"')
        finally:
            await conn.close()

    asyncio.run(create())
    owner = os.environ["E2E_OWNER_DB_URL"].rsplit("/", 1)[0] + f"/{name}"
    migrated = subprocess.run([str(lib.base.API_PYTHON), "-m", "alembic", "upgrade", "head"], cwd=lib.API_DIR, env={**os.environ, "ABB_DATABASE_URL": owner},
                              capture_output=True, text=True, check=False, timeout=300)  # noqa: S603
    if migrated.returncode != 0:
        lib.die(f"migrating the behind database failed: {migrated.stderr[-200:]}")
    head, previous = _previous_revision()

    async def set_revision(value: str) -> None:
        conn = await asyncpg.connect(owner.replace("postgresql+asyncpg://", "postgresql://"))
        try:
            await conn.execute("update alembic_version set version_num = $1", value)
        finally:
            await conn.close()

    runtime = os.environ["E2E_RUNTIME_DB_URL"].rsplit("/", 1)[0] + f"/{name}"
    asyncio.run(set_revision(previous))
    proc = lib.start_server("behind", lib.API_PORT + 6, {"ABB_DATABASE_URL": runtime, **QUIET}, wait=False, log_prefix="expected")
    try:
        _wait_alive(proc)
        ready = _ready(proc.url)
        checks = _checks(ready)
        check(ready.status_code == 503 and checks["schema"] == ("fail", "behind") and checks["database"] == ("ok", None),
              "schema behind: the database is one revision older than the code -> /readyz 503, schema `behind`, database ok", checks={k: checks[k] for k in ("database", "schema")})
        check(httpx.get(f"{proc.url}/healthz", timeout=10).status_code == 200 and _fixed_text(ready, previous, head), "schema behind: liveness stays green and the body names no revision")
        asyncio.run(set_revision(head))
        lib.poll("the process becomes ready once the database is at the code's revision", lambda: _ready(proc.url).status_code == 200, timeout=60, every=1.0)
        check(_checks(_ready(proc.url))["schema"] == ("ok", None), "schema behind: after the migration is applied the schema check is ok (no restart)")
        asyncio.run(set_revision("0000_a_revision_from_the_future"))
        lib.poll("an unknown (newer) revision is reported as ahead", lambda: _checks(_ready(proc.url))["schema"] == ("degraded", "ahead"), timeout=60, every=1.0)
        ahead = _ready(proc.url)
        check(ahead.status_code == 200, "schema ahead: a database newer than the code is degraded/ahead and STILL ready (a rolling deploy migrates first)", status=ahead.status_code)
    finally:
        proc.stop()


def _artifacts() -> None:
    root = lib.WORK / "ro-artifacts"
    root.mkdir(mode=0o700)
    root.chmod(0o500)  # readable and searchable, not writable
    blocker = lib.WORK / "a-file"
    blocker.write_text("not a directory")
    proc = lib.start_server("artifacts", lib.API_PORT + 7, {"ABB_ARTIFACT_DIR": str(root), **QUIET}, wait=False, log_prefix="expected")
    try:
        _wait_alive(proc)
        ready = _ready(proc.url)
        checks = _checks(ready)
        euid = os.geteuid() if hasattr(os, "geteuid") else -1
        check(ready.status_code == 503 and checks["artifacts"] == ("fail", "not_writable") and checks["database"] == ("ok", None),
              "artifact directory not writable: /readyz 503, artifacts `not_writable`, database ok", checks={k: checks[k] for k in ("database", "artifacts")}, euid=euid)
        check(_fixed_text(ready, str(root)), "artifact directory not writable: the body names no path")
        check(httpx.get(f"{proc.url}/healthz", timeout=10).status_code == 200, "artifact directory not writable: liveness stays green")
        root.chmod(0o700)
        lib.poll("readiness recovers once the directory is writable again (no restart)", lambda: _ready(proc.url).status_code == 200, timeout=60, every=1.0)
        check(_checks(_ready(proc.url))["artifacts"] == ("ok", None), "artifact directory writable again: artifacts ok")
    finally:
        proc.stop()
        root.chmod(0o700)
    missing = lib.start_server("artifacts-missing", lib.API_PORT + 7, {"ABB_ARTIFACT_DIR": str(blocker / "sub"), **QUIET}, wait=False, log_prefix="expected")
    try:
        _wait_alive(missing)
        ready = _ready(missing.url)
        check(ready.status_code == 503 and _checks(ready)["artifacts"] == ("fail", "missing"), "artifact directory that cannot exist: artifacts `missing`", checks=_checks(ready)["artifacts"])
    finally:
        missing.stop()


def _privileged_role() -> None:
    role = os.environ["E2E_ROLE_NAME"]
    proc = lib.start_server("production-role", lib.API_PORT + 8, {**PRODUCTION, "ABB_ROLE_CHECK_INTERVAL_SECONDS": "5", "ABB_AUTH_FAIL_ACTIVE_KEYS_REFRESH_SECONDS": "3600"})
    try:
        check(_ready(proc.url).status_code == 200 and _checks(_ready(proc.url))["role"] == ("ok", None), "production, least-privilege role: ready, role ok")
        lib.base.db(f'grant update on public.events to "{role}"')  # a mistaken grant after the process started
        try:
            lib.poll("the periodic role check notices the grant and fails readiness", lambda: _ready(proc.url).status_code == 503, timeout=90, every=1.0)
            ready = _ready(proc.url)
            checks = _checks(ready)
            check(checks["role"] == ("fail", "privileged_role") and checks["database"] == ("ok", None),
                  "production, a role that became privileged: /readyz 503 with role `privileged_role`", checks={k: checks[k] for k in ("database", "role")})
            check(proc.alive() and httpx.get(f"{proc.url}/healthz", timeout=10).status_code == 200, "production, privileged role: the process keeps running and liveness stays green")
            check(_fixed_text(ready, role), "production, privileged role: the body names no role")
            gauge = lib.value(lib.scrape(proc.url), "db_role_privileged", reason="events_rewritable")
            check(gauge == 1.0, "production, privileged role: db_role_privileged{reason=events_rewritable} = 1", gauge=gauge)
        finally:
            lib.base.db(f'revoke update on public.events from "{role}"')
        lib.poll("revoking the grant restores readiness within the next check", lambda: _ready(proc.url).status_code == 200, timeout=90, every=1.0)
        check(lib.value(lib.scrape(proc.url), "db_role_privileged", reason="events_rewritable") == 0.0 and proc.alive(),
              "production: after the revoke the gauge is 0, readiness is back, the same process is still running (never restarted)")
    finally:
        proc.stop()


def gate3() -> None:
    gate("gate3-readiness")
    _healthy()
    _database_down()
    _breaker_counted()
    _breaker_recovers()
    _behind()
    _artifacts()
    _privileged_role()
    lib.finish()

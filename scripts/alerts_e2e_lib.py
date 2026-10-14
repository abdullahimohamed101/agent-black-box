"""Shared helpers of scripts/alerts-e2e.sh (Phase 17 step 4): environment, state, clients, the receivers' control client, event
builders, waiting, the database scan and the result log. Run in the API's environment (`uv run`). Secrets are never printed:
failures name the check, not a value.
"""

import asyncio
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import httpx
from abb_event_schema.ids import IdKind, new_id

API = os.environ.get("E2E_API", "http://localhost:8210")
SLUG = os.environ.get("E2E_WORKSPACE", "")
SLUG2 = os.environ.get("E2E_WORKSPACE2", "")
ORIGIN = os.environ.get("E2E_ORIGIN", "http://localhost:3210")
STATE_FILE = Path(os.environ.get("E2E_STATE", "/dev/null"))
WORK = Path(os.environ.get("E2E_WORK", "/tmp"))  # noqa: S108
ROOT = Path(os.environ.get("E2E_ROOT", "."))
RECV = os.environ.get("E2E_RECV", "http://127.0.0.1:8212")
MAIN_PORT = int(os.environ.get("E2E_MAIN_PORT", "8213"))
SLACK_PORT = int(os.environ.get("E2E_SLACK_PORT", "8214"))
FLAKY_PORT = int(os.environ.get("E2E_FLAKY_PORT", "8216"))
NET_FILE = Path(os.environ.get("E2E_NET_FILE", "/dev/null"))
RESULTS = Path(os.environ.get("E2E_RESULTS", "/dev/null"))
PLANT = os.environ.get("E2E_PLANT", "PLANTALERT")
SESSIONS = {
    "owner": "E2E_OWNER_SESSION",
    "admin": "E2E_ADMIN_SESSION",
    "developer": "E2E_DEVELOPER_SESSION",
    "viewer": "E2E_VIEWER_SESSION",
    "security": "E2E_SECURITY_SESSION",
    "billing": "E2E_BILLING_SESSION",
    "other_admin": "E2E_OTHER_ADMIN_SESSION",
}
PUBLIC_IP = "93.184.216.34"
# A new connection per request: uvicorn closes an idle keep-alive connection after 5 s and httpx does not retry a reset on a reused one.
NO_KEEPALIVE = httpx.Limits(max_keepalive_connections=0)

_failures: list[str] = []
_gate = "setup"


# ------------------------------------------------------------------------------------------------ results


def gate(name: str) -> None:
    global _gate
    _gate = name


def check(condition: bool, label: str, **evidence: Any) -> bool:
    """Record and print one assertion. A failure does not stop the gate (the rest still runs); `finish()` exits non-zero."""
    status = "PASS" if condition else "FAIL"
    shown = " ".join(f"{k}={json.dumps(v, default=str)}" for k, v in evidence.items())
    print(f"  {status} [{_gate}] {label}" + (f"  ({shown})" if shown else ""), flush=True)
    with RESULTS.open("a") as handle:
        handle.write(json.dumps({"gate": _gate, "label": label, "ok": bool(condition)}) + "\n")
    if not condition:
        _failures.append(label)
    return bool(condition)


def info(**evidence: Any) -> None:
    print("  observed: " + json.dumps(evidence, sort_keys=True, default=str), flush=True)


def finish() -> None:
    if _failures:
        print(f"ALERTS-E2E CHECK FAILED [{_gate}]: {len(_failures)} failed: {_failures}", file=sys.stderr)
        raise SystemExit(1)


def die(message: str) -> None:
    print(f"ALERTS-E2E CHECK FAILED [{_gate}]: {message}", file=sys.stderr)
    raise SystemExit(1)


# ------------------------------------------------------------------------------------------------ state


def state() -> dict[str, Any]:
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.stat().st_size else {}


def save(**values: Any) -> None:
    current = state()
    current.update(values)
    STATE_FILE.write_text(json.dumps(current))


def remember(bucket: str, **values: Any) -> None:
    current = state()
    current.setdefault(bucket, {}).update(values)
    STATE_FILE.write_text(json.dumps(current))


# ------------------------------------------------------------------------------------------------ clients

_clients: dict[str, httpx.Client] = {}


def person(role: str) -> httpx.Client:
    if role in _clients:
        return _clients[role]
    client = httpx.Client(base_url=API, cookies={"abb_session": os.environ[SESSIONS[role]]}, headers={"Origin": ORIGIN}, timeout=60, limits=NO_KEEPALIVE)
    me = client.get("/v1/me")
    me.raise_for_status()
    wanted = SLUG2 if role == "other_admin" else SLUG
    for membership in me.json()["memberships"]:
        if membership["workspace"]["slug"] == wanted:
            client.headers["x-abb-workspace"] = membership["workspace"]["id"]
            _clients[role] = client
            return client
    die(f"{role} is not a member of {wanted}")
    raise AssertionError


def key_client(name: str) -> httpx.Client:
    return httpx.Client(base_url=API, headers={"Authorization": f"Bearer {state()['keys'][name]}"}, timeout=60, limits=NO_KEEPALIVE)


def projects() -> dict[str, str]:
    return dict(state()["projects"])


def create_key(client: httpx.Client, name: str, scopes: list[str], project: str | None) -> str:
    body: dict[str, Any] = {"name": name, "scopes": scopes}
    if project:
        body["project_id"] = project
    response = client.post("/v1/api-keys", json=body)
    if response.status_code != 201:
        die(f"create key {name} -> {response.status_code}")
    return str(response.json()["token"])


# ------------------------------------------------------------------------------------------------ the receivers

_recv = httpx.Client(base_url=RECV, timeout=30, limits=NO_KEEPALIVE)


def recv_state() -> dict[str, Any]:
    response = _recv.get("/state")
    response.raise_for_status()
    return dict(response.json())


def recv_requests(key: str | None = None, listener: str | None = None) -> list[dict[str, Any]]:
    return [r for r in recv_state()["requests"] if (key is None or r["key"] == key) and (listener is None or r["listener"] == listener)]


def recv_mail() -> list[dict[str, Any]]:
    return list(recv_state()["mail"])


def recv_mode(key: str, mode: str) -> None:
    _recv.post("/mode", json={"key": key, "mode": mode}).raise_for_status()


def recv_secret(key: str, secret: str) -> None:
    _recv.post("/secret", json={"key": key, "secret": secret}).raise_for_status()


def recv_listener(name: str, up: bool) -> None:
    _recv.post("/listener", json={"name": name, "up": up}).raise_for_status()


def net_set(**hosts: list[str]) -> None:
    """Change what the sender's test resolver answers for these names (the file is read on every resolution)."""
    data = json.loads(NET_FILE.read_text())
    data["hosts"].update(hosts)
    temporary = NET_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(data))
    temporary.replace(NET_FILE)


def web_url(host: str, port: int, kind: str, key: str) -> str:
    return f"https://{host}:{port}/{kind}/{key}"


# ------------------------------------------------------------------------------------------------ alert API verbs


def new_destination(name: str, type_: str, url: str | None = None, recipients: list[str] | None = None, *, key: str | None = None,
                    role: str = "owner") -> dict[str, Any]:
    """Create a destination as `role`; a webhook's signing secret is registered with the receiver under `key` (never printed)."""
    target: dict[str, Any] = {"url": url} if url else {"recipients": recipients}
    response = person(role).post("/v1/alerts/destinations", json={"name": name, "type": type_, "target": target})
    if response.status_code != 201:
        die(f"create destination {name} -> {response.status_code} {response.json().get('error', {}).get('code')}")
    body = dict(response.json())
    if body.get("signing_secret") and key:
        recv_secret(key, body["signing_secret"])
        remember("secrets", **{body["id"]: body["signing_secret"]})
    if url:
        remember("urls", **{body["id"]: url})
    return body


_agents: set[tuple[str, str]] = set()


def ensure_agent(project: str, agent: str) -> None:
    """A rule may filter on an agent only once the project has seen it: record a harmless successful run."""
    if (project, agent) in _agents:
        return
    run_id = post_run(project, "SUCCESS", agent)
    wait_until(f"agent {agent} to be known", lambda: run_current(run_id), 60, every=0.2)
    _agents.add((project, agent))


def new_rule(name: str, trigger: str, destinations: list[str], *, project: str | None = None, filters: dict[str, Any] | None = None,
             cooldown: int = 0, enabled: bool = True) -> dict[str, Any]:
    if project and filters and filters.get("agent"):
        ensure_agent(project, filters["agent"])
    body: dict[str, Any] = {"name": name, "trigger": trigger, "destination_ids": destinations, "filters": filters or {},
                            "cooldown_seconds": cooldown, "enabled": enabled}
    if project:
        body["project_id"] = projects()[project]
    response = person("owner").post("/v1/alerts/rules", json=body)
    if response.status_code != 201:
        die(f"create rule {name} -> {response.status_code} {response.text[:200]}")
    return dict(response.json())


def history(role: str = "owner", **params: Any) -> list[dict[str, Any]]:
    """The whole history matching the filters, newest first."""
    run_id = params.pop("run_id", None)  # the endpoint has no run filter (KI to file); filter here
    rows: list[dict[str, Any]] = []
    cursor = None
    while True:
        query = {**{k: v for k, v in params.items() if v is not None}, "limit": 200}
        if cursor:
            query["cursor"] = cursor
        response = person(role).get("/v1/alerts/notifications", params=query)
        if response.status_code != 200:
            die(f"history as {role} -> {response.status_code}")
        page = response.json()
        rows += [r for r in page["items"] if run_id is None or r.get("run_id") == run_id]
        cursor = page["next_cursor"]
        if not cursor:
            return rows


# ------------------------------------------------------------------------------------------------ events


def _now() -> datetime:
    return datetime.now(UTC) - timedelta(seconds=10)


def stamp(n: int) -> str:
    return (_now() + timedelta(milliseconds=n * 20)).isoformat().replace("+00:00", "Z")


def ids() -> dict[str, str]:
    return {"run_id": str(new_id(IdKind.RUN)), "trace_id": str(new_id(IdKind.TRACE))}


def event(run: dict[str, str], n: int, event_type: str, agent: str = "coding-agent", **fields: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": "1.0", "event_id": str(new_id(IdKind.EVENT)), "run_id": run["run_id"], "trace_id": run["trace_id"],
        "span_id": str(new_id(IdKind.SPAN)), "agent_id": agent, "event_type": event_type, "occurred_at": stamp(n), "sequence": n,
    }
    body.update(fields)
    return {k: v for k, v in body.items() if v is not ...}


def sentinel_events(run: dict[str, str], agent: str, sentinel: str, start: int = 2) -> list[dict[str, Any]]:
    """Ordinary activity whose content carries the sentinel in a file path, a shell command, a host, a tool argument and (the
    caller's final event) error text. Alerts must carry none of it."""
    return [
        event(run, start, "file.read", agent, attributes={"file.path": f"/srv/work/{sentinel}/report.txt"}),
        event(run, start + 1, "shell.command.completed", agent, status="success", duration_ms=5,
              attributes={"shell.command": f"echo {sentinel}", "shell.exit_code": 0}),
        event(run, start + 2, "http.request", agent,
              attributes={"http.method": "GET", "http.url": f"https://{sentinel.lower()}.hosts.example.net/probe"}),
        event(run, start + 3, "tool.call.completed", agent, status="success", duration_ms=7,
              attributes={"tool.name": "github"}, payload={"arguments": {"query": sentinel}}),
    ]


def run_events(kind: str, agent: str = "coding-agent", *, cost: float | None = None, sentinel: str | None = None,
               filler: int = 0) -> tuple[str, list[dict[str, Any]]]:
    """A run that ends as `kind`: FAILED | TIMED_OUT | BLOCKED | SUCCESS."""
    run = ids()
    events = [event(run, 1, "run.started", agent, span_id=..., attributes={})]
    n = 2
    if sentinel:
        events += sentinel_events(run, agent, sentinel, n)
        n += 4
    for _ in range(filler):
        events.append(event(run, n, "tool.call.completed", agent, status="success", duration_ms=5, attributes={"tool.name": "shell"}))
        n += 1
    if cost is not None:
        events.append(event(run, n, "llm.request.completed", agent, status="success", duration_ms=5,
                            attributes={"llm.provider": "example-provider", "llm.model": "alerts-e2e-model", "llm.input_tokens": 9,
                                        "cost.estimated_usd": cost}))
        n += 1
    extra = {"attributes": {"error.message": f"boom {sentinel or 'x'}"}} if sentinel else {}
    if kind == "FAILED":
        events.append(event(run, n, "run.failed", agent, span_id=..., status="error", **extra))
    else:
        status = {"TIMED_OUT": "timeout", "BLOCKED": "blocked", "SUCCESS": "success"}[kind]
        events.append(event(run, n, "run.completed", agent, span_id=..., status=status, **extra))
    return run["run_id"], events


def post_events(key: str, events: list[dict[str, Any]]) -> httpx.Response:
    return key_client(key).post("/v1/events/batch", json={"events": events})


def post_run(key: str, kind: str, agent: str = "coding-agent", **kwargs: Any) -> str:
    run_id, events = run_events(kind, agent, **kwargs)
    response = post_events(key, events)
    if response.status_code != 202:
        die(f"ingest {kind} -> {response.status_code} {response.text[:200]}")
    return run_id


def run_current(run_id: str) -> bool:
    response = person("owner").get(f"/v1/runs/{run_id}")
    return response.status_code == 200 and response.json().get("summary_state") == "current"


# ------------------------------------------------------------------------------------------------ waiting


def wait_until(what: str, probe: Any, timeout: float = 90.0, every: float = 0.5, diag: Any = None) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = probe()
        if found:
            return found
        time.sleep(every)
    if diag is not None:
        print(f"  state at the timeout: {json.dumps(diag(), default=str)}", file=sys.stderr)
    worker = WORK / "worker.log"
    if worker.exists():
        print("  worker log tail:\n    " + "\n    ".join(worker.read_text(errors="replace").splitlines()[-8:]), file=sys.stderr)
    die(f"timed out waiting for {what}")


def wait_alerts(what: str, expected: int, timeout: float = 90.0, *, statuses: tuple[str, ...] = ("delivered",), **params: Any) -> list[dict[str, Any]]:
    """Wait until the history (as the owner) holds `expected` rows in these statuses, then return them."""
    def probe() -> list[dict[str, Any]] | None:
        rows = [r for r in history(**params) if r["status"] in statuses]
        return rows if len(rows) >= expected else None
    return wait_until(what, probe, timeout) or []


def settle(seconds: float = 3.0) -> None:
    """Quiet time to prove nothing more arrives (bounded; used only for 'no further alert' assertions)."""
    time.sleep(seconds)


# ------------------------------------------------------------------------------------------------ the database


def db_url() -> str:
    return os.environ["ABB_DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


def db(sql: str, *args: Any) -> list[asyncpg.Record]:
    async def go() -> list[asyncpg.Record]:
        conn = await asyncpg.connect(db_url())
        try:
            return list(await conn.fetch(sql, *args))
        finally:
            await conn.close()
    return asyncio.run(go())


def uuid_of(public_id: str) -> uuid.UUID:
    from abb_event_schema.ids import to_uuid  # noqa: PLC0415
    return to_uuid(public_id)  # type: ignore[no-any-return]


async def _scan(needles: list[str], tables: list[str] | None) -> tuple[dict[str, list[str]], int, int]:
    conn = await asyncpg.connect(db_url())
    try:
        columns = await conn.fetch(
            "select table_name, column_name, data_type from information_schema.columns where table_schema = 'public' and data_type in "
            "('text', 'character varying', 'jsonb', 'json', 'tsvector', 'ARRAY', 'bytea') order by table_name, ordinal_position"
        )
        patterns = [f"%{n}%" for n in needles]
        hits: dict[str, list[str]] = {n: [] for n in needles}
        seen: set[str] = set()
        rows = 0
        count = 0
        for column in columns:
            table, name, kind = column["table_name"], column["column_name"], column["data_type"]
            if tables is not None and table not in tables:
                continue
            count += 1
            expr = f'encode("{name}", \'escape\')' if kind == "bytea" else f'"{name}"::text'
            found = await conn.fetch(f'select {expr} as v from "{table}" where {expr} ilike any($1::text[])', patterns)  # noqa: S608
            if table not in seen:
                seen.add(table)
                rows += await conn.fetchval(f'select count(*) from "{table}"')  # noqa: S608
            for row in found:
                for needle in needles:
                    if needle.lower() in row["v"].lower():
                        hits[needle].append(f"{table}.{name}")
        return hits, count, rows
    finally:
        await conn.close()


def scan_database(needles: list[str], tables: list[str] | None = None) -> tuple[dict[str, list[str]], int, int]:
    """Where each needle occurs (case-insensitive) in every text-like column of these tables (default: all). -> (hits, columns, rows)"""
    return asyncio.run(_scan(needles, tables))


def log_text() -> str:
    parts = []
    for log in filter(None, os.environ.get("E2E_LOGS", "").split(":")):
        path = Path(log)
        if path.exists():
            parts.append(path.read_text(errors="replace"))
    return "\n".join(parts)


def audit_rows(limit_pages: int = 20) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor = None
    for _ in range(limit_pages):
        params: dict[str, Any] = {"limit": 200}
        if cursor:
            params["cursor"] = cursor
        response = person("owner").get("/v1/audit", params=params)
        if response.status_code != 200:
            die(f"audit -> {response.status_code}")
        page = response.json()
        rows += page["items"]
        cursor = page.get("next_cursor")
        if not cursor:
            break
    return rows

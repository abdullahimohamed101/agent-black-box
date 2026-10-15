"""Shared helpers of scripts/hardening-e2e.sh (Phase 19a step 7): environment, results, API process management (a gate can start
its own API processes with its own settings, all over ONE database), clients, a small asyncio load generator, the database and
the log scan. Run in the API's environment (`uv run`). Secrets are never printed: failures name the check, not a value.
"""

import asyncio
import json
import os
import random
import secrets
import signal
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import httpx
from abb_event_schema.ids import IdKind, new_id

ROOT = Path(os.environ.get("E2E_ROOT", "."))
WORK = Path(os.environ.get("E2E_WORK", "/tmp"))  # noqa: S108
STATE_FILE = Path(os.environ.get("E2E_STATE", "/dev/null"))
RESULTS = Path(os.environ.get("E2E_RESULTS", "/dev/null"))
ORIGIN = os.environ.get("E2E_ORIGIN", "http://localhost:3220")
SLUG = os.environ.get("E2E_WORKSPACE", "")
SLUG2 = os.environ.get("E2E_WORKSPACE2", "")
OIDC_PORT = int(os.environ.get("E2E_OIDC_PORT", "8232"))
API_PYTHON = ROOT / "apps" / "api" / ".venv" / "bin" / "python"
API_DIR = ROOT / "apps" / "api"
PEOPLE = ("owner", "admin", "developer", "viewer", "other_admin", "inviter")

_failures: list[str] = []
_gate = "setup"

# A new connection per request: uvicorn closes an idle keep-alive connection after 5 s and httpx does not retry a reset on a reused one.
NO_KEEPALIVE = httpx.Limits(max_keepalive_connections=0)


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
        print(f"HARDENING-E2E CHECK FAILED [{_gate}]: {len(_failures)} failed: {_failures}", file=sys.stderr)
        raise SystemExit(1)


def die(message: str) -> None:
    print(f"HARDENING-E2E CHECK FAILED [{_gate}]: {message}", file=sys.stderr)
    raise SystemExit(1)


# ------------------------------------------------------------------------------------------------ state


def state() -> dict[str, Any]:
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.stat().st_size else {}


def save(**values: Any) -> None:
    current = state()
    current.update(values)
    STATE_FILE.write_text(json.dumps(current))


# ------------------------------------------------------------------------------------------------ the database


def owner_url() -> str:
    return os.environ["E2E_OWNER_DB_URL"].replace("postgresql+asyncpg://", "postgresql://")


def db(sql: str, *args: Any) -> list[asyncpg.Record]:
    async def go() -> list[asyncpg.Record]:
        conn = await asyncpg.connect(owner_url())
        try:
            return list(await conn.fetch(sql, *args))
        finally:
            await conn.close()

    return asyncio.run(go())


def db_one(sql: str, *args: Any) -> Any:
    rows = db(sql, *args)
    return rows[0][0] if rows else None


def transactions() -> int:
    """Commits plus rollbacks of this database so far (the load figure of the flood gates)."""
    return int(db_one("select xact_commit + xact_rollback from pg_stat_database where datname = current_database()"))


def quiet(max_rate: float = 25.0, timeout: float = 90.0) -> float:
    """Wait until the database is quiet (the worker has derived what the last gate ingested), so a transaction count taken next is the
    gate's own. Returns the idle rate (transactions per second). Bounded; fails the gate if the system never settles."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        before = transactions()
        time.sleep(1.5)
        rate = (transactions() - before - 1) / 1.5
        if rate < max_rate:
            return round(rate, 1)
    die(f"the database never went quiet (last rate {rate:.0f} transactions/s)")
    return 0.0


def uuid_of(public_id: str) -> Any:
    from abb_event_schema.ids import to_uuid  # noqa: PLC0415

    return to_uuid(public_id)


def room_in_window(window: int, minimum: float) -> None:
    """Wait until the database's current fixed window has at least `minimum` seconds left: a count taken inside one window is
    not split by a boundary (the same technique as tests/limits/helpers.py). Bounded by the window length."""
    left = float(db_one("select $1::numeric - mod(extract(epoch from now())::numeric, $1::numeric)", window))
    if left < minimum:
        print(f"  (waiting {left + 0.3:.1f} s for a fresh {window} s window)", flush=True)
        time.sleep(left + 0.3)


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))] if ordered else float("nan")


# ------------------------------------------------------------------------------------------------ processes


def _base_env() -> dict[str, str]:
    env = dict(os.environ)
    env["ABB_DATABASE_URL"] = os.environ["E2E_RUNTIME_DB_URL"]  # the serving processes run as the least-privilege role
    return env


@dataclass
class Proc:
    name: str
    popen: subprocess.Popen[bytes]
    log: Path
    port: int = 0
    env: dict[str, str] = field(default_factory=dict)

    @property
    def pid(self) -> int:
        return self.popen.pid

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def alive(self) -> bool:
        return self.popen.poll() is None

    def stop(self) -> None:
        if self.popen.poll() is None:
            self.popen.send_signal(signal.SIGTERM)
            try:
                self.popen.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.popen.kill()
                self.popen.wait(timeout=10)

    def rss_mib(self) -> float:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(self.pid)], capture_output=True, text=True, check=False)  # noqa: S603, S607
        return int(out.stdout.strip() or 0) / 1024


_started: list[Proc] = []


def start_api(name: str, port: int, env: dict[str, str | None] | None = None, *, owner_role: bool = False, wait: bool = True,
              log_prefix: str = "api") -> Proc:
    """One API process (a venv python, so the pid is the server's own). `env` overrides settings for this process only."""
    merged = _base_env()
    if owner_role:
        merged["ABB_DATABASE_URL"] = os.environ["E2E_OWNER_DB_URL"]
    for key, value in (env or {}).items():
        if value is None:
            merged.pop(key, None)  # None unsets: the setting is absent, not empty
        else:
            merged[key] = value
    log = WORK / f"{log_prefix}-{name}.log"
    handle = log.open("wb")
    popen = subprocess.Popen(  # noqa: S603
        [str(API_PYTHON), "-m", "uvicorn", "abb_api.main:app_from_env", "--factory", "--port", str(port), "--host", "127.0.0.1"],
        cwd=API_DIR, env=merged, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True,
    )
    proc = Proc(name, popen, log, port, {k: v for k, v in merged.items() if k.startswith("ABB_") and "DATABASE" not in k})
    _started.append(proc)
    if wait and not wait_ready(proc):
        tail = log.read_text(errors="replace").splitlines()[-6:]
        die(f"API {name} did not become ready (exit={popen.poll()}): {tail}")
    return proc


def wait_ready(proc: Proc, seconds: float = 40.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not proc.alive():
            return False
        try:
            if httpx.get(f"{proc.url}/readyz", timeout=2).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    return False


def start_pair(name: str, ports: tuple[int, int], env: dict[str, str] | None = None) -> tuple[Proc, Proc]:
    """Two API processes over the same database: what 'across processes' means in this suite."""
    first = start_api(f"{name}-a", ports[0], env, wait=False)
    second = start_api(f"{name}-b", ports[1], env, wait=False)
    for proc in (first, second):
        if not wait_ready(proc):
            die(f"API {proc.name} did not become ready: {proc.log.read_text(errors='replace').splitlines()[-6:]}")
    return first, second


def stop_all(*procs: Proc) -> None:
    for proc in procs:
        proc.stop()


# ------------------------------------------------------------------------------------------------ the CLI and clients


def cli(*args: str, env: dict[str, str] | None = None) -> str:
    merged = dict(os.environ)
    merged["ABB_DATABASE_URL"] = os.environ["E2E_OWNER_DB_URL"]
    merged.update(env or {})
    done = subprocess.run([str(API_PYTHON), "-m", "abb_api.cli", *args], cwd=API_DIR, env=merged, capture_output=True, text=True, check=False)  # noqa: S603
    if done.returncode != 0:  # stderr only: stdout can hold a session token
        die(f"cli {args[0]} failed: {done.stderr.strip()[-300:]}")
    return done.stdout.strip()


def email(role: str) -> str:
    return str(state()["people"][role])


def session_token(address: str) -> str:
    token = cli("create-session", "--email", address, "--hours", "3", env={"ABB_ALLOW_DEV_SESSIONS": "1"})
    if not token:
        die("could not mint a session")
    return token


_people: dict[tuple[str, str], httpx.Client] = {}


def person(role: str, base: str | None = None) -> httpx.Client:
    """A client signed in (development session) as `role`, acting in its workspace, talking to `base` (default: the main API)."""
    base = base or main_url()
    if (role, base) in _people:
        return _people[(role, base)]
    token = state()["sessions"][role]
    client = httpx.Client(base_url=base, cookies={"abb_session": token}, headers={"Origin": ORIGIN}, timeout=60, limits=NO_KEEPALIVE)
    me = client.get("/v1/me")
    me.raise_for_status()
    wanted = SLUG2 if role == "other_admin" else SLUG
    for membership in me.json()["memberships"]:
        if membership["workspace"]["slug"] == wanted:
            client.headers["x-abb-workspace"] = membership["workspace"]["id"]
            _people[(role, base)] = client
            return client
    die(f"{role} is not a member of {wanted}")
    raise AssertionError


def session_headers(role: str, base: str | None = None) -> dict[str, str]:
    """Headers that make an async client act as `role` (cookie, origin, workspace) without a cookie jar."""
    client = person(role, base)
    return {"Cookie": f"abb_session={state()['sessions'][role]}", "Origin": ORIGIN, "x-abb-workspace": client.headers["x-abb-workspace"]}


def refresh_session(role: str) -> None:
    """A gate that ends sessions on purpose (sign out everywhere) re-mints the role's session for the gates after it."""
    current = state()["sessions"]
    current[role] = session_token(email(role))
    save(sessions=current)
    for key in [k for k in _people if k[0] == role]:
        del _people[key]


def main_url() -> str:
    return str(state()["main_urls"][0])


def main_urls() -> list[str]:
    return list(state()["main_urls"])


def new_project(name: str, role: str = "owner", base: str | None = None) -> str:
    response = person(role, base).post("/v1/projects", json={"name": name, "slug": name, "capture_mode": "full"})
    if response.status_code != 201:
        die(f"create project {name} -> {response.status_code} {response.text[:160]}")
    project = str(response.json()["id"])
    # A new project captures metadata only (ADR-130); artifacts and payloads need FULL to be stored as sent.
    mode = person(role, base).put(f"/v1/projects/{project}/security-settings", json={"capture_mode": "FULL"})
    if mode.status_code != 200:
        die(f"set capture mode of {name} -> {mode.status_code}")
    return project


def new_key(project: str, scopes: list[str], name: str = "hardening", role: str = "owner", base: str | None = None) -> str:
    response = person(role, base).post("/v1/api-keys", json={"name": name, "scopes": scopes, "project_id": project})
    if response.status_code != 201:
        die(f"create key {name} -> {response.status_code} {response.text[:160]}")
    return str(response.json()["token"])


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def key_id_of(token: str) -> str:
    """The public key id of `abb_live_<id>.<secret>` (the id appears in the UI and the audit log; the secret never leaves)."""
    return token.split(".", 1)[0]


def bad_token_for(token: str) -> str:
    """The same key id with a wrong secret: a well-formed credential that fails authentication."""
    return f"{key_id_of(token)}.{secrets.token_urlsafe(32)}"  # 43 url-safe characters, the shape of a real secret


def random_token() -> str:
    """A well-formed key that matches no key id."""
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return f"abb_live_{''.join(secrets.choice(alphabet) for _ in range(12))}.{secrets.token_urlsafe(32)}"


# ------------------------------------------------------------------------------------------------ events


def stamp(n: int) -> str:
    return (datetime.now(UTC) - timedelta(seconds=30) + timedelta(milliseconds=n * 5)).isoformat().replace("+00:00", "Z")


def run_ids() -> dict[str, str]:
    return {"run_id": str(new_id(IdKind.RUN)), "trace_id": str(new_id(IdKind.TRACE))}


def event(run: dict[str, str], n: int, agent: str = "hardening-agent", pad: int = 0) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": "1.0", "event_id": str(new_id(IdKind.EVENT)), "run_id": run["run_id"], "trace_id": run["trace_id"],
        "span_id": str(new_id(IdKind.SPAN)), "agent_id": agent, "event_type": "tool.call.completed", "occurred_at": stamp(n),
        "sequence": n, "status": "success", "duration_ms": 5, "attributes": {"tool.name": "shell"},
    }
    if pad:
        body["payload"] = {"response": "x" * pad}
    return body


def batch_of(events: list[dict[str, Any]]) -> dict[str, Any]:
    return {"events": events}


# ------------------------------------------------------------------------------------------------ a small load generator


@dataclass
class Reply:
    status: int
    code: str | None
    retry_after: str | None
    elapsed_ms: float
    body: Any = None


def error_code(response: httpx.Response) -> str | None:
    try:
        return str(response.json()["error"]["code"])
    except (ValueError, KeyError, TypeError):
        return None


async def fire(client: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> Reply:
    started = time.perf_counter()
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.HTTPError as error:
        return Reply(0, type(error).__name__, None, (time.perf_counter() - started) * 1000)
    ms = (time.perf_counter() - started) * 1000
    body = None
    if response.headers.get("content-type", "").startswith("application/json"):
        try:
            body = response.json()
        except ValueError:
            body = None
    return Reply(response.status_code, error_code(response) if isinstance(body, dict) else None, response.headers.get("retry-after"), ms, body)


async def run_concurrent(total: int, concurrency: int, make: Callable[[int], Awaitable[Reply]]) -> list[Reply]:
    """`total` calls of make(i), at most `concurrency` at a time."""
    queue: asyncio.Queue[int] = asyncio.Queue()
    for i in range(total):
        queue.put_nowait(i)
    out: list[Reply | None] = [None] * total

    async def worker() -> None:
        while True:
            try:
                i = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            out[i] = await make(i)

    await asyncio.gather(*[worker() for _ in range(concurrency)])
    return [r for r in out if r is not None]


async def run_for(seconds: float, concurrency: int, make: Callable[[int], Awaitable[Reply]]) -> list[Reply]:
    """As many calls of make(i) as fit in `seconds`."""
    deadline = time.monotonic() + seconds
    out: list[Reply] = []
    counter = {"n": 0}

    async def worker() -> None:
        while time.monotonic() < deadline:
            counter["n"] += 1
            out.append(await make(counter["n"]))

    await asyncio.gather(*[worker() for _ in range(concurrency)])
    return out


def tally(replies: list[Reply]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for reply in replies:
        label = str(reply.status) if reply.code is None or reply.status < 400 else f"{reply.status} {reply.code}"
        counts[label] = counts.get(label, 0) + 1
    return counts


def count(replies: list[Reply], status: int, code: str | None = None) -> int:
    return sum(1 for r in replies if r.status == status and (code is None or r.code == code))


def scrub(body: Any) -> Any:
    """An error body without its per-request id, for 'identical bodies' comparisons."""
    if isinstance(body, dict):
        return {k: scrub(v) for k, v in body.items() if k not in {"request_id", "retry_after_seconds"}}
    if isinstance(body, list):
        return [scrub(v) for v in body]
    return body


def jitter_ip() -> str:
    return f"198.{random.randint(18, 19)}.{random.randint(0, 255)}.{random.randint(1, 254)}"  # noqa: S311


def audit_rows(role: str = "owner", base: str | None = None, pages: int = 20) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor = None
    for _ in range(pages):
        params: dict[str, Any] = {"limit": 200}
        if cursor:
            params["cursor"] = cursor
        response = person(role, base).get("/v1/audit", params=params)
        if response.status_code != 200:
            die(f"audit -> {response.status_code}")
        page = response.json()
        rows += page["items"]
        cursor = page.get("next_cursor")
        if not cursor:
            break
    return rows


# ------------------------------------------------------------------------------------------------ logs


def all_logs() -> list[Path]:
    return sorted(p for p in WORK.glob("*.log") if p.name != "oidc.log")


def log_lines(path: Path, *needles: str) -> list[str]:
    return [line.strip()[:240] for line in path.read_text(errors="replace").splitlines() if any(n in line for n in needles)]


def machine() -> dict[str, Any]:
    def run(*cmd: str) -> str:
        try:
            return subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=5).stdout.strip()  # noqa: S603
        except (OSError, subprocess.SubprocessError):
            return "?"

    return {
        "os": run("uname", "-sr"), "model": run("sysctl", "-n", "hw.model") if sys.platform == "darwin" else run("uname", "-m"),
        "cpus": os.cpu_count(), "loadavg": [round(x, 2) for x in os.getloadavg()],
    }

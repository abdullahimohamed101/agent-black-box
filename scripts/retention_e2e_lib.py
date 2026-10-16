"""Shared helpers of scripts/retention-e2e.sh (Phase 19b step 6): results, the database as the owner and as the other roles, seeding
through the REAL ingestion API, ageing runs (the owner's SQL is the test harness: events are immutable for the API), the retention
process, digests of rows and files, and the log scan. Run in the API's environment (`uv run`). Secrets are never printed.

Everything that is asserted is a DETERMINISTIC fact: counts, states, orders observed in the database, exit codes. Timings are
recorded as information only (docs/benchmarks/phase-19b-retention-e2e.md); no assertion depends on the clock or on how fast the
machine is. Ordering is observed (the retention process waits on a row lock the harness holds, and the harness looks at
pg_stat_activity), never slept for.
"""

import asyncio
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import httpx
from abb_event_schema.ids import IdKind, new_id

import hardening_e2e_lib as H  # noqa: N812

ROOT = H.ROOT
WORK = H.WORK
API_DIR = H.API_DIR
API_PYTHON = H.API_PYTHON
RESULTS = H.RESULTS
ARTIFACT_DIR = Path(os.environ.get("E2E_ARTIFACT_DIR", str(WORK / "artifacts")))
LOCK_WAIT = "Lock"
NAME = os.environ.get("E2E_NAME", "RETENTION-E2E")

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
        print(f"{NAME} CHECK FAILED [{_gate}]: {len(_failures)} failed: {_failures}", file=sys.stderr)
        raise SystemExit(1)


def die(message: str) -> None:
    print(f"{NAME} CHECK FAILED [{_gate}]: {message}", file=sys.stderr)
    raise SystemExit(1)


H.die = die  # the shared helpers fail under this script's name


# ------------------------------------------------------------------------------------------------ state, database


state = H.state
save = H.save
db = H.db
db_one = H.db_one
uuid_of = H.uuid_of
percentile = H.percentile
machine = H.machine
bearer = H.bearer


def public_id(kind: IdKind, value: uuid.UUID) -> str:
    from abb_event_schema.ids import from_uuid  # noqa: PLC0415

    return str(from_uuid(kind, value))


def retention_url() -> str:
    """The retention login's URL: a 0600 file, never the environment (the API and the worker must not be able to see it)."""
    return Path(os.environ["E2E_RETENTION_URL_FILE"]).read_text().strip()


def plain(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def _as(url: str, sql: str, *args: Any, fetch: bool = True) -> Any:
    conn = await asyncpg.connect(plain(url))
    try:
        return list(await conn.fetch(sql, *args)) if fetch else await conn.execute(sql, *args)
    finally:
        await conn.close()


def sqlstate_as(url: str, sql: str, *args: Any) -> str | None:
    """The SQLSTATE the statement fails with when run over a real connection of that role (None: it succeeded). The statement is
    always rolled back: a privilege probe must never change data."""

    async def go() -> str | None:
        conn = await asyncpg.connect(plain(url))
        try:
            tx = conn.transaction()
            await tx.start()
            try:
                await conn.execute(sql, *args)
            except asyncpg.PostgresError as error:
                return str(error.sqlstate)
            finally:
                await tx.rollback()
            return None
        finally:
            await conn.close()

    return asyncio.run(go())


def runtime_url() -> str:
    return os.environ["E2E_RUNTIME_DB_URL"]


# ------------------------------------------------------------------------------------------------ people and tenants


def client_in(role: str, slug: str, token: str, base: str) -> httpx.Client:
    client = httpx.Client(base_url=base, cookies={"abb_session": token}, headers={"Origin": H.ORIGIN}, timeout=60, limits=H.NO_KEEPALIVE)
    me = client.get("/v1/me")
    me.raise_for_status()
    for membership in me.json()["memberships"]:
        if membership["workspace"]["slug"] == slug:
            client.headers["x-abb-workspace"] = membership["workspace"]["id"]
            return client
    die(f"{role} is not a member of {slug}")
    raise AssertionError


def workspace_public_id(client: httpx.Client) -> str:
    return str(client.headers["x-abb-workspace"])


def workspace_uuid(client: httpx.Client) -> uuid.UUID:
    return uuid_of(workspace_public_id(client))


def owner() -> httpx.Client:
    return H.person("owner")


def other_owner() -> httpx.Client:
    return H.person("other_admin")


# ------------------------------------------------------------------------------------------------ seeding through the real API


def stamp(n: int) -> str:
    return (datetime.now(UTC) - timedelta(seconds=30) + timedelta(milliseconds=n * 20)).isoformat().replace("+00:00", "Z")


def _event(run: dict[str, str], n: int, event_type: str, agent: str, **fields: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": "1.0", "event_id": str(new_id(IdKind.EVENT)), "run_id": run["run_id"], "trace_id": run["trace_id"],
        "span_id": str(new_id(IdKind.SPAN)), "agent_id": agent, "event_type": event_type, "occurred_at": stamp(n), "sequence": n,
    }
    body.update(fields)
    return {k: v for k, v in body.items() if v is not ...}


def run_events(agent: str) -> tuple[str, list[dict[str, Any]]]:
    """One ordinary successful run: started, a tool call, a priced model call, completed (4 events, spans, a cost line)."""
    run = {"run_id": str(new_id(IdKind.RUN)), "trace_id": str(new_id(IdKind.TRACE))}
    events = [
        _event(run, 1, "run.started", agent, span_id=..., attributes={}),
        _event(run, 2, "tool.call.completed", agent, status="success", duration_ms=5, attributes={"tool.name": "shell"}),
        _event(run, 3, "llm.request.completed", agent, status="success", duration_ms=5,
               attributes={"llm.provider": "example-provider", "llm.model": "retention-e2e-model", "llm.input_tokens": 9, "cost.estimated_usd": 0.01}),
        _event(run, 4, "run.completed", agent, span_id=..., status="success"),
    ]
    return run["run_id"], events


def _text(n: int, tag: str) -> bytes:
    """`n` bytes of ordinary log-like text (the server redacts what looks like a secret, which would change the stored size)."""
    line = f"{tag} build step finished without errors in the sandbox\n"
    return (line * (n // len(line) + 1)).encode()[:n]


@dataclass
class Seeded:
    run: str  # public id
    uuid: uuid.UUID
    project: str
    group: str
    artifact_ids: list[str] = field(default_factory=list)


def ingest_runs(base: str, token: str, project: str, group: str, count: int, agent: str = "retention-agent",
                artifact_sizes: tuple[int, ...] = (1500, 1800)) -> list[Seeded]:
    """`count` runs with events and artifacts through POST /v1/events/batch and PUT /v1/artifacts (the real ingestion path)."""
    out: list[Seeded] = []
    for start in range(0, count, 8):
        batch: list[dict[str, Any]] = []
        chunk: list[Seeded] = []
        for _ in range(min(8, count - start)):
            run_id, events = run_events(agent)
            batch += events
            chunk.append(Seeded(run_id, uuid_of(run_id), project, group))
        response = httpx.post(f"{base}/v1/events/batch", json={"events": batch}, headers=bearer(token), timeout=60)
        if response.status_code != 202 or response.json().get("rejected"):
            die(f"ingest {group} -> {response.status_code} {response.text[:200]}")
        for seeded in chunk:
            for size in artifact_sizes:
                artifact_id = str(new_id(IdKind.ARTIFACT))
                put = httpx.put(f"{base}/v1/artifacts/{artifact_id}", content=_text(size, f"{group}-{artifact_id[-6:]}"),
                                params={"run_id": seeded.run, "kind": "stdout"}, headers={**bearer(token), "content-type": "text/plain"}, timeout=60)
                if put.status_code != 201:
                    die(f"artifact upload {group} -> {put.status_code} {put.text[:160]}")
                seeded.artifact_ids.append(artifact_id)
        out += chunk
    return out


def seeded(group: str) -> list[Seeded]:
    return [Seeded(r["run"], uuid.UUID(r["uuid"]), r["project"], group, r["artifacts"]) for r in state()["groups"][group]]


def remember(group: str, runs: list[Seeded]) -> None:
    groups = state().get("groups", {})
    groups[group] = [{"run": r.run, "uuid": str(r.uuid), "project": r.project, "artifacts": r.artifact_ids} for r in runs]
    save(groups=groups)


def wait_until(what: str, probe: Callable[[], Any], timeout: float = 120.0, every: float = 0.4) -> Any:
    """Poll an OBSERVED state (never a sleep standing in for one); fail the run if it does not appear."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = probe()
        if value:
            return value
        time.sleep(every)
    die(f"timed out waiting for {what}")


def wait_idle(runs: list[Seeded] | None = None, timeout: float = 180.0) -> None:
    """The worker has summarised every given run and has no job left that could touch the data (so a later 'before' snapshot is stable)."""

    def probe() -> bool:
        if runs:  # every run is stored (the ingest answered 202 only after its transaction committed)
            stored = db_one("select count(*) from runs where id = any($1::uuid[])", [r.uuid for r in runs])
            if int(stored) != len(runs):
                return False
        pending = db_one("select count(*) from outbox_jobs where status in ('pending', 'running')")
        dirty = db_one("select count(*) from analytics_dirty where refreshed_version < version")
        return int(pending) == 0 and int(dirty) == 0

    wait_until("the worker to finish what was ingested", probe, timeout)
    # two consecutive quiet readings: a job enqueued by the last one's completion would show up here
    time.sleep(1.5)
    wait_until("the worker to stay idle", probe, 30)


_AGED_COLUMNS = {
    "events": ("occurred_at", "received_at"),
    "spans": ("run_started_at", "started_at", "ended_at"),
    "cost_calculations": ("run_started_at", "occurred_at"),
    "findings": ("run_started_at",),
    "run_search": ("started_at",),
    "artifacts": ("created_at",),
}


def age_runs(runs: list[Seeded], days: float, step_seconds: int = 0) -> None:
    """Backdate the runs' own timestamps by `days` (the owner's SQL; the API cannot change them). With `step_seconds` each run
    is older than the previous by that much, so the retention job's oldest-first order is known."""
    for index, seeded in enumerate(runs):
        seconds = days * 86400 + index * step_seconds
        db("update runs set created_at = created_at - make_interval(secs => $1), started_at = started_at - make_interval(secs => $1), "
           "completed_at = completed_at - make_interval(secs => $1), updated_at = updated_at - make_interval(secs => $1) where id = $2",
           float(seconds), seeded.uuid)
        # The rollups are keyed by the run's start copied into its derived rows, and the events carry their own times: age those too,
        # so the data looks like data that was written that long ago.
        for table, columns in _AGED_COLUMNS.items():
            present = {r[0] for r in db("select column_name from information_schema.columns where table_schema = 'public' and table_name = $1", table)}
            sets = ", ".join(f"{c} = {c} - make_interval(secs => $1)" for c in columns if c in present)
            if sets:
                db(f"update {table} set {sets} where run_id = $2", float(seconds), seeded.uuid)  # noqa: S608


def rebuild_rollups(workspace: uuid.UUID, days: set[date]) -> None:
    """Rewrite the analytics rollups of those days from the (now aged) runs, as the worker would have: the owner is the harness."""
    code = (
        "import asyncio, os, sys, uuid, json\n"
        "from datetime import date\n"
        "from sqlalchemy.ext.asyncio import create_async_engine\n"
        "from abb_api.analytics.rollup import refresh_day\n"
        "from abb_api.tenancy import TenantContext\n"
        "async def main():\n"
        "    engine = create_async_engine(os.environ['ABB_DATABASE_URL'])\n"
        "    ws = uuid.UUID(sys.argv[1])\n"
        "    for d in json.loads(sys.argv[2]):\n"
        "        async with engine.begin() as conn:\n"
        "            await refresh_day(conn, TenantContext(ws), date.fromisoformat(d))\n"
        "    await engine.dispose()\n"
        "asyncio.run(main())\n"
    )
    env = dict(os.environ)
    env["ABB_DATABASE_URL"] = os.environ["E2E_OWNER_DB_URL"]
    done = subprocess.run([str(API_PYTHON), "-c", code, str(workspace), json.dumps(sorted(d.isoformat() for d in days))],  # noqa: S603
                          cwd=API_DIR, env=env, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        die(f"could not rebuild rollups: {done.stderr.strip()[-300:]}")


def run_days(runs: list[Seeded]) -> set[date]:
    rows = db("select distinct (started_at at time zone 'UTC')::date from runs where id = any($1::uuid[])", [r.uuid for r in runs])
    return {r[0] for r in rows}


def prepare_group(workspace: uuid.UUID, runs: list[Seeded], days: float, step_seconds: int = 0) -> None:
    """Settle the worker, age the runs, and rebuild the rollups of the days involved (today's included)."""
    wait_idle(runs)
    before = run_days(runs)
    age_runs(runs, days, step_seconds)
    rebuild_rollups(workspace, before | run_days(runs))


# ------------------------------------------------------------------------------------------------ digests of rows and files

VOLATILE = ("updated_at", "computed_at", "refreshed_at", "last_used_at", "last_seen_at", "refreshed_version", "version")


def tables() -> list[str]:
    return [r[0] for r in db("select table_name from information_schema.tables where table_schema = 'public' and table_type = 'BASE TABLE' order by 1")]


def counts() -> dict[str, int]:
    out: dict[str, int] = {}
    for name in tables():
        out[name] = int(db_one(f'select count(*) from "{name}"'))  # noqa: S608
    return out


def table_digest(name: str, where: str = "true", *args: Any) -> str:
    """md5 over every row (as JSON, volatile columns removed, order-independent) of the rows matching `where`."""
    drop = " ".join(f"- '{c}'" for c in VOLATILE)
    value = db_one(f'select coalesce(md5(string_agg((to_jsonb(t) {drop})::text, \'|\' order by (to_jsonb(t) {drop})::text)), \'-\') from "{name}" t where {where}', *args)  # noqa: S608
    return str(value)


def files_digest(root: Path | None = None) -> dict[str, str]:
    """sha256 of every file under the artifact directory, by relative path."""
    root = root or ARTIFACT_DIR
    out: dict[str, str] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def run_keys(run_uuids: list[uuid.UUID]) -> list[str]:
    return [r[0] for r in db("select storage_uri from artifacts where run_id = any($1::uuid[])", run_uuids)]


def file_exists(key: str) -> bool:
    return (ARTIFACT_DIR / key).exists()


# ------------------------------------------------------------------------------------------------ the retention process


@dataclass
class RetentionProc:
    popen: subprocess.Popen[bytes]
    log: Path
    cwd: Path

    def wait(self, timeout: float = 300.0) -> int:
        return self.popen.wait(timeout=timeout)

    def alive(self) -> bool:
        return self.popen.poll() is None

    def signal(self, sig: int) -> None:
        self.popen.send_signal(sig)

    def kill(self) -> None:
        self.popen.send_signal(signal.SIGKILL)
        self.popen.wait(timeout=30)

    def summary(self) -> dict[str, str]:
        """The `key=value ...` line a `run-once` prints (the last line that has `mode=`)."""
        for line in reversed(self.log.read_text(errors="replace").splitlines()):
            if line.startswith("mode="):
                return dict(part.split("=", 1) for part in line.split() if "=" in part)
        return {}


def write_mode(cwd: Path, mode: str) -> None:
    (cwd / ".env").write_text(f"ABB_RETENTION_MODE={mode}\n")


def start_retention(*args: str, mode: str = "enforce", env: dict[str, str] | None = None, name: str | None = None,
                    mode_via_file: bool = False) -> RetentionProc:
    """A retention process exactly as an operator runs it: its own credentials (the retention login's URL), the API's artifact
    directory, NO other database URL. `mode_via_file` puts the mode in a `.env` file in the process's working directory (the kill
    switch re-reads that file before every batch); otherwise it is an environment setting."""
    label = f"{name or (args[0] if args else 'run-once')}-{uuid.uuid4().hex[:6]}"
    cwd = Path(tempfile.mkdtemp(prefix="ret-", dir=WORK))
    environment = {k: v for k, v in os.environ.items() if not k.startswith(("ABB_", "E2E_"))}
    environment.update({
        "ABB_RETENTION_DATABASE_URL": retention_url(), "ABB_ARTIFACT_DIR": str(ARTIFACT_DIR), "ABB_ENVIRONMENT": "development",
        "ABB_RETENTION_STATEMENT_TIMEOUT_SECONDS": "300", "ABB_RETENTION_TICK_BUDGET_SECONDS": "600",
    })
    if mode_via_file:
        write_mode(cwd, mode)
    else:
        environment["ABB_RETENTION_MODE"] = mode
    environment.update(env or {})
    log = WORK / f"retention-{label}.log"
    handle = log.open("wb")
    popen = subprocess.Popen(  # noqa: S603
        [str(API_PYTHON), "-m", "abb_api.retention", *(args or ("run-once",))], cwd=cwd, env=environment,
        stdout=handle, stderr=subprocess.STDOUT, start_new_session=True,
    )
    return RetentionProc(popen, log, cwd)


def run_retention(*args: str, **kwargs: Any) -> tuple[int, RetentionProc]:
    proc = start_retention(*args, **kwargs)
    return proc.wait(), proc


# ------------------------------------------------------------------------------------------------ observing the retention process


class RowLock:
    """The harness holds a row lock (the owner's SELECT ... FOR UPDATE) on an event of one run. The retention process's DELETE of that
    run's events then WAITS, which is an observable state (pg_stat_activity shows a Lock wait for the retention login), so a gate can
    kill or signal the process at a known point without sleeping or racing it."""

    def __init__(self, run: uuid.UUID) -> None:
        self.run = run
        self.loop = asyncio.new_event_loop()
        self.conn: asyncpg.Connection | None = None

    def hold(self) -> "RowLock":
        async def go() -> None:
            self.conn = await asyncpg.connect(plain(os.environ["E2E_OWNER_DB_URL"]))
            await self.conn.execute("begin")
            await self.conn.fetch("select 1 from events where run_id = $1 order by event_id limit 1 for update", self.run)

        self.loop.run_until_complete(go())
        return self

    def release(self) -> None:
        async def go() -> None:
            if self.conn is not None:
                await self.conn.execute("rollback")
                await self.conn.close()
                self.conn = None

        self.loop.run_until_complete(go())

    def __enter__(self) -> "RowLock":
        return self.hold()

    def __exit__(self, *exc: object) -> None:
        self.release()


def retention_waiting(login: str) -> bool:
    """True while a session of the retention login is blocked on a lock."""
    return int(db_one("select count(*) from pg_stat_activity where usename = $1 and wait_event_type = $2", login, LOCK_WAIT)) > 0


def retention_login() -> str:
    return str(re.search(r"//([^:]+):", retention_url()).group(1))  # type: ignore[union-attr]


# ------------------------------------------------------------------------------------------------ API views


def run_view(client: httpx.Client, run_id: str) -> httpx.Response:
    return client.get(f"/v1/runs/{run_id}")


def trace_state(client: httpx.Client, run_id: str) -> str | None:
    response = run_view(client, run_id)
    return str(response.json().get("trace_state")) if response.status_code == 200 else None


def project_artifact_bytes(client: httpx.Client, project: str) -> int:
    response = client.get(f"/v1/projects/{project}/quotas")
    response.raise_for_status()
    for row in response.json()["quotas"]:
        if row["quota"] == "artifact_bytes":
            return int(row["used"])
    die("no artifact_bytes quota row")
    return -1


def put_policy(client: httpx.Client, **tiers: Any) -> httpx.Response:
    return client.put(f"/v1/workspaces/{workspace_public_id(client)}/retention", json=tiers)


def get_policy(client: httpx.Client) -> dict[str, Any]:
    response = client.get(f"/v1/workspaces/{workspace_public_id(client)}/retention")
    response.raise_for_status()
    return dict(response.json())


def tier_days(policy: dict[str, Any]) -> dict[str, Any]:
    return {t["tier"]: ("forever" if t["forever"] else t["days"]) for t in policy["tiers"]}


def reports(client: httpx.Client, limit: int = 100) -> list[dict[str, Any]]:
    response = client.get(f"/v1/workspaces/{workspace_public_id(client)}/retention/reports", params={"limit": limit})
    response.raise_for_status()
    return list(response.json()["items"])


def audit_actions(client: httpx.Client) -> list[str]:
    rows = H.audit_rows("owner")
    return [str(r.get("action")) for r in rows]


# ------------------------------------------------------------------------------------------------ logs


def process_environment(pid: int) -> str:
    """The environment of a process of ours, as `ps` shows it (empty when it cannot be inspected)."""
    done = subprocess.run(["ps", "eww", "-p", str(pid)], capture_output=True, text=True, check=False)  # noqa: S603, S607
    return done.stdout


def all_logs() -> list[Path]:
    return H.all_logs()


log_lines = H.log_lines

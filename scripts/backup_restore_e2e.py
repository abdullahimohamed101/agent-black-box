"""THE PERFORMED RESTORE DRILL of scripts/backup-restore-e2e.sh (Phase 19b step 6, gate 6 of D6, ADR-162).

Seeds a real installation through the real API (runs, events, artifacts of known bytes, an alert destination with a sealed signing secret
and a TLS receiver, a retention policy, quota overrides, people and sessions), takes backups with the REAL tooling (the module CLI, a
passphrase-encrypted bundle, and, when Docker is available, the compose `backup` service built from the API image), DESTROYS the source
(drops the database, deletes the artifact directory), restores into a fresh database and directory, starts the API and the worker on
the restored data and checks that everything is the same; then an OLDER bundle (taken at the previous migration revision) is upgraded on
restore, the refusals are exercised through the CLI, and the alert key is rotated.

Every assertion is a deterministic fact (equal counts, equal digests, byte-identical files, exit codes). The durations, sizes and the
machine are RECORDED (BACKUP_RESTORE_E2E_OUT, docs/benchmarks/phase-19b-restore-drill.md) and never asserted.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).parent))

import retention_e2e_lib as lib  # noqa: E402
from retention_e2e_lib import check, die, gate, info  # noqa: E402

H = lib.H
WORK = lib.WORK
ROOT = lib.ROOT
API_DIR = lib.API_DIR
PY = str(lib.API_PYTHON)
API_PORT = int(os.environ["E2E_API_PORT"])
CTRL = f"http://127.0.0.1:{os.environ['E2E_CTRL_PORT']}"
MAIN_PORT = int(os.environ["E2E_MAIN_PORT"])
HOST = "hooks.example.test"
SERVER_URL = os.environ["E2E_SERVER_URL"]  # the owner URL of the database server (database name replaced per phase)
RUNTIME_ROLE, RUNTIME_PASSWORD = os.environ["E2E_RUNTIME_ROLE"], os.environ["E2E_RUNTIME_PASSWORD"]
RUNS = int(os.environ.get("BACKUP_RESTORE_E2E_RUNS", "120"))
EVENTS_PER_RUN = int(os.environ.get("BACKUP_RESTORE_E2E_EVENTS", "12"))
ARTIFACT_BYTES = int(os.environ.get("BACKUP_RESTORE_E2E_ARTIFACT_BYTES", "40000"))
PASSPHRASE = "drill passphrase 19b6 not a secret"
ADMIN = SERVER_URL.rsplit("/", 1)[0] + "/postgres"

results: dict[str, Any] = {"machine": lib.machine(), "load_before": [round(x, 2) for x in os.getloadavg()]}


# ------------------------------------------------------------------------------------------------ plumbing


def url_for(db_name: str, *, role: str | None = None, password: str | None = None) -> str:
    base = SERVER_URL.rsplit("/", 1)[0] + "/" + db_name
    if role is None:
        return base
    from sqlalchemy.engine import make_url  # noqa: PLC0415

    return make_url(base).set(username=role, password=password).render_as_string(hide_password=False)


def admin(sql: str) -> None:
    import asyncio  # noqa: PLC0415

    import asyncpg  # noqa: PLC0415

    async def go() -> None:
        conn = await asyncpg.connect(lib.plain(ADMIN))
        try:
            await conn.execute(sql)
        finally:
            await conn.close()

    asyncio.run(go())


def use_database(db_name: str) -> None:
    """Point the helpers (owner SQL, the CLI, the API processes) at this database."""
    os.environ["E2E_OWNER_DB_URL"] = url_for(db_name)
    os.environ["E2E_RUNTIME_DB_URL"] = url_for(db_name, role=RUNTIME_ROLE, password=RUNTIME_PASSWORD)


def module(*args: str, env: dict[str, str] | None = None, check_code: bool = False, timeout: float = 900) -> tuple[int, str, str, float]:
    """Run `python -m abb_api.<module> ...` as an operator would: a clean environment plus what the command needs."""
    environment = {k: v for k, v in os.environ.items() if not k.startswith(("ABB_", "E2E_"))}
    environment["ABB_ENVIRONMENT"] = "development"
    environment.update(env or {})
    started = time.perf_counter()
    done = subprocess.run([PY, "-m", *args], cwd=API_DIR, env=environment, capture_output=True, text=True, check=False, timeout=timeout)  # noqa: S603
    seconds = time.perf_counter() - started
    if check_code and done.returncode != 0:
        die(f"{' '.join(args[:2])} failed ({done.returncode}): {done.stderr.strip()[-300:]}")
    return done.returncode, done.stdout, done.stderr, seconds


def kv(line: str) -> dict[str, str]:
    return dict(part.split("=", 1) for part in line.split() if "=" in part)


def backup_env(db_name: str, artifacts: Path) -> dict[str, str]:
    return {"ABB_BACKUP_DATABASE_URL": url_for(db_name), "ABB_ARTIFACT_DIR": str(artifacts)}


def restore_env(db_name: str, artifacts: Path) -> dict[str, str]:
    # No ABB_RUNTIME_DB_PASSWORD / ABB_RETENTION_DB_PASSWORD: the restore's `alembic upgrade` would set them on the CLUSTER roles
    # abb_runtime / abb_retention, which this drill shares with the developer's other databases. The drill's login is its own role.
    return {"ABB_RESTORE_DATABASE_URL": url_for(db_name), "ABB_ARTIFACT_DIR": str(artifacts)}


def api_env(artifacts: Path) -> dict[str, str | None]:
    return {"ABB_ARTIFACT_DIR": str(artifacts)}


_stack: list[Any] = []


def start_stack(artifacts: Path, label: str) -> None:
    proc = H.start_api(label, API_PORT, api_env(artifacts), log_prefix="api")  # type: ignore[arg-type]
    environment = H._base_env()
    environment["ABB_ARTIFACT_DIR"] = str(artifacts)
    log = WORK / f"worker-{label}.log"
    handle = log.open("wb")
    worker = subprocess.Popen([PY, "-m", "abb_api.worker"], cwd=API_DIR, env=environment, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)  # noqa: S603
    _stack[:] = [proc, worker]
    # the worker has no port; it is up when it has started polling (the API's readiness is the gate that matters)
    time.sleep(1.0)
    if worker.poll() is not None:
        die(f"the worker exited: {log.read_text(errors='replace')[-300:]}")


def stop_stack() -> None:
    for item in _stack:
        if hasattr(item, "stop"):
            item.stop()
        else:
            item.terminate()
            try:
                item.wait(timeout=20)
            except subprocess.TimeoutExpired:
                item.kill()
    _stack.clear()
    H._people.clear()


def dir_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def recv(path: str, **body: Any) -> Any:
    if body:
        httpx.post(f"{CTRL}{path}", json=body, timeout=30).raise_for_status()
        return None
    response = httpx.get(f"{CTRL}{path}", timeout=30)
    response.raise_for_status()
    return response.json()


def webhook_requests(key: str) -> list[dict[str, Any]]:
    return [r for r in recv("/state")["requests"] if r["key"] == key]


# ------------------------------------------------------------------------------------------------ what is compared


NOISE_TABLES = {"rate_limit_counters"}


def fingerprint() -> dict[str, Any]:
    """Everything the restored installation must reproduce: row digests of every table, the privilege list, the revision."""
    digests = {t: lib.table_digest(t) for t in lib.tables() if t not in NOISE_TABLES}
    counts = {t: int(lib.db_one(f'select count(*) from "{t}"')) for t in lib.tables() if t not in NOISE_TABLES}  # noqa: S608
    privileges = [tuple(r) for r in lib.db(
        "select c.relname, c.relacl::text from pg_class c join pg_namespace n on n.oid = c.relnamespace "
        "where n.nspname = 'public' and c.relkind in ('r', 'S') "
        "union all select c.relname || '.' || a.attname, a.attacl::text from pg_attribute a join pg_class c on c.oid = a.attrelid "
        "join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'public' and a.attacl is not null order by 1")]
    return {"digests": digests, "counts": counts, "privileges": [(n, _normal(a)) for n, a in privileges],
            "revision": lib.db_one("select version_num from alembic_version")}


def _normal(acl: str | None) -> str | None:
    """An ACL that only gives the owner its own default rights is the same as none (NULL)."""
    if acl and re.fullmatch(r"\{(\w+)=[a-zA-Z]+/\1\}", acl):
        return None
    return acl


def api_view() -> dict[str, Any]:
    """What a person sees through the API (a content digest of selected answers)."""
    owner = lib.owner()
    project = lib.state()["project_ids"]["main"]
    runs = owner.get("/v1/runs", params={"limit": 200}).json()["items"]
    destinations = owner.get("/v1/alerts/destinations").json()["items"]
    return {
        "runs": sorted((r["id"], r["status"], r["trace_state"]) for r in runs),
        "run_count": len(runs),
        "policy": lib.tier_days(lib.get_policy(owner)),
        "quotas": sorted((q["quota"], q["limit"], q["overridden"]) for q in owner.get(f"/v1/projects/{project}/quotas").json()["quotas"]),
        "destinations": sorted((d["id"], d["name"], d["type"], d["masked_target"]) for d in destinations),
    }


def digest_of(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:16]


# ------------------------------------------------------------------------------------------------ the drill


def seed() -> dict[str, Any]:
    gate("drill-seed")
    suffix = os.environ["E2E_SUFFIX"]
    ws = H.SLUG
    owner_email = f"owner-{suffix}@drill.test"
    H.cli("create-workspace", "--name", "Drill", "--slug", ws)
    H.cli("add-member", "--workspace", ws, "--email", owner_email, "--role", "OWNER")
    H.cli("add-member", "--workspace", ws, "--email", f"viewer-{suffix}@drill.test", "--role", "VIEWER")
    H.cli("create-project", "--workspace", ws, "--name", "main", "--slug", "main", "--capture-mode", "full")
    key = H.cli("create-key", "--workspace", ws, "--project", "main", "--scopes", "events:write", "artifacts:write", "runs:read")
    people = {"owner": owner_email}
    lib.save(people=people, sessions={"owner": H.session_token(owner_email)},
             main_urls=[f"http://127.0.0.1:{API_PORT}"], keys={"main": key}, groups={})
    owner = lib.owner()
    project = next(p["id"] for p in owner.get("/v1/projects").json()["items"] if p["slug"] == "main")
    lib.save(project_ids={"main": project})

    # Runs with events and artifacts of known bytes (the real ingestion path).
    runs: list[lib.Seeded] = []
    for start in range(0, RUNS, 8):
        batch: list[dict[str, Any]] = []
        chunk: list[lib.Seeded] = []
        for _ in range(min(8, RUNS - start)):
            run = {"run_id": str(lib.new_id(lib.IdKind.RUN)), "trace_id": str(lib.new_id(lib.IdKind.TRACE))}
            events = [lib._event(run, 1, "run.started", "drill-agent", span_id=..., attributes={})]
            for n in range(2, EVENTS_PER_RUN):
                events.append(lib._event(run, n, "tool.call.completed", "drill-agent", status="success", duration_ms=5, attributes={"tool.name": f"tool-{n % 4}"}))
            events.append(lib._event(run, EVENTS_PER_RUN, "run.completed", "drill-agent", span_id=..., status="success"))
            batch += events
            chunk.append(lib.Seeded(run["run_id"], lib.uuid_of(run["run_id"]), "main", "drill"))
        response = httpx.post(f"http://127.0.0.1:{API_PORT}/v1/events/batch", json={"events": batch}, headers=lib.bearer(key), timeout=60)
        if response.status_code != 202 or response.json().get("rejected"):
            die(f"ingest -> {response.status_code} {response.text[:200]}")
        for seeded in chunk:
            for index in range(2):
                artifact_id = str(lib.new_id(lib.IdKind.ARTIFACT))
                body = lib._text(ARTIFACT_BYTES, f"drill-{artifact_id[-8:]}-{index}")
                put = httpx.put(f"http://127.0.0.1:{API_PORT}/v1/artifacts/{artifact_id}", content=body, params={"run_id": seeded.run, "kind": "stdout"},
                                headers={**lib.bearer(key), "content-type": "text/plain"}, timeout=60)
                if put.status_code != 201:
                    die(f"artifact -> {put.status_code}")
                seeded.artifact_ids.append(artifact_id)
        runs += chunk
    lib.wait_idle(runs)

    # An alert destination with a sealed signing secret and a target that is a real TLS receiver.
    url = f"https://{HOST}:{MAIN_PORT}/hook/drill"
    created = owner.post("/v1/alerts/destinations", json={"name": "drill-webhook", "type": "webhook", "target": {"url": url}})
    if created.status_code != 201:
        die(f"destination -> {created.status_code} {created.text[:200]}")
    destination = created.json()
    recv("/secret", key="drill", secret=destination["signing_secret"])
    lib.save(destination={"id": destination["id"]}, signing_secret_present=bool(destination["signing_secret"]))
    tested = owner.post(f"/v1/alerts/destinations/{destination['id']}/test").json()
    got = webhook_requests("drill")
    check(tested.get("ok") is True and len(got) == 1 and got[0]["signature_ok"] is True and got[0]["timestamp_ok"] is True,
          "before: a test alert reaches the TLS receiver and its HMAC verifies with the one-time signing secret", result=tested)

    # Policy, quota overrides, a second person with a session.
    check(lib.put_policy(owner, artifacts=20, trace=45, run=200).status_code == 200, "a retention policy is set (20/45/200)")
    quota = owner.put(f"/v1/projects/{project}/quotas", json={"runs_per_day": 12345, "artifact_bytes": 7 * 1024**3})
    check(quota.status_code == 200, "quota overrides are set", status=quota.status_code)
    lib.wait_idle(runs)
    lib.remember("drill", runs)
    sizes = {"runs": len(runs), "events": int(lib.db_one("select count(*) from events")), "artifacts": int(lib.db_one("select count(*) from artifacts")),
             "artifact_bytes": int(lib.db_one("select coalesce(sum(size_bytes), 0) from artifacts"))}
    info(dataset=sizes)
    results["dataset"] = sizes
    return sizes


def destroy(db_name: str, artifacts: Path) -> None:
    admin(f'drop database "{db_name}" with (force)')
    shutil.rmtree(artifacts)
    check(not artifacts.exists(), "the source artifact directory is deleted")


def database_exists(name: str) -> bool:
    import asyncio  # noqa: PLC0415

    import asyncpg  # noqa: PLC0415

    async def go() -> bool:
        conn = await asyncpg.connect(lib.plain(ADMIN))
        try:
            return bool(await conn.fetchval("select 1 from pg_database where datname = $1", name))
        finally:
            await conn.close()

    return asyncio.run(go())


def public_table_count(name: str) -> int:
    import asyncio  # noqa: PLC0415

    import asyncpg  # noqa: PLC0415

    async def go() -> int:
        conn = await asyncpg.connect(lib.plain(url_for(name)))
        try:
            return int(await conn.fetchval("select count(*) from information_schema.tables where table_schema = 'public'"))
        finally:
            await conn.close()

    return asyncio.run(go())


def compose_backup(src_db: str, artifacts: Path, work: Path, bundles_dir: Path) -> dict[str, Any]:
    """The compose `backup` service built from the API image: pg_dump/openssl from the Dockerfile's packages, the container's uid
    writing the bundle. Skipped (with the reason printed) ONLY when Docker is not available; a failure is a failure."""
    probe = subprocess.run(["docker", "info"], capture_output=True, text=True, check=False)  # noqa: S603, S607
    if probe.returncode != 0:
        print("  SKIP [drill-compose] Docker is not available on this machine; the compose backup service path is NOT exercised here", flush=True)
        results["compose"] = {"skipped": "docker unavailable"}
        return results["compose"]
    # Bind mounts must be paths the Docker daemon can see (Colima and Docker Desktop share the home directory, not the system
    # temporary directory), so the compose step works in a directory under the repository's git-ignored .local/, with a copy of the
    # artifact directory (the same bytes the other backups read). The container runs as uid 10001, not as this user: it must be able
    # to read the copy and to write ./backups; in a real compose stack the artifact volume is owned by that uid.
    share = ROOT / ".local" / f"backup-drill-{os.getpid()}"
    shutil.rmtree(share, ignore_errors=True)
    share.mkdir(parents=True)
    shutil.copytree(artifacts, share / "artifacts")
    subprocess.run(["chmod", "-R", "a+rX", str(share / "artifacts")], check=False)  # noqa: S603, S607
    share.chmod(0o711)
    out_dir = share / "backups"
    out_dir.mkdir()
    out_dir.chmod(0o777)
    owner_url = url_for(src_db).replace("localhost", "host.docker.internal").replace("127.0.0.1", "host.docker.internal")
    override = work / "compose.override.yml"
    # A project-private image tag: building must not replace the developer's own agent-black-box-api:local.
    override.write_text(
        "services:\n  backup:\n    image: abb-p19b6-drill-backup:local\n    extra_hosts: ['host.docker.internal:host-gateway']\n    volumes:\n"
        f"      - {share / 'artifacts'}:/drill/artifacts:ro\n    environment:\n"
        f"      ABB_BACKUP_DATABASE_URL: '{owner_url}'\n      ABB_ARTIFACT_DIR: /drill/artifacts\n")
    override.chmod(0o600)
    base = ["docker", "compose", "-p", "abb-p19b6-drill", "-f", str(ROOT / "docker-compose.yml"), "-f", str(override), "--profile", "ops"]
    environment = dict(os.environ)
    environment["ABB_BACKUP_DIR"] = str(out_dir)
    started = time.perf_counter()
    built = subprocess.run([*base, "build", "backup"], capture_output=True, text=True, check=False, env=environment, cwd=ROOT)  # noqa: S603
    results["compose"] = {"build_seconds": round(time.perf_counter() - started, 1)}
    check(built.returncode == 0, "the API image builds with postgresql-client and openssl (docker compose build backup)", exit=built.returncode,
          tail=built.stderr.strip()[-300:] if built.returncode else "")
    if built.returncode != 0:
        return results["compose"]
    started = time.perf_counter()
    made = subprocess.run([*base, "run", "--rm", "--no-deps", "backup", "create", "--out", "/backups/compose-bundle"], capture_output=True, text=True,
                          check=False, env=environment, cwd=ROOT)  # noqa: S603
    results["compose"]["create_seconds"] = round(time.perf_counter() - started, 1)
    line = next((x for x in made.stdout.splitlines() if x.startswith("result=")), "")
    check(made.returncode == 0 and "result=ok" in line, "compose: `backup create` in the container writes a bundle through the mounted ./backups (exit 0)", exit=made.returncode,
          summary=line, tail=made.stderr.strip()[-300:] if made.returncode else "")
    verified = subprocess.run([*base, "run", "--rm", "--no-deps", "backup", "verify", "/backups/compose-bundle"], capture_output=True, text=True,
                              check=False, env=environment, cwd=ROOT)  # noqa: S603
    check(verified.returncode == 0 and "result=ok" in verified.stdout, "compose: `backup verify` inside the container accepts the bundle", exit=verified.returncode)
    try:
        manifest = json.loads((out_dir / "compose-bundle" / "MANIFEST.json").read_text())
        results["compose"]["manifest_seen_from_host"] = True
        results["compose"]["manifest"] = manifest
        results["compose"]["row_counts_equal_module_bundle"] = manifest.get("row_counts") == json.loads((bundles_dir / "plain" / "MANIFEST.json").read_text()).get("row_counts")
        check(results["compose"]["row_counts_equal_module_bundle"], "compose: the container's bundle records the same row counts as the module CLI's")
    except OSError:
        results["compose"]["manifest_seen_from_host"] = False  # Linux: the bundle is 0700 and owned by the container's uid, as designed
    subprocess.run([*base, "down", "--volumes", "--remove-orphans"], capture_output=True, text=True, check=False, env=environment, cwd=ROOT)  # noqa: S603
    # The bundle is owned by the container's uid (0700): when the host cannot remove it, the same image does.
    shutil.rmtree(share, ignore_errors=True)
    if share.exists():
        subprocess.run(["docker", "run", "--rm", "--user", "root", "--entrypoint", "sh", "-v", f"{share}:/s", "abb-p19b6-drill-backup:local", "-c", "rm -rf /s/backups /s/artifacts"],  # noqa: S603, S607
                       capture_output=True, text=True, check=False)
        shutil.rmtree(share, ignore_errors=True)
    subprocess.run(["docker", "image", "rm", "-f", "abb-p19b6-drill-backup:local"], capture_output=True, text=True, check=False)  # noqa: S603, S607
    return results["compose"]


def drill() -> None:
    src, restored, wrong, nonempty = "abb_p19b6_drill_src", "abb_p19b6_drill_restored", "abb_p19b6_drill_wrong", "abb_p19b6_drill_nonempty"
    src_artifacts, bundles = WORK / "artifacts-src", WORK / "bundles"
    bundles.mkdir()
    bundles.chmod(0o700)
    src_artifacts.mkdir()
    passphrase = WORK / "passphrase"
    passphrase.write_text(PASSPHRASE + "\n")
    passphrase.chmod(0o600)
    wrong_pass = WORK / "wrong-passphrase"
    wrong_pass.write_text("an entirely different passphrase 123\n")
    wrong_pass.chmod(0o600)

    # ---------------------------------------------------------------- the source installation
    use_database(src)
    start_stack(src_artifacts, "src")
    seed()
    files_before = lib.files_digest(src_artifacts)
    check(len(files_before) == results["dataset"]["artifacts"], "the artifact directory holds one file per artifact row", files=len(files_before))
    view_before = api_view()
    stop_stack()  # a quiet source: what is recorded now is what the bundles must hold (the backup itself does not need this)
    before = fingerprint()
    lib.save(recorded=digest_of({"v": view_before, "d": before["digests"]}))
    info(tables=len(before["counts"]), revision=before["revision"], runs=view_before["run_count"], privileges=len(before["privileges"]))

    # ---------------------------------------------------------------- backups with the real tooling
    gate("drill-backup")
    plain_bundle = bundles / "plain"
    code, out, err, seconds = module("abb_api.backup", "create", "--out", str(plain_bundle), env=backup_env(src, src_artifacts))
    summary = kv(out.strip().splitlines()[-1]) if out.strip() else {}
    check(code == 0 and summary.get("result") == "ok", "backup create (module CLI): exit 0", exit=code, summary=summary)
    results["backup"] = {"seconds": round(seconds, 2), "bundle_bytes": dir_size(plain_bundle), "artifact_files": summary.get("artifact_files"), "revision": summary.get("revision")}
    code, out, err, _ = module("abb_api.backup", "verify", str(plain_bundle))
    check(code == 0 and "result=ok" in out, "backup verify (no database touched): the bundle's checksums match", exit=code)
    manifest = json.loads((plain_bundle / "MANIFEST.json").read_text())
    check(manifest["schema_revision"] == before["revision"], "the manifest records the schema revision of the source", revision=manifest["schema_revision"])
    mode = (plain_bundle.stat().st_mode & 0o777, (plain_bundle / "database.dump").stat().st_mode & 0o777)
    check(mode == (0o700, 0o600), "the bundle is private (directory 0700, files 0600)", mode=[oct(m) for m in mode])
    encrypted = bundles / "encrypted"
    code, out, err, enc_seconds = module("abb_api.backup", "create", "--out", str(encrypted), "--encrypt", "--passphrase-file", str(passphrase), env=backup_env(src, src_artifacts))
    check(code == 0 and "encrypted=true" in out, "backup create --encrypt: exit 0", exit=code)
    results["backup"].update({"encrypted_seconds": round(enc_seconds, 2), "encrypted_bundle_bytes": dir_size(encrypted)})
    check(b"PGDMP" not in (next(encrypted.glob("database.dump*")).read_bytes()[:16]), "the encrypted database dump does not start with the PostgreSQL archive magic (it is ciphertext)")
    compose_backup(src, src_artifacts, WORK, bundles)

    # ---------------------------------------------------------------- DESTROY the source
    gate("drill-destroy")
    destroy(src, src_artifacts)
    check(not database_exists(src), "the source database is dropped")

    # ---------------------------------------------------------------- refusals through the CLI (nothing is created or changed)
    gate("drill-refusals")
    admin(f'create database "{wrong}"')
    wrong_artifacts = WORK / "artifacts-wrong"
    code, out, err, _ = module("abb_api.backup", "restore", str(encrypted), "--passphrase-file", str(wrong_pass), env=restore_env(wrong, wrong_artifacts))
    check(code == 4, "a WRONG passphrase is refused (exit 4: integrity)", exit=code)
    check(public_table_count(wrong) == 0 and (not wrong_artifacts.exists() or not any(wrong_artifacts.iterdir())), "and restored nothing: the database has no table and the artifact directory is empty")
    check("passphrase" not in err.replace("wrong passphrase", "").lower() or PASSPHRASE not in err, "no passphrase is printed")
    tampered = bundles / "tampered"
    shutil.copytree(plain_bundle, tampered)
    tar = tampered / "artifacts.tar"
    data = bytearray(tar.read_bytes())
    data[len(data) // 2] ^= 0xFF
    tar.write_bytes(bytes(data))
    code_v, _, err_v, _ = module("abb_api.backup", "verify", str(tampered))
    code_r, _, err_r, _ = module("abb_api.backup", "restore", str(tampered), env=restore_env(wrong, wrong_artifacts))
    check(code_v == 4 and code_r == 4 and "artifacts.tar" in err_v, "a tampered bundle fails verify and restore (exit 4) and names the file", verify=code_v, restore=code_r)
    check(public_table_count(wrong) == 0, "the tampered restore created nothing")
    import asyncio  # noqa: PLC0415

    import asyncpg  # noqa: PLC0415

    async def make_nonempty() -> None:
        conn = await asyncpg.connect(lib.plain(url_for(nonempty)))
        await conn.execute("create table leftovers (id int)")
        await conn.close()

    admin(f'create database "{nonempty}"')
    asyncio.run(make_nonempty())
    code, _, err, _ = module("abb_api.backup", "restore", str(plain_bundle), env=restore_env(nonempty, WORK / "artifacts-nonempty"))
    check(code == 3 and public_table_count(nonempty) == 1, "restoring into a NON-EMPTY database is refused (exit 3) and leaves it untouched", exit=code)
    full_dir = WORK / "artifacts-full"
    full_dir.mkdir()
    (full_dir / "stray.txt").write_text("x")
    code, _, err, _ = module("abb_api.backup", "restore", str(plain_bundle), env=restore_env(wrong, full_dir))
    check(code == 3 and public_table_count(wrong) == 0, "restoring into a NON-EMPTY artifact directory is refused (exit 3)", exit=code)

    # ---------------------------------------------------------------- RESTORE into a fresh database and directory
    gate("drill-restore")
    admin(f'create database "{restored}"')
    restored_artifacts = WORK / "artifacts-restored"
    code, out, err, seconds = module("abb_api.backup", "restore", str(plain_bundle), env=restore_env(restored, restored_artifacts))
    summary = kv(out.strip().splitlines()[-1]) if out.strip() else {}
    check(code == 0 and summary.get("result") == "ok", "restore into a fresh database and directory: exit 0, result=ok", exit=code, summary=summary, stderr=err.strip()[-200:] if code else "")
    results["restore"] = {"wall_seconds": round(seconds, 2), "tool_seconds": summary.get("seconds"), "upgraded": summary.get("upgraded"),
                          "artifact_files": summary.get("artifact_files")}
    use_database(restored)
    after = fingerprint()
    changed = [t for t in before["digests"] if before["digests"][t] != after["digests"].get(t)]
    check(not changed and before["counts"] == after["counts"], "every table of the restored database has the same row count and the same content digest", tables=len(after["digests"]), differing=changed)
    check(lib.files_digest(restored_artifacts) == files_before, "every artifact file is byte-identical (sha256 of each)", files=len(files_before))
    check(after["privileges"] == before["privileges"], "the grants are identical (table, sequence and column privileges)", entries=len(after["privileges"]))
    from abb_api.backup.restore import alembic_root, code_head  # noqa: PLC0415

    head = code_head(alembic_root(os.environ))
    check(after["revision"] == head == before["revision"], "alembic head equals the code head", revision=head)

    # ---------------------------------------------------------------- the API and worker on the restored data
    gate("drill-serve")
    start_stack(restored_artifacts, "restored")
    view_after = api_view()
    check(view_after == view_before, "the API serves the same runs, retention policy, quota overrides and alert destinations (content digest equal)",
          runs=view_after["run_count"], digest=digest_of(view_after))
    owner = lib.owner()
    check(owner.get("/v1/me").status_code == 200, "the owner's session from BEFORE the backup still works (sessions are in the bundle)")
    fresh = H.session_token(lib.state()["people"]["owner"])
    check(bool(fresh), "a new session can be minted on the restored installation")
    run = lib.seeded("drill")[5]
    detail = owner.get(f"/v1/runs/{run.run}/events", params={"limit": 50}).json()["items"]
    check(len(detail) == EVENTS_PER_RUN, "a restored run's events are served", events=len(detail))
    destination = lib.state()["destination"]["id"]
    sent_before = len(webhook_requests("drill"))
    tested = owner.post(f"/v1/alerts/destinations/{destination}/test").json()
    got = webhook_requests("drill")
    check(tested.get("ok") is True and len(got) == sent_before + 1 and got[-1]["signature_ok"] is True,
          "the alert destination's sealed signing secret still OPENS: a test alert reaches the receiver and the HMAC verifies with the ORIGINAL secret", result=tested)
    ingest = lib.ingest_runs(f"http://127.0.0.1:{API_PORT}", lib.state()["keys"]["main"], "main", "after-restore", 2)
    lib.wait_idle(ingest)
    check(len(ingest) == 2 and int(lib.db_one("select count(*) from runs where id = any($1::uuid[])", [r.uuid for r in ingest])) == 2,
          "the restored installation ingests new runs (API key from the bundle, worker running)")
    stop_stack()

    # ---------------------------------------------------------------- the encrypted bundle restores to the same state
    gate("drill-encrypted")
    restored_enc = "abb_p19b6_drill_restored_enc"
    admin(f'create database "{restored_enc}"')
    code, out, err, seconds = module("abb_api.backup", "restore", str(encrypted), "--passphrase-file", str(passphrase), env=restore_env(restored_enc, WORK / "artifacts-restored-enc"))
    check(code == 0 and "result=ok" in out, "the encrypted bundle restores with the right passphrase (exit 0)", exit=code)
    results["restore"]["encrypted_wall_seconds"] = round(seconds, 2)
    use_database(restored_enc)
    enc_after = fingerprint()
    check(all(enc_after["digests"][t] == before["digests"][t] for t in before["digests"]) and lib.files_digest(WORK / "artifacts-restored-enc") == files_before,
          "and its content equals the plain restore's (every table digest, every file)")

    # ---------------------------------------------------------------- an OLDER bundle is upgraded on restore
    gate("drill-older")
    import sqlalchemy  # noqa: F401, PLC0415
    from abb_api.backup.restore import _script  # noqa: PLC0415

    revisions = [r.revision for r in _script(alembic_root(os.environ)).walk_revisions()]  # newest first
    previous = revisions[1]
    old_db = "abb_p19b6_drill_old"
    admin(f'create database "{old_db}"')
    migrate = subprocess.run([PY, "-m", "alembic", "upgrade", previous], cwd=API_DIR, env={**{k: v for k, v in os.environ.items() if not k.startswith("ABB_")}, "ABB_DATABASE_URL": url_for(old_db), "ABB_ENVIRONMENT": "development"},  # noqa: S603
                             capture_output=True, text=True, check=False)
    check(migrate.returncode == 0, f"a database is migrated to the PREVIOUS revision ({previous})", exit=migrate.returncode)
    use_database(old_db)
    lib.db("insert into workspaces (id, name, slug) values (gen_random_uuid(), 'Older', 'older-ws')")
    old_artifacts = WORK / "artifacts-old"
    old_artifacts.mkdir()
    old_bundle = bundles / "old"
    code, out, err, _ = module("abb_api.backup", "create", "--out", str(old_bundle), env=backup_env(old_db, old_artifacts))
    old_manifest = json.loads((old_bundle / "MANIFEST.json").read_text()) if code == 0 else {}
    check(code == 0 and old_manifest.get("schema_revision") == previous, "a bundle is taken at the previous revision", revision=old_manifest.get("schema_revision"))
    old_target = "abb_p19b6_drill_old_restored"
    admin(f'create database "{old_target}"')
    code, out, err, _ = module("abb_api.backup", "restore", str(old_bundle), env=restore_env(old_target, WORK / "artifacts-old-restored"))
    summary = kv(out.strip().splitlines()[-1]) if out.strip() else {}
    check(code == 0 and summary.get("upgraded") == "true" and summary.get("revision_in_bundle") == previous and summary.get("revision_now") == head,
          "the older bundle is UPGRADED to the code head on restore", summary=summary)
    use_database(old_target)
    check(lib.db_one("select version_num from alembic_version") == head and int(lib.db_one("select count(*) from workspaces where slug = 'older-ws'")) == 1,
          "the upgraded database is at head and holds the older bundle's data")

    # ---------------------------------------------------------------- rotate the alert key on the restored installation
    gate("drill-rotate")
    use_database(restored)
    start_stack(restored_artifacts, "rotated")
    sealed_before = lib.db_one("select signing_secret_encrypted from alert_destinations limit 1")
    key_before = lib.db_one("select key_id from alert_destinations limit 1")
    dry = module("abb_api.rotate", "alerts", "--dry-run", env={"ABB_DATABASE_URL": url_for(restored)})
    check(dry[0] == 0 and "verdict=dry_run_nothing_changed" in dry[1], "rotate alerts --dry-run changes nothing", exit=dry[0])
    code, out, err, _ = module("abb_api.rotate", "alerts", "--new-key", env={"ABB_DATABASE_URL": url_for(restored)})
    check(code == 0 and "failed=0" in out, "rotate alerts --new-key re-seals every destination (exit 0, failed=0)", summary=out.strip().splitlines()[:1])
    sealed_after = lib.db_one("select signing_secret_encrypted from alert_destinations limit 1")
    check(sealed_after != sealed_before and lib.db_one("select key_id from alert_destinations limit 1") != key_before, "the stored ciphertext and its key id changed")
    stop_stack()
    start_stack(restored_artifacts, "rotated2")  # restart: the API and worker load the new key from the database
    owner = lib.owner()
    sent_before = len(webhook_requests("drill"))
    tested = owner.post(f"/v1/alerts/destinations/{destination}/test").json()
    got = webhook_requests("drill")
    check(tested.get("ok") is True and len(got) == sent_before + 1 and got[-1]["signature_ok"] is True,
          "after the restart the destination still delivers and the receiver still verifies the ORIGINAL secret (only the sealing key changed)", result=tested)
    code, out, err, _ = module("abb_api.rotate", "alerts", env={"ABB_DATABASE_URL": url_for(restored)})
    check(code == 0 and "verdict=old_keys_can_be_retired" in out, "a rerun converges and says the old keys can be retired", verdict=[x for x in out.splitlines() if x.startswith("verdict")])
    stop_stack()

    results["load_after"] = [round(x, 2) for x in os.getloadavg()]
    out_path = os.environ.get("BACKUP_RESTORE_E2E_OUT")
    if out_path:
        Path(out_path).write_text(json.dumps(results, indent=2, default=str))
    info(drill=results)


def summary() -> None:
    gate("summary")
    rows = [json.loads(line) for line in lib.RESULTS.read_text().splitlines() if line.strip()]
    by_gate: dict[str, list[bool]] = {}
    for row in rows:
        by_gate.setdefault(row["gate"], []).append(row["ok"])
    print("\n  gate                      checks  passed  failed")
    for name, oks in by_gate.items():
        print(f"  {name:<24} {len(oks):>7}  {sum(oks):>6}  {len(oks) - sum(oks):>6}")
    bad: dict[str, list[str]] = {}
    scanned = 0
    for log in lib.all_logs():
        scanned += 1
        for line in lib.log_lines(log, "Traceback", "job failed", "Unhandled exception", "ERROR:", '"level": "ERROR"', '"level":"ERROR"'):
            bad.setdefault(log.name, []).append(line)
    check(not bad, "no 'job failed', Traceback or ERROR line in any process log", logs=scanned, offenders={k: v[:3] for k, v in bad.items()})
    failures = [r for r in rows if not r["ok"]]
    print(f"\n  TOTAL {len(rows)} checks, {len(rows) - len(failures)} passed, {len(failures)} failed")
    for row in failures:
        print(f"  FAIL [{row['gate']}] {row['label']}")
    lib.finish()
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "drill"
    try:
        if stage == "drill":
            drill()
            lib.finish()
        elif stage == "summary":
            summary()
        else:
            die(f"unknown stage {stage}")
    finally:
        stop_stack()

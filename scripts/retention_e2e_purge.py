"""Gates 5 and 6 of scripts/retention-e2e.sh: the privileges of the database roles over real connections (and the API's environment),
and the purge commands (ADR-163) on a real stack."""

import uuid
from urllib.parse import urlparse

import httpx

import retention_e2e_lib as lib
from retention_e2e_lib import check, gate, info

# Tables whose rows change by themselves while the stack runs (counters, queue state, sessions, audit): excluded from the "everything
# else is byte-identical" digests. The data tables are what the claim is about.
NOISE = {"rate_limit_counters", "audit_log", "outbox_jobs", "sessions", "users", "retention_reports", "analytics_dirty", "purge_tombstones",
         "project_usage_daily", "alembic_version"}

DENIED = "42501"  # insufficient_privilege


# ------------------------------------------------------------------------------------------------ gate 5


def gate5() -> None:
    gate("gate5-privileges")
    runtime, retention = lib.runtime_url(), lib.retention_url()
    denied_runtime = {
        "DELETE events": "delete from events where false",
        "UPDATE events": "update events set agent_id = agent_id where false",
        "DELETE runs": "delete from runs where false",
        "INSERT retention_run_progress": "insert into retention_run_progress (workspace_id, run_id, tier) values (gen_random_uuid(), gen_random_uuid(), 'trace')",
        "DELETE retention_run_progress": "delete from retention_run_progress where false",
        "INSERT retention_tombstones": "insert into retention_tombstones (workspace_id, run_id, tier, mode) values (gen_random_uuid(), gen_random_uuid(), 'trace', 'enforce')",
        "UPDATE retention_tombstones": "update retention_tombstones set mode = mode where false",
        "DELETE retention_tombstones": "delete from retention_tombstones where false",
        "INSERT retention_reports": "insert into retention_reports (tier, mode) values ('trace', 'enforce')",
        "DELETE retention_reports": "delete from retention_reports where false",
        "UPDATE purge_tombstones": "update purge_tombstones set state = state where false",
        "DELETE purge_tombstones": "delete from purge_tombstones where false",
        "DELETE audit_log": "delete from audit_log where false",
        "UPDATE audit_log": "update audit_log set action = action where false",
    }
    for label, sql in denied_runtime.items():
        state = lib.sqlstate_as(runtime, sql)
        check(state == DENIED, f"runtime role (the API and worker): {label} is refused (42501)", sqlstate=state)
    # What the runtime role legitimately does to the run tables (the summariser updates runs; a failed upload removes its artifact row):
    # not a retention path, recorded so a reader sees the line is drawn at events, deletion of runs and the job's own tables.
    info(runtime_may_update_runs=lib.sqlstate_as(runtime, "update runs set updated_at = updated_at where false") is None,
         runtime_may_delete_artifacts=lib.sqlstate_as(runtime, "delete from artifacts where false") is None)
    for label, sql in {"SELECT retention_reports": "select count(*) from retention_reports", "SELECT retention_tombstones": "select count(*) from retention_tombstones",
                       "SELECT purge_tombstones": "select count(*) from purge_tombstones", "SELECT events": "select count(*) from events"}.items():
        state = lib.sqlstate_as(runtime, sql)
        check(state is None, f"runtime role: {label} works (the API serves it)", sqlstate=state)

    denied_retention = {
        "SELECT api_keys.secret_hash": "select secret_hash from api_keys limit 1",
        "SELECT server_secrets.secret": "select secret from server_secrets limit 1",
        "SELECT alert_destinations.config_encrypted": "select config_encrypted from alert_destinations limit 1",
        "SELECT alert_destinations.signing_secret_encrypted": "select signing_secret_encrypted from alert_destinations limit 1",
        "INSERT api_keys": "insert into api_keys (id) values (gen_random_uuid())",
        "DELETE server_secrets": "delete from server_secrets where false",
        "UPDATE events": "update events set agent_id = agent_id where false",
        "INSERT events": "insert into events (event_id) values (gen_random_uuid())",
        "UPDATE runs.status": "update runs set status = status where false",
        "UPDATE alert_destinations": "update alert_destinations set name = name where false",
        "DELETE retention_policies": "delete from retention_policies where false",
    }
    for label, sql in denied_retention.items():
        state = lib.sqlstate_as(retention, sql)
        check(state == DENIED, f"retention role: {label} is refused (42501)", sqlstate=state)
    for label, sql in {"DELETE events": "delete from events where false", "DELETE runs": "delete from runs where false",
                       "UPDATE runs.trace_state": "update runs set trace_state = trace_state where false",
                       "SELECT events": "select count(*) from events", "SELECT retention_policies": "select count(*) from retention_policies"}.items():
        state = lib.sqlstate_as(retention, sql)
        check(state is None, f"retention role: {label} is allowed (it is the job's privilege)", sqlstate=state)

    # The API and the worker hold no retention credential (checked against the live processes' environment).
    password = urlparse(lib.retention_url()).password or ""
    login = lib.retention_login()
    for name in ("api", "worker"):
        pid = int((lib.WORK / f"{name}.pid").read_text())
        env = lib.process_environment(pid)
        check(len(env) > 100, f"the {name} process's environment can be inspected", pid=pid)
        check("ABB_RETENTION_DATABASE_URL" not in env and login not in env and (password and password not in env),
              f"the {name} process has no retention credential in its environment (no URL variable, no login name, no password)")
    # A retention process pointed at a more privileged role refuses to start in production (and prints no credential).
    owner_url = __import__("os").environ["E2E_OWNER_DB_URL"]
    code, proc = lib.run_retention("run-once", mode="dry_run", env={"ABB_RETENTION_DATABASE_URL": owner_url, "ABB_ENVIRONMENT": "production"}, name="overprivileged")
    text = proc.log.read_text(errors="replace")
    owner_password = urlparse(owner_url).password or ""
    check(code == 2 and "refusing to start" in text.lower() and owner_password not in text,
          "production refuses to start with a role that is more than abb_retention (exit 2), and prints no password", exit=code)
    lib.finish()


# ------------------------------------------------------------------------------------------------ gate 6


def columns(column: str) -> list[str]:
    rows = lib.db("select c.table_name from information_schema.columns c join information_schema.tables t using (table_schema, table_name) "
                  "where c.table_schema = 'public' and t.table_type = 'BASE TABLE' and c.column_name = $1 order by 1", column)
    return [r[0] for r in rows]


def rows_of_project(project: uuid.UUID, runs: list[uuid.UUID]) -> dict[str, int]:
    """Rows of the project in EVERY table that has a project_id column, plus every table with a run_id column (found in
    information_schema, not from the job's own inventory)."""
    out: dict[str, int] = {}
    for table in columns("project_id"):
        n = int(lib.db_one(f'select count(*) from "{table}" where project_id = $1', project))  # noqa: S608
        if n:
            out[table] = n
    for table in columns("run_id"):
        n = int(lib.db_one(f'select count(*) from "{table}" where run_id = any($1::uuid[])', runs))  # noqa: S608
        if n:
            out[table] = out.get(table, 0) + n
    return out


def rows_of_workspace(workspace: uuid.UUID) -> dict[str, int]:
    out: dict[str, int] = {}
    for table in columns("workspace_id"):
        if table == "purge_tombstones":  # the permanent platform record of the purge itself (ids, counts, a hash of the slug)
            continue
        n = int(lib.db_one(f'select count(*) from "{table}" where workspace_id = $1', workspace))  # noqa: S608
        if n:
            out[table] = n
    return out


def outside_digest(project: uuid.UUID | None, workspace: uuid.UUID | None, own_workspace: uuid.UUID | None = None) -> dict[str, str]:
    """A digest of every row that is NOT in the scope being purged, per table (noise tables and volatile columns removed)."""
    project_tables, workspace_tables = set(columns("project_id")), set(columns("workspace_id"))
    out: dict[str, str] = {}
    for table in sorted((project_tables | workspace_tables) - NOISE):
        if workspace is not None and table in workspace_tables:
            out[table] = lib.table_digest(table, "workspace_id <> $1", workspace)
        elif project is not None and table in project_tables:
            out[table] = lib.table_digest(table, "project_id is distinct from $1", project)
        elif own_workspace is not None and table in workspace_tables:
            out[table] = lib.table_digest(table, "workspace_id <> $1", own_workspace)
    return out


def purge_text(proc: lib.RetentionProc) -> str:
    return proc.log.read_text(errors="replace")


def unknown_key_reply(base: str) -> httpx.Response:
    return httpx.post(f"{base}/v1/events/batch", json={"events": []}, headers=lib.bearer(lib.H.random_token()), timeout=30)


def gate6() -> None:
    gate("gate6-purge")
    base = lib.H.main_url()
    keys = lib.state()["keys"]
    ws_a = lib.workspace_uuid(lib.owner())
    slug_a, slug_c = lib.H.SLUG, lib.state()["ws_c"]
    owner = lib.owner()
    doomed_project = lib.uuid_of(lib.state()["project_ids"]["doomed"])
    doomed_runs = [r.uuid for r in lib.seeded("doomed")]

    # A live key works before the purge (this also adds one run to the doomed project).
    run_id, events = lib.run_events("doomed-agent")
    first = httpx.post(f"{base}/v1/events/batch", json={"events": events}, headers=lib.bearer(keys["doomed"]), timeout=30)
    check(first.status_code == 202, "before: the project's API key ingests (202)")
    lib.wait_until("the new run to be stored", lambda: int(lib.db_one("select count(*) from runs where id = $1", lib.uuid_of(run_id))) == 1, 60)
    doomed_runs.append(lib.uuid_of(run_id))
    lib.wait_idle()

    # ---- purge-project: dry run
    keys_of_files = lib.run_keys(doomed_runs)
    before_rows = rows_of_project(doomed_project, doomed_runs)
    before_outside = outside_digest(doomed_project, None, ws_a)
    before_files = lib.files_digest()
    before_all = lib.counts()
    code, dry = lib.run_retention("purge-project", "--workspace", slug_a, "--project", "doomed", name="purge-project-dry")
    text = purge_text(dry)
    info(exit=code, project_rows=before_rows, files=len(keys_of_files))
    check(code == 0 and "dry run: nothing was deleted" in text, "purge-project without --confirm is a dry run (exit 0) and says nothing was deleted")
    check(f"runs {len(doomed_runs)}" in text and "events" in text, "the dry run prints per-table counts that match the project (runs, events, ...)", output_tail=text.strip().splitlines()[-8:])
    check(lib.counts() == before_all and lib.files_digest() == before_files, "the dry run changed no table's row count and no file")
    code, wrong = lib.run_retention("purge-project", "--workspace", slug_a, "--project", "doomed", "--confirm", "not-the-slug", name="purge-project-wrong")
    check(code == 2 and lib.counts() == before_all and lib.files_digest() == before_files, "a wrong --confirm is refused (exit 2) and deletes nothing", exit=code)
    code, wrong_case = lib.run_retention("purge-project", "--workspace", slug_a, "--project", "doomed", "--confirm", "DOOMED", name="purge-project-case")
    check(code == 2 and lib.counts() == before_all, "the slug must match exactly, case included (exit 2)")

    # ---- purge-project: confirmed
    code, done = lib.run_retention("purge-project", "--workspace", slug_a, "--project", "doomed", "--confirm", "doomed", name="purge-project")
    text = purge_text(done)
    check(code == 0 and "done" in text, "purge-project --confirm doomed completes (exit 0)", exit=code)
    left = rows_of_project(doomed_project, doomed_runs)
    check(not left, "zero rows of the project in EVERY table with a project_id or run_id column (information_schema scan)", scanned=len(columns("project_id")) + len(columns("run_id")), before=sum(before_rows.values()), left=left)
    check(not any(lib.file_exists(k) for k in keys_of_files) and keys_of_files, "zero artifact files of the project", files=len(keys_of_files))
    check(lib.files_digest() == {k: v for k, v in before_files.items() if k not in set(keys_of_files)}, "every other artifact file is byte-identical")
    after_outside = outside_digest(doomed_project, None, ws_a)
    check(after_outside == before_outside, "all other projects and workspaces are byte-identical in every table", tables=len(after_outside),
          changed=[t for t in after_outside if after_outside[t] != before_outside.get(t)])
    after = httpx.post(f"{base}/v1/events/batch", json={"events": events}, headers=lib.bearer(keys["doomed"]), timeout=30)
    unknown = unknown_key_reply(base)
    check(after.status_code == 401 and unknown.status_code == 401 and lib.H.scrub(after.json()) == lib.H.scrub(unknown.json()),
          "the project's API key stops working at once with the uniform 401 (same answer as a key that never existed)", status=after.status_code, code=lib.H.error_code(after))
    check(owner.get(f"/v1/runs/{lib.seeded('doomed')[0].run}").status_code == 404, "API: a run of the purged project is 404")
    other = httpx.post(f"{base}/v1/events/batch", json={"events": []}, headers=lib.bearer(keys["sibling"]), timeout=30)
    check(other.status_code in {200, 202}, "the sibling project's key still works", status=other.status_code)
    code, again = lib.run_retention("purge-project", "--workspace", slug_a, "--project", "doomed", name="purge-project-again")
    check(code == 3, "a second purge of the same project says not found (exit 3)", exit=code)
    purge_record = lib.db("select scope, state, completed_at is not null from purge_tombstones where project_id = $1", doomed_project)
    check(len(purge_record) == 1 and purge_record[0][1] == "done", "one purge record (ids and counts only) survives", record=[tuple(r) for r in purge_record])
    check(lib.audit_actions(owner).count("retention.purge.project") == 1, "one counts-only audit entry, retention.purge.project")

    # ---- purge-workspace
    ws_c = lib.db_one("select id from workspaces where slug = $1", slug_c)
    c_runs = [r.uuid for r in lib.seeded("c_young")]
    c_files = lib.run_keys(c_runs)
    c_only_before = lib.db_one("select count(*) from users where email = $1", lib.state()["c_only"])
    c_shared_before = lib.db_one("select count(*) from users where email = $1", lib.state()["c_shared"])
    before_rows_c = rows_of_workspace(ws_c)
    outside_before = outside_digest(None, ws_c)
    files_before = lib.files_digest()
    all_before = lib.counts()
    code, dry = lib.run_retention("purge-workspace", "--workspace", slug_c, name="purge-workspace-dry")
    text = purge_text(dry)
    check(code == 0 and "dry run: nothing was deleted" in text and "people" in text, "purge-workspace without --confirm is a dry run that also counts the people (exit 0)")
    check(lib.counts() == all_before and lib.files_digest() == files_before, "the dry run changed nothing")
    code, wrong = lib.run_retention("purge-workspace", "--workspace", slug_c, "--confirm", slug_a, name="purge-workspace-wrong")
    check(code == 2 and lib.counts() == all_before, "naming another workspace in --confirm is refused (exit 2)")
    code, done = lib.run_retention("purge-workspace", "--workspace", slug_c, "--confirm", slug_c, name="purge-workspace")
    check(code == 0, "purge-workspace --confirm <slug> completes (exit 0)", exit=code)
    left = rows_of_workspace(ws_c)
    check(c_only_before == 1 and not left and sum(before_rows_c.values()) > 0, "zero rows of the workspace in EVERY table with a workspace_id column", before=sum(before_rows_c.values()), left=left)
    check(int(lib.db_one("select count(*) from workspaces where slug = $1", slug_c)) == 0 and not any(lib.file_exists(k) for k in c_files) and c_files,
          "the workspace row and its artifact files are gone", files=len(c_files))
    check(lib.files_digest() == {k: v for k, v in files_before.items() if k not in set(c_files)}, "every other artifact file is byte-identical")
    check(outside_digest(None, ws_c) == outside_before, "every other workspace is byte-identical in every table")
    check(int(lib.db_one("select count(*) from users where email = $1", lib.state()["c_only"])) == 0, "users rule: the person who belonged only to that workspace is deleted")
    check(int(lib.db_one("select count(*) from users where email = $1", lib.state()["c_shared"])) == c_shared_before == 1, "users rule: the person who also belongs to workspace B is kept")
    c_key = httpx.post(f"{base}/v1/events/batch", json={"events": []}, headers=lib.bearer(keys["cproj"]), timeout=30)
    check(c_key.status_code == 401 and c_key.json().get("error", {}).get("code") == unknown_key_reply(base).json().get("error", {}).get("code"),
          "the workspace's API key answers the uniform 401", status=c_key.status_code)
    record = lib.db("select scope, state from purge_tombstones where workspace_id = $1 and scope = 'workspace'", ws_c)
    check(len(record) == 1 and record[0][1] == "done", "one purge record survives for the workspace", record=[tuple(r) for r in record])
    lib.finish()

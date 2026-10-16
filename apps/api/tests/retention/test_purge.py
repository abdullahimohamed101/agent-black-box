"""Project and workspace purge (ADR-163): the destructive path against real Postgres, the real
retention role and a real artifact directory.

Nothing here reads a clock or sleeps: ordering is driven by hooks, concurrency by a held advisory
lock, interruption by raising from a hook (a process death) or setting the stop event.
"""

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.artifacts.store import ArtifactStoreError, LocalFsArtifactStore
from abb_api.retention import purge
from abb_api.retention import purge_inventory as inv
from tests.ingest_helpers import Tenant, make_tenant
from tests.retention.enforce_helpers import everything, make_store, scalar, truncate_all
from tests.retention.purge_helpers import (
    ProjectWorld,
    assert_scope_gone,
    files_of_scope,
    seed_destination,
    seed_project_world,
    seed_secret,
    seed_user,
    seed_workspace_extras,
    snapshot,
)


class Crash(BaseException):
    """A process death: not an Exception, so nothing in the purge may swallow or clean up."""


@pytest.fixture
def store(tmp_path: Path) -> LocalFsArtifactStore:
    return make_store(tmp_path)


def root_of(tmp_path: Path) -> Path:
    return tmp_path / "artifacts"


def project_request(
    ws: str = "acme", project: str = "p1", *, confirm: str | None = None, dry_run: bool = False
) -> purge.PurgeRequest:
    return purge.PurgeRequest(purge.PROJECT_SCOPE, ws, project, dry_run=dry_run, confirm=confirm)


def workspace_request(
    ws: str = "acme", *, confirm: str | None = None, dry_run: bool = False
) -> purge.PurgeRequest:
    return purge.PurgeRequest(purge.WORKSPACE_SCOPE, ws, None, dry_run=dry_run, confirm=confirm)


async def run(
    retention_engine: AsyncEngine,
    store: LocalFsArtifactStore | None,
    request: purge.PurgeRequest,
    **kw: Any,
) -> tuple[int, list[str]]:
    lines: list[str] = []
    code = await purge.execute(
        retention_engine,
        store,
        request,
        batch_size=2,  # several batches even for tiny scopes
        timeout_ms=30_000,
        out=lines.append,
        **kw,
    )
    return code, lines


async def two_projects(
    engine: AsyncEngine, store: LocalFsArtifactStore, *, runs: int = 2
) -> tuple[Tenant, Tenant, ProjectWorld, ProjectWorld, ProjectWorld]:
    """acme (p1 to purge, p2 sibling) and globex (q1), each with data in every backend."""
    key = await seed_secret(engine)
    acme = await make_tenant(engine, "acme", projects=("p1", "p2"))
    globex = await make_tenant(engine, "globex", projects=("q1",))
    destination = await seed_destination(engine, acme, key)
    p1 = await seed_project_world(engine, acme, "p1", store, runs=runs, destination=destination)
    p2 = await seed_project_world(engine, acme, "p2", store, runs=runs)
    q1 = await seed_project_world(engine, globex, "q1", store, runs=runs)
    return acme, globex, p1, p2, q1


async def tombstone_rows(engine: AsyncEngine) -> list[Any]:
    async with engine.connect() as conn:
        return list(
            (
                await conn.execute(
                    text(
                        "SELECT scope, state, steps_done, counts, attempts, failure_code, "
                        "user_candidates, tool_version, project_id, workspace_id "
                        "FROM purge_tombstones ORDER BY requested_at"
                    )
                )
            ).all()
        )


# ---------------------------------------------------------------- dry run and confirmation


async def test_a_dry_run_reports_exact_counts_per_backend_and_table_and_deletes_nothing(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore, tmp_path: Path
) -> None:
    await two_projects(engine, store)
    before = await everything(engine)
    files = files_of_scope(root_of(tmp_path))

    for request in (project_request(), project_request(confirm="p1", dry_run=True)):
        code, lines = await run(retention_engine, None, request)  # no store is even needed
        assert code == purge.EXIT_OK
        text_out = "\n".join(lines)
        # Hand-computed: 2 runs of 3 events and 2 spans; 2 artifacts each of 100(+n) and 200 bytes.
        assert "  events 6" in lines and "  runs 2" in lines and "  spans 4" in lines
        assert "  artifacts 4" in lines and "  run_search 2" in lines
        assert "  alert_rules 1" in lines and "  api_keys 1" in lines
        assert "  alert_rule_destinations 1" in lines and "  agents 1" in lines
        assert "artifact store: files=4 bytes=601" in lines
        assert "search index: entries=2" in lines
        assert any(line.startswith("rollups: rows=") for line in lines)
        assert "dry run: nothing was deleted" in lines
        assert "--confirm p1" in text_out
        assert "p2" not in text_out and "q1" not in text_out  # sibling data is not reported

    assert await everything(engine) == before  # byte-identical: tombstones, states, everything
    assert files_of_scope(root_of(tmp_path)) == files
    assert await tombstone_rows(engine) == []
    assert await scalar(engine, "SELECT count(*) FROM projects WHERE purge_state <> 'active'") == 0


async def test_a_wrong_or_missing_confirmation_refuses_and_changes_nothing(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore, tmp_path: Path
) -> None:
    await two_projects(engine, store)
    before = await everything(engine)
    for request in (
        project_request(confirm="p2"),  # a sibling's slug
        project_request(confirm="acme"),  # the workspace's slug for a project purge
        project_request(confirm="P1"),  # case matters
        project_request(confirm=""),
        workspace_request(confirm="p1"),
        workspace_request(confirm="globex"),
    ):
        code, lines = await run(retention_engine, store, request)
        assert code == purge.EXIT_USAGE, request
        assert "nothing was deleted" in lines[0]
    assert await everything(engine) == before
    assert await tombstone_rows(engine) == []


async def test_unknown_names_are_refused_the_same_way(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    await two_projects(engine, store)
    for request in (
        project_request("acme", "nope", confirm="nope"),
        project_request("nope", "p1", confirm="p1"),
        project_request("globex", "p1", confirm="p1"),  # a real project of ANOTHER workspace
        workspace_request("nope", confirm="nope"),
    ):
        code, lines = await run(retention_engine, store, request)
        assert code == purge.EXIT_REFUSED
        assert lines == [lines[0]] and "not_found" in lines[0]


# ---------------------------------------------------------------- project purge


async def test_a_project_purge_removes_the_project_everywhere_and_touches_nothing_else(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore, tmp_path: Path
) -> None:
    acme, _, p1, p2, q1 = await two_projects(engine, store)
    ws, pid = acme.context.workspace_id, acme.project_uuids["p1"]
    root = root_of(tmp_path)
    before = await snapshot(engine, "project", ws, pid, root)
    assert before.inside["alert_rules"] == 1  # KI-170: a project with a rule
    assert before.inside["artifacts"] == 4 and len(p1.artifact_keys) == 4
    destinations_before = await scalar(engine, "SELECT count(*) FROM alert_destinations")

    code, lines = await run(retention_engine, store, project_request(confirm="p1"))
    assert code == purge.EXIT_OK, lines
    assert any("done purge_id=" in line for line in lines)

    await assert_scope_gone(engine, before, "project", ws, pid, root, scope_files=p1.artifact_keys)
    # The sibling and the other workspace are whole, down to their files.
    for world in (p2, q1):
        for key in world.artifact_keys:
            assert (root / key).is_file()
    assert await scalar(engine, "SELECT count(*) FROM runs") == 4  # p2's two and q1's two
    # The workspace-level destination stays (it is not the project's), its link to the rule goes.
    assert await scalar(engine, "SELECT count(*) FROM alert_destinations") == destinations_before
    assert await scalar(engine, "SELECT count(*) FROM alert_rule_destinations") == 0
    assert await scalar(engine, "SELECT count(*) FROM server_secrets") == 1  # platform key kept
    # The workspace is not purging, and its other project reads normally.
    assert (
        await scalar(engine, "SELECT count(*) FROM workspaces WHERE purge_state <> 'active'") == 0
    )
    assert await scalar(engine, "SELECT count(*) FROM projects") == 2  # p2 and q1

    # The record: one completed tombstone, ids and counts only.
    (row,) = await tombstone_rows(engine)
    assert (row.scope, row.state, row.attempts, row.failure_code) == (
        "project",
        "completed",
        0,
        None,
    )
    assert row.project_id == pid and row.workspace_id == ws and row.user_candidates == []
    assert (
        row.counts["tables"]["alert_rules"] == 1 and row.counts["backends"]["artifact_files"] == 4
    )
    assert "completed" in row.steps_done and "measured" in row.steps_done
    assert "p1" not in str(tuple(row)) and "acme" not in str(tuple(row))  # no names
    # Per-run tombstones of the engine, and exactly one counts-only audit row in the workspace.
    assert await scalar(engine, "SELECT count(*) FROM retention_tombstones WHERE tier = 'run'") == 2
    async with engine.connect() as conn:
        audit = (
            await conn.execute(
                text(
                    "SELECT actor_kind, actor_id, action, resource_kind, resource_id, details "
                    "FROM audit_log WHERE action = 'retention.purge.project'"
                )
            )
        ).all()
    assert len(audit) == 1 and audit[0].actor_kind == "system"
    assert audit[0].resource_id == str(pid) and set(audit[0].details) == {
        "purge_id",
        "rows",
        "artifact_bytes",
    }


async def test_the_quota_counters_are_consistent_after_a_project_purge(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore, tmp_path: Path
) -> None:
    acme, _, _, p2, _ = await two_projects(engine, store)
    sibling_before = await scalar(
        engine,
        "SELECT bytes_stored FROM project_artifact_usage WHERE project_id = :p",
        p=acme.project_uuids["p2"],
    )
    assert sibling_before == sum(a for r in p2.runs for a in (r.artifact_bytes,))
    code, _ = await run(retention_engine, store, project_request(confirm="p1"))
    assert code == purge.EXIT_OK
    assert (
        await scalar(
            engine,
            "SELECT count(*) FROM project_artifact_usage WHERE project_id = :p",
            p=acme.project_uuids["p1"],
        )
        == 0
    )
    sibling_after = await scalar(
        engine,
        "SELECT bytes_stored FROM project_artifact_usage WHERE project_id = :p",
        p=acme.project_uuids["p2"],
    )
    stored = await scalar(
        engine,
        "SELECT COALESCE(sum(size_bytes), 0) FROM artifacts WHERE project_id = :p",
        p=acme.project_uuids["p2"],
    )
    assert sibling_after == sibling_before == stored  # the counter still equals the rows


async def test_artifacts_without_a_run_are_purged_with_the_project(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore, tmp_path: Path
) -> None:
    """KI-040's orphan: a project's artifact whose run never arrived has a file and a counter."""
    acme, _, p1, _, _ = await two_projects(engine, store, runs=1)
    ws, pid = acme.context.workspace_id, acme.project_uuids["p1"]
    from abb_api.artifacts.service import storage_key

    artifact = uuid.uuid4()
    body = b"orphan"
    import hashlib

    key = storage_key(ws, artifact, hashlib.sha256(body).digest())
    await store.put(key, body)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO artifacts (workspace_id, id, run_id, project_id, artifact_type, "
                "storage_uri, size_bytes) VALUES (:w, :a, gen_random_uuid(), :p, 'stdout', :u, 6)"
            ),
            {"w": ws, "a": artifact, "p": pid, "u": key},
        )
    root = root_of(tmp_path)
    before = await snapshot(engine, "project", ws, pid, root)
    assert (root / key).is_file()
    code, _ = await run(retention_engine, store, project_request(confirm="p1"))
    assert code == purge.EXIT_OK
    await assert_scope_gone(
        engine, before, "project", ws, pid, root, scope_files=[*p1.artifact_keys, key]
    )


# ------------------------------------------------------- workspace purge and the users rule


async def test_a_workspace_purge_removes_everything_of_the_workspace_and_nothing_else(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore, tmp_path: Path
) -> None:
    acme, globex, p1, p2, q1 = await two_projects(engine, store)
    key = await scalar(engine, "SELECT id FROM server_secrets LIMIT 1")
    await seed_workspace_extras(engine, acme, key)
    await seed_workspace_extras(engine, globex, key)
    ws = acme.context.workspace_id
    root = root_of(tmp_path)
    before = await snapshot(engine, "workspace", ws, None, root)
    assert before.inside["audit_log"] >= 3 and before.inside["alert_destinations"] == 2
    assert before.inside["invitations"] == 1 and before.inside["projects"] == 2

    code, lines = await run(retention_engine, store, workspace_request(confirm="acme"))
    assert code == purge.EXIT_OK, lines

    await assert_scope_gone(
        engine,
        before,
        "workspace",
        ws,
        None,
        root,
        scope_files=[*p1.artifact_keys, *p2.artifact_keys],
    )
    # globex is whole: its data, destinations (with their sealed config) and files.
    assert await scalar(engine, "SELECT count(*) FROM workspaces") == 1
    assert await scalar(engine, "SELECT count(*) FROM alert_destinations") == 1
    for key_ in q1.artifact_keys:
        assert (root / key_).is_file()
    assert await scalar(engine, "SELECT count(*) FROM server_secrets") == 1
    # The workspace's own audit log went with it: the tombstone is the record, with counts.
    (row,) = await tombstone_rows(engine)
    assert (row.scope, row.state, row.project_id) == ("workspace", "completed", None)
    assert row.workspace_id == ws and row.counts["tables"]["audit_log"] >= 3
    assert row.counts["tables"]["alert_destinations"] == 2 and row.counts["tables"]["projects"] == 2
    assert await scalar(engine, "SELECT count(*) FROM audit_log WHERE workspace_id = :w", w=ws) == 0


async def test_the_users_rule(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    """A person is removed ONLY when the purge leaves them with no membership anywhere and no live
    session; the platform keeps everyone else."""
    key = await seed_secret(engine)
    acme = await make_tenant(engine, "acme", projects=("p1",))
    globex = await make_tenant(engine, "globex", projects=("q1",))
    await seed_workspace_extras(engine, acme, key)
    both = await seed_user(engine, "both@x.test", member_of=[(acme, "OWNER"), (globex, "VIEWER")])
    sole = await seed_user(engine, "sole@x.test", member_of=[(acme, "ADMIN")], dead_session=True)
    live = await seed_user(
        engine, "live@x.test", member_of=[(acme, "DEVELOPER")], live_session=True
    )
    outsider = await seed_user(engine, "outsider@x.test")  # tied to no workspace at all
    other = await seed_user(engine, "other@x.test", member_of=[(globex, "OWNER")])

    code, lines = await run(retention_engine, store, workspace_request(confirm="acme"))
    assert code == purge.EXIT_OK, lines
    async with engine.connect() as conn:
        left = {r[0] for r in await conn.execute(text("SELECT id FROM users"))}
        memberships = (
            await conn.execute(text("SELECT count(*) FROM workspace_members"))
        ).scalar_one()
        sessions = (await conn.execute(text("SELECT user_id FROM sessions"))).all()
    assert left == {both, live, outsider, other}  # `sole` (and only they) is gone
    assert sole not in left
    assert memberships == 2  # both@globex and other@globex: acme's memberships are gone
    assert {r[0] for r in sessions} == {live}  # the live session is kept, the dead one went
    (row,) = await tombstone_rows(engine)
    assert row.counts["people"]["removed"] == 1 and row.user_candidates == []
    assert row.counts["people"]["candidates"] == 3  # both, sole, live (not the outsider)


# ---------------------------------------------------------------- resumability and crashes


async def final_state(
    engine: AsyncEngine, root: Path, scope_files: list[str], sibling_files: list[str]
) -> dict[str, Any]:
    tables = {
        name: await scalar(engine, f"SELECT count(*) FROM {name}")  # noqa: S608
        for name in (
            "runs", "events", "spans", "artifacts", "run_search", "alert_rules", "api_keys",
            "agents", "approvals", "projects", "workspaces", "workspace_members",
            "retention_run_progress", "project_artifact_usage", "audit_log",
            "analytics_runs_daily", "retention_tombstones", "outbox_jobs", "invitations",
            "alert_destinations", "rate_limit_counters", "users",
        )
    }  # fmt: skip
    rows = await tombstone_rows(engine)
    return {
        "tables": tables,
        "tombstones": [(r.scope, r.state, r.attempts or 0) for r in rows],
        "counts": [r.counts for r in rows],
        "scope_files": sum(1 for f in scope_files if (root / f).is_file()),
        "sibling_files": sum(1 for f in sibling_files if (root / f).is_file()),
    }


@pytest.mark.timeout(900)  # one full purge per hook call: slow by construction
async def test_a_crash_after_every_step_of_a_project_purge_is_completed_by_running_it_again(
    engine: AsyncEngine, retention_engine: AsyncEngine, tmp_path: Path
) -> None:
    """Kill the purge after each hook call in turn, then run the same command: the end state is
    the one an uninterrupted purge reaches, with no leftover row and exactly one tombstone."""
    reference: dict[str, Any] = {}
    calls: list[str] = []
    k = -1  # -1: the uninterrupted reference run
    while True:
        await truncate_all(engine)
        store = make_store(tmp_path / f"c{k}")
        acme, _, p1, p2, q1 = await two_projects(engine, store, runs=1)
        ws, pid = acme.context.workspace_id, acme.project_uuids["p1"]
        root = tmp_path / f"c{k}" / "artifacts"
        before = await snapshot(engine, "project", ws, pid, root)
        seen = 0

        async def hook(point: str, k: int = k) -> None:
            nonlocal seen
            if k < 0:
                calls.append(point)
            elif seen == k:
                raise Crash(point)
            seen += 1

        if k < 0:
            code, _ = await run(retention_engine, store, project_request(confirm="p1"), hook=hook)
            assert code == purge.EXIT_OK
        else:
            with pytest.raises(Crash):
                await run(retention_engine, store, project_request(confirm="p1"), hook=hook)
            # Whatever was reached, the project already reads as going and has a running record.
            if seen > 0:
                assert await scalar(
                    engine, "SELECT count(*) FROM purge_tombstones WHERE state = 'running'"
                ) in (0, 1)
            code, lines = await run(retention_engine, store, project_request(confirm="p1"))
            # A crash after the last hook is a crash after completion: nothing is left to purge.
            done = purge.EXIT_REFUSED if calls[k] == "purge:completed" else purge.EXIT_OK
            assert code == done, (k, calls[k], lines)
        await assert_scope_gone(
            engine, before, "project", ws, pid, root, scope_files=p1.artifact_keys
        )
        state = await final_state(
            engine, root, p1.artifact_keys, [*p2.artifact_keys, *q1.artifact_keys]
        )
        if k < 0:
            reference = state
            assert reference["tombstones"] == [("project", "completed", 0)]
            assert len(calls) > 25
            expected = {
                "purge:marked", "purge:measured", "purge:runs", "purge:artifacts",
                "purge:analytics", "purge:api_keys", "purge:alert_rules", "purge:completed",
                "begun", "search", "detail", "record",
            }  # fmt: skip
            assert expected <= set(calls)
        else:
            assert state == reference, f"crash after call {k} ({calls[k]})"
        k += 1
        if k >= len(calls):
            break


@pytest.mark.timeout(900)  # one full purge per hook call: slow by construction
async def test_a_crash_after_every_step_of_a_workspace_purge_is_completed_by_running_it_again(
    engine: AsyncEngine, retention_engine: AsyncEngine, tmp_path: Path
) -> None:
    calls: list[str] = []
    reference: dict[str, Any] = {}
    k = -1
    while True:
        await truncate_all(engine)
        store = make_store(tmp_path / f"w{k}")
        root = tmp_path / f"w{k}" / "artifacts"
        key = await seed_secret(engine)
        acme = await make_tenant(engine, "acme", projects=("p1",))
        globex = await make_tenant(engine, "globex", projects=("q1",))
        destination = await seed_destination(engine, acme, key)
        p1 = await seed_project_world(engine, acme, "p1", store, runs=1, destination=destination)
        q1 = await seed_project_world(engine, globex, "q1", store, runs=1)
        await seed_workspace_extras(engine, acme, key)
        await seed_user(engine, "sole@x.test", member_of=[(acme, "OWNER")])
        await seed_user(engine, "both@x.test", member_of=[(acme, "ADMIN"), (globex, "ADMIN")])
        ws = acme.context.workspace_id
        before = await snapshot(engine, "workspace", ws, None, root)
        seen = 0

        async def hook(point: str, k: int = k) -> None:
            nonlocal seen
            if k < 0:
                calls.append(point)
            elif seen == k:
                raise Crash(point)
            seen += 1

        if k < 0:
            code, _ = await run(
                retention_engine, store, workspace_request(confirm="acme"), hook=hook
            )
            assert code == purge.EXIT_OK
        else:
            with pytest.raises(Crash):
                await run(retention_engine, store, workspace_request(confirm="acme"), hook=hook)
            code, lines = await run(retention_engine, store, workspace_request(confirm="acme"))
            done = purge.EXIT_REFUSED if calls[k] == "purge:completed" else purge.EXIT_OK
            assert code == done, (k, calls[k], lines)
        await assert_scope_gone(
            engine, before, "workspace", ws, None, root, scope_files=p1.artifact_keys
        )
        state = await final_state(engine, root, p1.artifact_keys, q1.artifact_keys)
        # the people step ran exactly once whatever the interruption
        state["users"] = sorted(
            r[0] for r in await _rows(engine, "SELECT email FROM users ORDER BY email")
        )
        if k < 0:
            reference = state
            assert reference["tombstones"] == [("workspace", "completed", 0)]
            assert reference["users"] == ["both@x.test"]
            expected = {
                "purge:marked", "purge:candidates", "purge:projects", "purge:project_row",
                "purge:workspace_rows", "purge:people", "purge:completed",
            }  # fmt: skip
            assert expected <= set(calls)
        else:
            assert state == reference, f"crash after call {k} ({calls[k]})"
        k += 1
        if k >= len(calls):
            break


async def _rows(engine: AsyncEngine, sql: str) -> list[Any]:
    async with engine.connect() as conn:
        return [tuple(r) for r in await conn.execute(text(sql))]


async def test_stopping_the_process_ends_at_a_batch_boundary_and_a_rerun_completes(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore, tmp_path: Path
) -> None:
    """The kill switch of a purge is the stop signal (SIGTERM sets this event): no half
    transaction, a `running` record, and the next run finishes without a second record."""
    acme, _, p1, _, _ = await two_projects(engine, store)
    ws, pid = acme.context.workspace_id, acme.project_uuids["p1"]
    root = root_of(tmp_path)
    before = await snapshot(engine, "project", ws, pid, root)
    stop = asyncio.Event()

    async def after_first_batch(point: str) -> None:
        if point in ("artifact_batch", "detail_batch"):
            stop.set()

    code, lines = await run(
        retention_engine, store, project_request(confirm="p1"), stop=stop, hook=after_first_batch
    )
    assert code == purge.EXIT_STOPPED and "stopped at a batch boundary" in lines[0]
    (row,) = await tombstone_rows(engine)
    assert row.state == "running" and row.failure_code is None
    assert await scalar(engine, "SELECT count(*) FROM projects WHERE purge_state = 'purging'") == 1
    code, lines = await run(retention_engine, store, project_request(confirm="p1"))
    assert code == purge.EXIT_OK, lines
    await assert_scope_gone(engine, before, "project", ws, pid, root, scope_files=p1.artifact_keys)
    assert [(r.state, r.attempts) for r in await tombstone_rows(engine)] == [("completed", 0)]


class FailingStore(LocalFsArtifactStore):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.failing = True

    async def delete(self, key: str) -> None:
        if self.failing:
            raise ArtifactStoreError("disk on fire: /secret/path/with/content")
        await super().delete(key)


async def test_a_failing_step_reports_a_fixed_code_never_the_error_and_a_rerun_completes(
    engine: AsyncEngine,
    retention_engine: AsyncEngine,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    flaky = FailingStore(tmp_path / "artifacts")
    acme, _, p1, _, _ = await two_projects(engine, flaky)
    ws, pid = acme.context.workspace_id, acme.project_uuids["p1"]
    root = root_of(tmp_path)
    before = await snapshot(engine, "project", ws, pid, root)

    code, lines = await run(retention_engine, flaky, project_request(confirm="p1"))
    assert code == purge.EXIT_FAILED
    assert lines == ["purge-project: failed: artifact_store; run the same command again to resume"]
    assert "disk on fire" not in caplog.text and "/secret/path" not in caplog.text
    (row,) = await tombstone_rows(engine)
    assert (row.state, row.attempts, row.failure_code) == ("running", 1, "artifact_store")
    # Nothing is lost by failing: the project is hidden, its record is running, no root row left.
    assert await scalar(engine, "SELECT count(*) FROM projects WHERE id = :p", p=pid) == 1

    flaky.failing = False
    code, lines = await run(retention_engine, flaky, project_request(confirm="p1"))
    assert code == purge.EXIT_OK, lines
    await assert_scope_gone(engine, before, "project", ws, pid, root, scope_files=p1.artifact_keys)
    (row,) = await tombstone_rows(engine)
    assert (row.state, row.failure_code) == ("completed", None)


# ---------------------------------------------------------------- concurrency


async def test_a_purge_of_a_scope_someone_else_holds_is_refused_and_works_after(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore, tmp_path: Path
) -> None:
    acme, _, p1, _, _ = await two_projects(engine, store)
    ws = acme.context.workspace_id
    holder = await engine.connect()
    try:
        # What a running tick or another purge holds: the workspace's advisory lock (ADR-160).
        await holder.execute(
            text("SELECT pg_advisory_lock(hashtextextended(:k, 0))"), {"k": f"abb_retention:{ws}"}
        )
        before = await everything(engine)
        for request in (project_request(confirm="p1"), workspace_request(confirm="acme")):
            code, lines = await run(retention_engine, store, request)
            assert code == purge.EXIT_REFUSED and "locked" in lines[0]
        # Nothing at all changed: no mark, no record.
        assert await everything(engine) == before
        assert await tombstone_rows(engine) == []
    finally:
        await holder.close()  # closing the session releases its advisory lock
    code, lines = await run(retention_engine, store, project_request(confirm="p1"))
    assert code == purge.EXIT_OK, lines
    assert all(not (root_of(tmp_path) / k).exists() for k in p1.artifact_keys)


async def test_two_running_records_for_one_scope_cannot_exist(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    """The advisory lock is the first line; the database is the second."""
    acme, _, _, _, _ = await two_projects(engine, store, runs=1)
    ws, pid = acme.context.workspace_id, acme.project_uuids["p1"]
    insert = text(
        "INSERT INTO purge_tombstones (id, scope, workspace_id, project_id, scope_hash, "
        "tool_version) VALUES (gen_random_uuid(), 'project', :w, :p, 'h', '1')"
    )
    async with engine.begin() as conn:
        await conn.execute(insert, {"w": ws, "p": pid})
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(insert, {"w": ws, "p": pid})
    async with engine.begin() as conn:  # another project's record is fine
        await conn.execute(insert, {"w": ws, "p": acme.project_uuids["p2"]})


# ---------------------------------------------------------------- independence from the mode


async def test_a_purge_works_whatever_the_retention_mode_says(
    engine: AsyncEngine,
    retention_engine: AsyncEngine,
    store: LocalFsArtifactStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ABB_RETENTION_MODE", "off")
    await two_projects(engine, store, runs=1)
    code, lines = await run(retention_engine, store, project_request(confirm="p1"))
    assert code == purge.EXIT_OK, lines
    assert await scalar(engine, "SELECT count(*) FROM projects WHERE slug = 'p1'") == 0


def test_the_inventory_names_every_step_a_purge_runs() -> None:
    """The deletion lists are the classification's PROJECT/WORKSPACE tables, not a second list."""
    assert {t for t, _ in inv.PROJECT_DRAINS} <= set(inv.tables_of(inv.Kind.PROJECT))
    assert {t for t, _ in inv.WORKSPACE_DRAINS} <= (
        set(inv.tables_of(inv.Kind.PROJECT)) | set(inv.tables_of(inv.Kind.WORKSPACE))
    )

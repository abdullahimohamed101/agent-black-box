"""The retention tick in dry-run mode (ADR-159): correct reports, nothing deleted, bounded.

The tick runs as the real retention role. Times come from a fixed `NOW` and a scripted monotonic
clock, so no assertion depends on how fast the machine is.
"""

import logging
import uuid
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.db import tables as t
from abb_api.db.tables import metadata
from abb_api.limits.scopes import GLOBAL_WORKSPACE
from abb_api.retention import tick as tick_module
from abb_api.retention.measure import measure_tier
from abb_api.retention.runner import RetentionRunnerSettings
from abb_api.retention.tick import TICK_LOCK_KEY, TickResult, run_tick
from tests.ingest_helpers import make_tenant
from tests.retention.helpers import NOW, add_audit, days_ago, seed_run, seed_world


def settings(**overrides: Any) -> RetentionRunnerSettings:
    return RetentionRunnerSettings(
        retention_mode=overrides.pop("retention_mode", "dry_run"), **overrides
    )


async def tick(
    engine: AsyncEngine,
    cfg: RetentionRunnerSettings | None = None,
    *,
    monotonic: Callable[[], float] | None = None,
    keep: int = 50,
) -> TickResult:
    cfg = cfg or settings()
    extra: dict[str, Any] = {"monotonic": monotonic} if monotonic else {}
    return await run_tick(
        engine, cfg, budget_seconds=60, keep_reports=keep, now=lambda: NOW, **extra
    )


async def reports(engine: AsyncEngine) -> list[Any]:
    async with engine.connect() as conn:
        rows = await conn.execute(select(t.retention_reports).order_by(t.retention_reports.c.id))
        return list(rows)


def by_key(rows: list[Any]) -> dict[tuple[uuid.UUID | None, str], Any]:
    return {(r.workspace_id, r.tier): r for r in rows}


async def table_counts(engine: AsyncEngine) -> dict[str, int]:
    async with engine.connect() as conn:
        return {
            table.name: (
                await conn.execute(text(f'SELECT count(*) FROM "{table.name}"'))  # noqa: S608
            ).scalar_one()
            for table in metadata.sorted_tables
        }


async def add_operational_and_platform(engine: AsyncEngine, acme: Any) -> None:
    ws = acme.context.workspace_id
    async with engine.begin() as conn:
        jobs = (
            ("done", 8, 1),
            ("done", 6, 0),
            ("dead_letter", 31, 1),
            ("dead_letter", 29, 0),
            ("pending", 400, 0),
        )
        for status, age, _ in jobs:
            await conn.execute(
                insert(t.outbox_jobs).values(
                    id=uuid.uuid4(),
                    job_type="alert_prune",
                    workspace_id=ws,
                    dedupe_key=uuid.uuid4().hex,
                    status=status,
                    updated_at=days_ago(age),
                )
            )
        for scope, hours in (("alert_write", 2), ("alert_write", 0.5)):
            await conn.execute(
                insert(t.rate_limit_counters).values(
                    scope=scope,
                    workspace_id=ws,
                    key=uuid.uuid4().hex,
                    window_start=NOW,
                    count=1,
                    expires_at=NOW - timedelta(hours=hours),
                )
            )
        project = next(iter(acme.project_uuids.values()))
        for age in (40, 30):
            await conn.execute(
                insert(t.project_usage_daily).values(
                    workspace_id=ws, project_id=project, day=(NOW - timedelta(days=age)).date()
                )
            )
        # Install level: global counters, sessions and people who never joined a workspace.
        await conn.execute(
            insert(t.rate_limit_counters).values(
                scope="login_start",
                workspace_id=GLOBAL_WORKSPACE,
                key="k",
                window_start=NOW,
                count=1,
                expires_at=NOW - timedelta(hours=3),
            )
        )
        member = uuid.uuid4()
        stranger_old, stranger_young, invited = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        for uid, email, age in (
            (member, "member@x.test", 400),
            (stranger_old, "old@x.test", 40),
            (stranger_young, "young@x.test", 10),
            (invited, "invited@x.test", 90),
        ):
            await conn.execute(
                insert(t.users).values(id=uid, email=email, created_at=days_ago(age))
            )
        await conn.execute(
            insert(t.workspace_members).values(workspace_id=ws, user_id=member, role="VIEWER")
        )
        await conn.execute(
            insert(t.invitations).values(
                workspace_id=ws,
                id=uuid.uuid4(),
                email="invited@x.test",
                role="VIEWER",
                token_hash=b"i" * 32,
                expires_at=NOW + timedelta(days=3),
            )
        )
        for expired, idle, revoked in (
            (-8, 1, None),
            (-6, 1, None),
            (2, -9, None),
            (2, 2, -8),
            (2, 2, -2),
            (2, 2, None),
        ):
            await conn.execute(
                insert(t.sessions).values(
                    id=uuid.uuid4(),
                    user_id=member,
                    token_hash=uuid.uuid4().bytes * 2,
                    created_at=days_ago(30),
                    expires_at=NOW + timedelta(days=expired),
                    idle_expires_at=NOW + timedelta(days=idle),
                    revoked_at=None if revoked is None else NOW + timedelta(days=revoked),
                )
            )


@pytest.fixture
async def world(engine: AsyncEngine) -> tuple[Any, Any]:
    acme, globex = await make_tenant(engine, "acme"), await make_tenant(engine, "globex")
    await seed_world(engine, acme, globex)
    await add_operational_and_platform(engine, acme)
    return acme, globex


async def test_a_dry_run_tick_writes_one_correct_report_per_workspace_and_tier(
    world: tuple[Any, Any], engine: AsyncEngine, retention_engine: AsyncEngine
) -> None:
    acme, globex = world
    result = await tick(retention_engine)
    assert (result.mode, result.workspaces, result.errors, result.truncated) == ("dry_run", 2, 0, 0)
    assert not (result.refused or result.locked_out or result.budget_exhausted)
    rows = await reports(engine)
    assert result.reports == len(rows) == 2 * 5 + 1  # five per workspace, one install-level
    assert {r.tick_id for r in rows} == {result.tick_id}
    assert {r.mode for r in rows} == {"dry_run"} and not any(r.truncated for r in rows)
    got = by_key(rows)
    a = acme.context.workspace_id

    def figures(r: Any) -> tuple[int, int, int, int]:
        return (r.runs, r.events, r.artifact_bytes, r.row_count)

    # The same hand-computed figures as the preview (tests/retention/test_preview.py).
    assert figures(got[a, "artifacts"]) == (3, 0, 1067, 4)
    assert figures(got[a, "trace"]) == (2, 7, 0, 7)
    assert figures(got[a, "run"]) == (1, 0, 0, 1)
    assert figures(got[a, "audit"]) == (0, 0, 0, 2)
    # Operational, hand-counted: done job 8 days old, dead letter 31 days old, one counter expired
    # for two hours (the half-hour one is inside its grace), one usage row 40 days old.
    assert figures(got[a, "operational"]) == (0, 0, 0, 4)
    g = globex.context.workspace_id
    assert figures(got[g, "artifacts"]) == (1, 0, 9999, 1)
    assert figures(got[g, "trace"]) == (1, 9, 0, 10)  # 9 events; 9 spans + 1 job
    assert figures(got[g, "audit"]) == (0, 0, 0, 1)
    # Install level: expired sessions 8 days (absolute), 9 days (idle), revoked 8 days = 3; the
    # one stranger 40 days old (not the 10-day one, nor the invited, nor the member); one global
    # counter.
    assert figures(got[None, "platform"]) == (0, 0, 0, 5)
    assert got[None, "platform"].workspace_id is None
    assert got[a, "trace"].cutoff_at == NOW - timedelta(days=90)
    assert got[a, "operational"].cutoff_at is None
    assert all(r.finished_at >= r.started_at for r in rows)


async def test_a_dry_run_tick_deletes_nothing_anywhere(
    world: tuple[Any, Any], engine: AsyncEngine, retention_engine: AsyncEngine
) -> None:
    before = await table_counts(engine)
    assert before["events"] > 0 and before["artifacts"] > 0 and before["sessions"] > 0
    await tick(retention_engine)
    await tick(retention_engine)  # and again: still nothing
    after = await table_counts(engine)
    changed = {name for name in before if before[name] != after[name]}
    assert changed == {"retention_reports"}
    assert after["retention_tombstones"] == 0  # unused until enforcement


async def test_off_does_nothing_and_enforce_without_an_artifact_store_is_refused(
    world: tuple[Any, Any],
    engine: AsyncEngine,
    retention_engine: AsyncEngine,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = await table_counts(engine)
    off = await tick(retention_engine, settings(retention_mode="off"))
    assert (off.mode, off.reports, off.refused) == ("off", 0, False)
    monkeypatch.setattr(tick_module, "_enforce_refusal_logged", False)
    with caplog.at_level(logging.ERROR, logger="abb_api.retention.tick"):
        first = await tick(retention_engine, settings(retention_mode="enforce"))
        second = await tick(retention_engine, settings(retention_mode="enforce"))
    assert first.refused and second.refused and first.reports == second.reports == 0
    assert [r.message for r in caplog.records].count(
        "ABB_RETENTION_MODE=enforce needs the artifact store; nothing was deleted"
    ) == 1  # logged once per process
    assert await table_counts(engine) == before


async def test_a_tier_kept_forever_gets_no_report(
    engine: AsyncEngine, retention_engine: AsyncEngine
) -> None:
    acme = await make_tenant(engine, "acme")
    async with engine.begin() as conn:
        await conn.execute(
            insert(t.retention_policies).values(
                workspace_id=acme.context.workspace_id,
                artifacts_forever=True,
                trace_forever=True,
                run_forever=True,
                audit_days=30,
                updated_by="t",
            )
        )
    await tick(retention_engine)
    tiers = sorted(r.tier for r in await reports(engine) if r.workspace_id)
    assert tiers == ["audit", "operational"]


async def test_the_workspace_policy_not_the_default_decides_what_is_measured(
    engine: AsyncEngine, retention_engine: AsyncEngine
) -> None:
    acme = await make_tenant(engine, "acme")
    await seed_run(engine, acme, ended=days_ago(20), events=2, spans=0, jobs=0)
    await seed_run(engine, acme, ended=days_ago(60), events=3, spans=0, jobs=0)
    async with engine.begin() as conn:
        await conn.execute(
            insert(t.retention_policies).values(
                workspace_id=acme.context.workspace_id, trace_days=30, updated_by="t"
            )
        )
    await tick(retention_engine)
    got = by_key(await reports(engine))[acme.context.workspace_id, "trace"]
    assert (got.runs, got.events, got.cutoff_at) == (1, 3, NOW - timedelta(days=30))


async def test_a_saved_policy_that_the_defaults_make_invalid_is_skipped_and_counted(
    engine: AsyncEngine, retention_engine: AsyncEngine
) -> None:
    acme, globex = await make_tenant(engine, "acme"), await make_tenant(engine, "globex")
    async with engine.begin() as conn:
        # Trace detail forever under a finite default run record (365): the order is broken.
        await conn.execute(
            insert(t.retention_policies).values(
                workspace_id=acme.context.workspace_id, trace_forever=True, updated_by="t"
            )
        )
    result = await tick(retention_engine)
    assert result.errors == 1
    workspaces = {r.workspace_id for r in await reports(engine)}
    assert acme.context.workspace_id not in workspaces
    assert globex.context.workspace_id in workspaces


async def test_per_tick_bounds_mark_reports_truncated(
    engine: AsyncEngine, retention_engine: AsyncEngine
) -> None:
    acme = await make_tenant(engine, "acme")
    for age in (400, 410, 420, 430, 440):
        await seed_run(engine, acme, ended=days_ago(age), events=1, spans=0, jobs=0)
    for age in (400, 401, 402):
        await add_audit(engine, acme.context.workspace_id, age)
    cfg = settings(retention_max_runs_per_tick=2, retention_max_rows_per_tick=2)
    result = await tick(retention_engine, cfg)
    got = by_key(await reports(engine))
    a = acme.context.workspace_id
    for tier in ("artifacts", "trace", "run"):
        assert got[a, tier].truncated is True, tier
    assert (got[a, "trace"].runs, got[a, "trace"].events) == (2, 2)  # the oldest two runs
    assert got[a, "run"].runs == 2
    assert (got[a, "audit"].row_count, got[a, "audit"].truncated) == (2, True)
    assert result.truncated >= 4
    # With room to spare nothing is truncated.
    await tick(retention_engine)
    latest = by_key(await reports(engine))
    assert latest[a, "trace"].truncated is False and latest[a, "trace"].runs == 5


def scripted(*values: float) -> Callable[[], float]:
    """A monotonic clock that returns the given readings in order, then keeps the last."""
    it: Iterator[float] = iter(values)
    last = values[-1]

    def clock() -> float:
        nonlocal last
        last = next(it, last)
        return last

    return clock


async def test_the_time_budget_stops_the_tick_and_the_next_one_starts_with_the_rest(
    engine: AsyncEngine, retention_engine: AsyncEngine
) -> None:
    tenants = [await make_tenant(engine, f"ws{n}") for n in range(3)]
    # Readings: the deadline is taken at 0 (budget 60); the first workspace starts at 10; the
    # second would start at 61, past the deadline.
    first = await tick(retention_engine, monotonic=scripted(0, 10, 61))
    assert (first.workspaces, first.budget_exhausted) == (1, True)
    done_first = {r.workspace_id for r in await reports(engine)}
    assert len(done_first) == 1  # and no install-level report: the tick ran out of time
    second = await tick(retention_engine, monotonic=scripted(0, 10, 11, 61))
    assert (second.workspaces, second.budget_exhausted) == (2, True)
    done_second = {r.workspace_id for r in await reports(engine)} - done_first
    assert len(done_second) == 2  # the workspaces not yet reported on went first
    assert {*done_first, *done_second} == {tn.context.workspace_id for tn in tenants}


async def test_two_ticks_never_run_at_once(
    engine: AsyncEngine, retention_engine: AsyncEngine
) -> None:
    await make_tenant(engine, "acme")
    async with engine.connect() as holder:
        assert (
            await holder.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": TICK_LOCK_KEY})
        ).scalar_one()
        result = await tick(retention_engine)
        assert result.locked_out and result.reports == 0
        await holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": TICK_LOCK_KEY})
    assert (await tick(retention_engine)).reports > 0  # the lock was released, not leaked


async def test_old_reports_are_pruned_per_workspace_and_tier(
    engine: AsyncEngine, retention_engine: AsyncEngine
) -> None:
    acme = await make_tenant(engine, "acme")
    for _ in range(4):
        await tick(retention_engine, keep=2)
    rows = await reports(engine)
    per: dict[Any, list[Any]] = {}
    for r in rows:
        per.setdefault((r.workspace_id, r.tier), []).append(r.tick_id)
    assert {len(v) for v in per.values()} == {2}
    assert acme.context.workspace_id in {w for w, _ in per}


async def test_a_failing_tier_is_logged_and_does_not_stop_the_others(
    engine: AsyncEngine,
    retention_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    acme = await make_tenant(engine, "acme")
    real = measure_tier

    async def flaky(conn: Any, workspace_id: Any, tier: Any, *args: Any) -> Any:
        if tier.value == "trace":
            raise RuntimeError("boom with content: secret-prompt")
        return await real(conn, workspace_id, tier, *args)

    monkeypatch.setattr(tick_module, "measure_tier", flaky)
    with caplog.at_level(logging.WARNING, logger="abb_api.retention.tick"):
        result = await tick(retention_engine)
    assert result.errors == 1
    tiers = {r.tier for r in await reports(engine) if r.workspace_id == acme.context.workspace_id}
    assert tiers == {"artifacts", "run", "audit", "operational"}
    assert "secret-prompt" not in caplog.text  # only the error type is logged

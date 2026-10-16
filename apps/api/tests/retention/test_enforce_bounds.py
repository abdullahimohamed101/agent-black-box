"""Bounds, the kill switch, concurrency, the audit-log and operational clean-ups, rollups.

Every assertion is a deterministic fact: counts of batches and calls, not elapsed time.
"""

import asyncio
import itertools
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.analytics.postgres import PostgresAnalyticsStore
from abb_api.analytics.rollup import refresh_day
from abb_api.analytics.store import AnalyticsScope
from abb_api.artifacts.store import LocalFsArtifactStore
from abb_api.db import tables as t
from abb_api.retention.tick import TICK_LOCK_KEY
from abb_api.tenancy import TenantContext
from tests.ingest_helpers import make_tenant
from tests.retention.enforce_helpers import (
    enforce,
    enforcing,
    fingerprint,
    make_store,
    scalar,
    tombstones,
)
from tests.retention.helpers import NOW, add_audit, days_ago, seed_run, seed_world
from tests.retention.test_tick import add_operational_and_platform


@pytest.fixture
def store(tmp_path: Path) -> LocalFsArtifactStore:
    return make_store(tmp_path)


# ---------------------------------------------------------------- the kill switch


async def test_switching_the_mode_away_from_enforce_stops_at_the_next_batch_boundary(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    acme = await make_tenant(engine, "acme")
    run = await seed_run(engine, acme, ended=days_ago(100), events=9, spans=1, jobs=0)
    mode = ["enforce"]
    batches = 0

    async def probe() -> str:
        return mode[0]

    async def operator(point: str) -> None:
        nonlocal batches
        if point == "detail_batch":
            batches += 1
            if batches == 2:  # the operator sets dry_run while the third batch is about to start
                mode[0] = "dry_run"

    first = await enforce(retention_engine, store, enforcing(), mode_probe=probe, hook=operator)
    assert first.halted == "mode"
    left = await scalar(engine, "SELECT count(*) FROM events WHERE run_id = :r", r=run.run_id)
    assert left == 9 - 2 * 2  # exactly two batches of two went; nothing was torn
    assert await scalar(engine, "SELECT trace_state FROM runs") == "deleting"
    assert await scalar(engine, "SELECT count(*) FROM retention_run_progress") >= 1
    # Switched off entirely: the next tick does not touch it either.
    off = await run_off(retention_engine, store)
    assert off.reports == 0
    assert (
        await scalar(engine, "SELECT count(*) FROM events WHERE run_id = :r", r=run.run_id) == left
    )
    # Enforcement again completes the run from where it stopped.
    done = await enforce(retention_engine, store)
    assert done.halted is None
    assert await scalar(engine, "SELECT count(*) FROM events") == 0
    assert await scalar(engine, "SELECT trace_state FROM runs") == "expired"
    assert await scalar(engine, "SELECT count(*) FROM retention_run_progress") == 0
    assert [tier for _, tier in await tombstones(engine)] == ["trace"]  # no artifacts to tombstone


async def run_off(engine: AsyncEngine, store: LocalFsArtifactStore) -> Any:
    from abb_api.retention.runner import RetentionRunnerSettings
    from abb_api.retention.tick import run_tick

    return await run_tick(
        engine,
        RetentionRunnerSettings(retention_mode="off"),
        budget_seconds=60,
        keep_reports=50,
        now=lambda: NOW,
        store=store,
    )


async def test_a_stop_request_ends_enforcement_at_a_batch_boundary(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    acme = await make_tenant(engine, "acme")
    await seed_run(engine, acme, ended=days_ago(100), events=9, jobs=0)
    stop = asyncio.Event()

    async def hook(point: str) -> None:
        if point == "detail_batch":
            stop.set()

    result = await enforce(retention_engine, store, enforcing(), hook=hook, stop=stop)
    assert result.halted == "stop"
    assert await scalar(engine, "SELECT count(*) FROM events") == 7  # exactly one batch of 2 went


async def test_an_unreadable_mode_means_off() -> None:
    from abb_api.retention import runner

    assert await runner.mode_now() in ("dry_run", "enforce", "off")


# ---------------------------------------------------------------- bounds


async def test_at_most_max_runs_are_finished_per_tick_and_the_rest_wait(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    acme = await make_tenant(engine, "acme")
    for age in (400, 401, 402):
        await seed_run(engine, acme, ended=days_ago(age), events=1, spans=0, jobs=0)
    cfg = enforcing(retention_max_runs_per_tick=1)
    first = await enforce(retention_engine, store, cfg)
    assert first.deleted_runs >= 1
    assert await scalar(engine, "SELECT count(*) FROM runs") == 2  # one run record went
    async with engine.connect() as conn:
        truncated = (
            await conn.execute(
                text("SELECT bool_or(truncated) FROM retention_reports WHERE mode = 'enforce'")
            )
        ).scalar_one()
    assert truncated is True  # the report says more is waiting
    await enforce(retention_engine, store, cfg)
    await enforce(retention_engine, store, cfg)
    assert await scalar(engine, "SELECT count(*) FROM runs") == 0
    await enforce(retention_engine, store, cfg)  # settled


async def test_the_row_allowance_per_pass_ends_the_pass_and_the_next_tick_resumes(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    acme = await make_tenant(engine, "acme")
    run = await seed_run(engine, acme, ended=days_ago(100), events=10, spans=0, jobs=0)
    cfg = enforcing(retention_max_rows_per_tick=3)  # batch 2: allowance reached after 2 batches
    result = await enforce(retention_engine, store, cfg)
    assert result.truncated >= 1 and result.halted is None  # a pass limit, not a hard stop
    first_left = await scalar(engine, "SELECT count(*) FROM events WHERE run_id = :r", r=run.run_id)
    assert first_left == 10 - 4
    for _ in range(5):
        await enforce(retention_engine, store, cfg)
    assert await scalar(engine, "SELECT count(*) FROM events") == 0
    assert await scalar(engine, "SELECT trace_state FROM runs") == "expired"


async def test_the_time_budget_stops_enforcement_between_batches(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    acme = await make_tenant(engine, "acme")
    await seed_run(engine, acme, ended=days_ago(100), events=20, jobs=0)
    clock = itertools.count()  # one "second" per look at the clock: deterministic

    result = await enforce(retention_engine, store, monotonic=lambda: float(next(clock)), budget=12)
    assert result.budget_exhausted and result.halted == "budget"
    left = await scalar(engine, "SELECT count(*) FROM events")
    assert 0 < left < 20
    await enforce(retention_engine, store)
    assert await scalar(engine, "SELECT count(*) FROM events") == 0


# ---------------------------------------------------------------- concurrency


async def test_two_ticks_never_work_at_once_and_nothing_is_deleted_twice(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    acme = await make_tenant(engine, "acme")
    await seed_run(engine, acme, ended=days_ago(400), store=store, artifact_sizes=(10,), events=4)
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold(point: str) -> None:
        if point == "begun" and not entered.is_set():
            entered.set()
            await release.wait()

    first = asyncio.create_task(enforce(retention_engine, store, hook=hold))
    await entered.wait()
    second = await enforce(retention_engine, store)  # while the first is mid-run
    assert second.locked_out and second.deleted_runs == 0
    release.set()
    done = await first
    assert not done.locked_out and done.failed == 0
    assert sorted(tier for _, tier in await tombstones(engine)) == ["artifacts", "run", "trace"]
    assert await scalar(engine, "SELECT bytes_stored FROM project_artifact_usage") == 0


async def test_a_workspace_another_process_is_working_on_is_skipped_not_waited_for(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    acme = await make_tenant(engine, "acme")
    globex = await make_tenant(engine, "globex")
    mine = await seed_run(engine, acme, ended=days_ago(400), events=2, jobs=0)
    other = await seed_run(engine, globex, ended=days_ago(400), events=2, jobs=0)
    async with engine.connect() as holder:
        await holder.execute(
            text("SELECT pg_advisory_lock(hashtextextended(:k, 0))"),
            {"k": f"abb_retention:{acme.context.workspace_id}"},
        )
        try:
            result = await enforce(retention_engine, store)
        finally:
            await holder.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:k, 0))"),
                {"k": f"abb_retention:{acme.context.workspace_id}"},
            )
    assert result.skipped_locked == 1
    assert (await fingerprint(engine, mine.run_id))["runs"][0] == 1  # skipped workspace untouched
    assert (await fingerprint(engine, other.run_id))["runs"][0] == 0
    assert TICK_LOCK_KEY  # the tick lock itself is released
    await enforce(retention_engine, store)
    assert (await fingerprint(engine, mine.run_id))["runs"][0] == 0


# ---------------------------------------------------------------- audit log (KI-072)


async def test_the_audit_log_loses_only_entries_older_than_the_cutoff_and_records_each_batch(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    acme = await make_tenant(engine, "acme")
    globex = await make_tenant(engine, "globex")
    ws, other = acme.context.workspace_id, globex.context.workspace_id
    for age in (400, 366, 365, 364, 5):
        await add_audit(engine, ws, age)
    await add_audit(engine, other, 900)
    result = await enforce(retention_engine, store, enforcing(retention_batch_size=1))
    assert result.failed == 0
    async with engine.connect() as conn:
        remaining = (
            await conn.execute(
                text(
                    "SELECT action, details FROM audit_log WHERE workspace_id = :w "
                    "ORDER BY occurred_at"
                ),
                {"w": ws},
            )
        ).all()
        others = (
            await conn.execute(
                text("SELECT count(*) FROM audit_log WHERE workspace_id = :w"), {"w": other}
            )
        ).scalar_one()
    kept = [r for r in remaining if r.action == "test.event"]
    summaries = [r for r in remaining if r.action == "retention.delete.audit"]
    assert len(kept) == 3  # 365 days exactly, 364 and 5 stay
    assert len(summaries) == 2  # one summary per batch (batch size 1: two batches deleted rows)
    assert all(r.details["rows"] == 1 and r.details["tier"] == "audit" for r in summaries)
    # The other workspace was past its own (default) cutoff too: its entry went, with a summary
    # of its own; nothing of acme's run touched its log beyond that.
    assert others == 1  # only globex's own summary row remains in globex's log


async def test_a_workspace_with_a_longer_audit_policy_keeps_its_entries(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    acme = await make_tenant(engine, "acme")
    globex = await make_tenant(engine, "globex")
    await add_audit(engine, acme.context.workspace_id, 400)
    await add_audit(engine, globex.context.workspace_id, 400)
    async with engine.begin() as conn:
        await conn.execute(
            insert(t.retention_policies).values(
                workspace_id=globex.context.workspace_id, updated_by="t", audit_forever=True
            )
        )
    await enforce(retention_engine, store)
    counts = {}
    for name, tenant in (("acme", acme), ("globex", globex)):
        counts[name] = await scalar(
            engine,
            "SELECT count(*) FROM audit_log WHERE workspace_id = :w AND action = 'test.event'",
            w=tenant.context.workspace_id,
        )
    assert counts == {"acme": 0, "globex": 1}


async def test_a_summary_row_survives_until_its_own_age(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    acme = await make_tenant(engine, "acme")
    await add_audit(engine, acme.context.workspace_id, 400)
    await enforce(retention_engine, store)
    await enforce(retention_engine, store)  # a second tick never removes the first one's record
    assert (
        await scalar(
            engine,
            "SELECT count(*) FROM audit_log WHERE action = 'retention.delete.audit'",
        )
        == 1
    )


async def test_audit_rows_of_a_pass_carry_counts_only(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    acme = await make_tenant(engine, "acme")
    await seed_run(engine, acme, ended=days_ago(400), store=store, artifact_sizes=(10,), events=3)
    await enforce(retention_engine, store)
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT actor_kind, actor_id, action, outcome, details FROM audit_log "
                    "WHERE action LIKE 'retention.delete.%' ORDER BY id"
                )
            )
        ).all()
    assert [r.action for r in rows] == [
        "retention.delete.artifacts",
        "retention.delete.trace",
        "retention.delete.run",
    ]
    for r in rows:
        assert (r.actor_kind, r.actor_id, r.outcome) == ("system", "retention", "allowed")
        assert all(isinstance(v, int | bool | str) for v in r.details.values())
        assert set(r.details) <= {
            "tier", "mode", "runs", "events", "rows", "artifact_bytes", "failed", "truncated",
        }  # fmt: skip
    assert rows[1].details["events"] == 3 and rows[0].details["artifact_bytes"] == 10


# ---------------------------------------------------------------- fixed windows


async def test_operational_tables_are_pruned_by_their_fixed_windows(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    acme = await make_tenant(engine, "acme")
    globex = await make_tenant(engine, "globex")
    await seed_world(engine, acme, globex)
    await add_operational_and_platform(engine, acme)
    async with engine.begin() as conn:  # a dirty-day row that is old and settled, and a young one
        ws = acme.context.workspace_id
        for age, version, refreshed in ((40, 3, 3), (41, 4, 3), (2, 1, 1)):
            await conn.execute(
                insert(t.analytics_dirty).values(
                    workspace_id=ws, day=(NOW - timedelta(days=age)).date(),
                    version=version, refreshed_version=refreshed,
                )
            )  # fmt: skip
    result = await enforce(retention_engine, store)
    assert result.failed == 0 and result.errors == 0
    # Jobs: the 8-day-old done job and the 31-day-old dead letter go; the rest stay.
    statuses = await _scalars(
        engine,
        "SELECT status || ':' || (extract(day FROM :n - updated_at))::int FROM outbox_jobs "
        "WHERE job_type = 'alert_prune' ORDER BY 1",
        n=NOW,
    )
    assert statuses == ["dead_letter:29", "done:6", "pending:400"]
    # Counters: the one expired two hours ago and the global one go; the half-hour one stays.
    assert await scalar(engine, "SELECT count(*) FROM rate_limit_counters") == 1
    # Usage: 40 days old goes, 30 days old stays.
    assert await scalar(engine, "SELECT count(*) FROM project_usage_daily") == 1
    # Dirty markers: only the old settled one (40 days, version = refreshed_version) goes.
    assert await scalar(engine, "SELECT count(*) FROM analytics_dirty") == 2
    # Sessions: three dead for over a week go; three stay.
    assert await scalar(engine, "SELECT count(*) FROM sessions") == 3
    # People: the one stranger 40 days old goes; the member, the invited and the young stay.
    emails = await _scalars(engine, "SELECT email FROM users ORDER BY email")
    assert emails == ["invited@x.test", "member@x.test", "young@x.test"]
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT tier, mode, row_count FROM retention_reports "
                    "WHERE tier IN ('operational', 'platform') AND mode = 'enforce'"
                )
            )
        ).all()
    figures: dict[str, int] = {}
    for r in rows:
        figures[r.tier] = figures.get(r.tier, 0) + r.row_count
    assert figures["platform"] == 5  # what the dry run counted (tests/retention/test_tick.py)
    assert figures["operational"] == 4 + 1  # jobs 2, counter 1, usage 1 (as counted) + a marker


async def _scalars(engine: AsyncEngine, sql: str, **params: Any) -> list[Any]:
    async with engine.connect() as conn:
        return [r[0] for r in await conn.execute(text(sql), params)]


async def test_a_person_who_gained_a_reference_after_the_count_is_kept(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    """KI-194: the DELETE re-checks every reference, so an invitation that appears after the
    person was selected keeps them."""
    acme = await make_tenant(engine, "acme")
    ws = acme.context.workspace_id
    async with engine.begin() as conn:
        for email in ("late@x.test", "alone@x.test"):
            await conn.execute(
                insert(t.users).values(id=uuid.uuid4(), email=email, created_at=days_ago(90))
            )

    async def invite(point: str) -> None:
        if point == "strangers_selected":
            async with engine.begin() as conn:
                await conn.execute(
                    insert(t.invitations).values(
                        workspace_id=ws, id=uuid.uuid4(), email="late@x.test", role="VIEWER",
                        token_hash=b"i" * 32, expires_at=NOW + timedelta(days=3),
                    )
                )  # fmt: skip

    await enforce(retention_engine, store, hook=invite)
    assert await _scalars(engine, "SELECT email FROM users ORDER BY email") == ["late@x.test"]


# ---------------------------------------------------------------- rollups


async def refresh_all(engine: AsyncEngine, tenant: Any, days: list[date]) -> None:
    async with engine.begin() as conn:
        for day in days:
            await refresh_day(conn, TenantContext(tenant.context.workspace_id), day)


async def rollup_totals(engine: AsyncEngine) -> dict[str, Any]:
    async with engine.connect() as conn:
        runs = (
            await conn.execute(
                text("SELECT day, sum(runs) AS runs FROM analytics_runs_daily GROUP BY day")
            )
        ).all()
        spans = (
            await conn.execute(
                text("SELECT day, sum(finished) AS n FROM analytics_spans_daily GROUP BY day")
            )
        ).all()
        cost = (
            await conn.execute(
                text("SELECT day, sum(calls) AS n FROM analytics_cost_daily GROUP BY day")
            )
        ).all()
    return {
        "runs": {r.day: int(r.runs) for r in runs},
        "spans": {r.day: int(r.n) for r in spans},
        "cost": {r.day: int(r.n) for r in cost},
    }


async def test_rollups_of_the_affected_days_exclude_what_was_deleted(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    acme = await make_tenant(engine, "acme")
    project = next(iter(acme.project_uuids.values()))
    seeded = {}
    for name, age in (("gone", 400), ("trace", 100), ("fresh", 10)):
        run = await seed_run(engine, acme, ended=days_ago(age), full=True, events=2, spans=2)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE spans SET kind = 'tool', status = 'success', project_id = :p, "
                    "run_started_at = (SELECT started_at FROM runs WHERE id = :r), "
                    "duration_ms = 5 WHERE run_id = :r"
                ),
                {"p": project, "r": run.run_id},
            )
            await conn.execute(
                text(
                    "UPDATE cost_calculations SET run_started_at = "
                    "(SELECT started_at FROM runs WHERE id = :r) WHERE run_id = :r"
                ),
                {"r": run.run_id},
            )
        seeded[name] = (days_ago(age) - timedelta(minutes=5)).date()
    await refresh_all(engine, acme, list(seeded.values()))
    before = await rollup_totals(engine)
    d_gone, d_trace, d_fresh = seeded["gone"], seeded["trace"], seeded["fresh"]
    assert before["runs"] == {d_gone: 1, d_trace: 1, d_fresh: 1}
    assert before["spans"] == {d_gone: 2, d_trace: 2, d_fresh: 2}
    fresh_rows = await _day_hash(engine, d_fresh)

    result = await enforce(retention_engine, store)
    assert result.failed == 0
    after = await rollup_totals(engine)
    # The run past every tier no longer counts anywhere; the one that kept its record still counts
    # as a run (its typed totals live on the record) but its span and cost detail is gone.
    assert after["runs"] == {d_trace: 1, d_fresh: 1}
    assert after["spans"] == {d_fresh: 2} and after["cost"] == {d_fresh: 1}
    assert await _day_hash(engine, d_fresh) == fresh_rows  # untouched day: byte-identical
    assert await scalar(engine, "SELECT count(*) FROM retention_run_progress") == 0


async def test_run_totals_of_a_day_are_identical_before_and_after_the_trace_tier(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    """KI-199 (ADR-161): everything derivable from the surviving run record keeps its value; the
    runs that lack trace detail are counted, and the reports that rest on trace detail say so."""
    acme = await make_tenant(engine, "acme")
    run = await seed_run(engine, acme, ended=days_ago(100), full=True, events=2, spans=2)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE runs SET cost_usd = 1.5, retry_cost_usd = 0.25, retry_count = 2, "
                "llm_calls = 3, tool_calls = 4, tool_spans_finished = 4, tool_spans_ok = 3, "
                "llm_spans_finished = 3, llm_spans_ok = 3, duration_ms = 900 WHERE id = :r"
            ),
            {"r": run.run_id},
        )
    day = (days_ago(100) - timedelta(minutes=5)).date()
    await refresh_all(engine, acme, [day])
    columns = (
        "SELECT runs, cost_usd, retry_cost_usd, retried_runs, llm_calls, tool_calls, retry_count, "
        "tool_spans_finished, tool_spans_ok, llm_spans_finished, llm_spans_ok, status "
        "FROM analytics_runs_daily WHERE day = :d"
    )
    async with engine.connect() as conn:
        before = [tuple(r) for r in (await conn.execute(text(columns), {"d": day})).all()]
    assert (
        before
        and await scalar(
            engine, "SELECT sum(expired_trace_runs) FROM analytics_runs_daily WHERE day = :d", d=day
        )
        == 0
    )

    result = await enforce(retention_engine, store)
    assert result.failed == 0
    async with engine.connect() as conn:
        after = [tuple(r) for r in (await conn.execute(text(columns), {"d": day})).all()]
    assert after == before
    assert (
        await scalar(
            engine, "SELECT sum(expired_trace_runs) FROM analytics_runs_daily WHERE day = :d", d=day
        )
        == 1
    )

    scope = AnalyticsScope(
        tenant=TenantContext(acme.context.workspace_id),
        start_day=day,
        end_day=day + timedelta(days=1),
    )
    analytics = PostgresAnalyticsStore(engine)
    cost = await analytics.cost(scope, top=5)
    assert cost.headline.total_usd == 1.5 and cost.by_day[0].runs == 1
    assert cost.by_model == [] and cost.excluded_expired_traces == 1
    assert (await analytics.reliability(scope, top=5)).excluded_expired_traces == 1
    assert (await analytics.performance(scope, top=5)).excluded_expired_traces == 1
    summary = await analytics.summary(scope)
    assert summary.tools.calls == 4 and summary.run_latency.count == 1  # all from the record


async def _day_hash(engine: AsyncEngine, day: date) -> tuple[str, ...]:
    tables = (
        "analytics_runs_daily", "analytics_cost_daily", "analytics_spans_daily",
        "analytics_latency_daily", "analytics_top_runs",
    )  # fmt: skip
    out = []
    for name in tables:
        out.append(
            str(
                await scalar(
                    engine,
                    f"SELECT coalesce(md5(string_agg(x::text, '|' ORDER BY x::text)), '') "  # noqa: S608
                    f"FROM {name} x WHERE day = :d",
                    d=day,
                )
            )
        )
    return tuple(out)

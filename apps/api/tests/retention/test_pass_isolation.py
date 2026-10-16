"""A pass that reaches the row allowance never skips the later passes of the tick (review P2-1)."""

import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.ingest_helpers import make_tenant
from tests.retention.enforce_helpers import enforce, enforcing, make_store, scalar
from tests.retention.helpers import NOW, days_ago, seed_run


async def test_rows_halt_in_the_run_pass_still_runs_rollups_audit_and_operational(
    engine: AsyncEngine, retention_engine: AsyncEngine, tmp_path: Path
) -> None:
    """A run pass that reaches ABB_RETENTION_MAX_ROWS_PER_TICK leaves `Control.rows` at the cap;
    `refresh_rollups` consults the same counter without a `begin_pass`, so its first checkpoint
    raises Halt('rows'), which `_tiers` swallows: the audit and operational passes of that
    workspace are skipped for the whole tick."""
    store = make_store(tmp_path)
    acme = await make_tenant(engine, "acme")
    ws = acme.context.workspace_id
    # Old enough for every tier (run tier default 365 days). A finishes in the run pass (so a
    # day becomes eligible for a rollup refresh); B makes the run pass reach the row cap.
    await seed_run(engine, acme, ended=days_ago(401), events=2, spans=0, jobs=0)
    await seed_run(engine, acme, ended=days_ago(400), events=10, spans=0, jobs=0)
    # An audit entry older than the audit cutoff (365 days): the audit pass must delete it.
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO audit_log (workspace_id, actor_kind, actor_id, action, outcome, "
                "occurred_at) VALUES (:w, 'system', 'test', 'old.thing', 'allowed', :t)"
            ),
            {"w": ws, "t": NOW - timedelta(days=400)},
        )
    old_before = int(
        await scalar(
            engine,
            "SELECT count(*) FROM audit_log WHERE workspace_id = :w AND action = 'old.thing'",
            w=ws,
        )
    )
    assert old_before == 1
    cfg = enforcing(retention_max_rows_per_tick=3)  # batch 2: the run pass halts on rows
    await enforce(retention_engine, store, cfg)
    reports = {
        r[0]
        for r in (
            await _rows(engine, "SELECT tier FROM retention_reports WHERE workspace_id = :w", ws)
        )
    }
    old_after = int(
        await scalar(
            engine,
            "SELECT count(*) FROM audit_log WHERE workspace_id = :w AND action = 'old.thing'",
            w=ws,
        )
    )
    # What the design promises: the audit and operational passes ran (reports written) and the
    # old audit entry is gone. If this assertion fails, the pass limit leaked into the tick.
    assert {"audit", "operational"} <= reports, reports
    assert old_after == 0


async def _rows(engine: AsyncEngine, sql: str, ws: uuid.UUID) -> list[Any]:
    async with engine.connect() as conn:
        return [tuple(r) for r in await conn.execute(text(sql), {"w": ws})]


async def test_a_run_updated_after_selection_is_left_alone(
    engine: AsyncEngine, retention_engine: AsyncEngine, tmp_path: Path
) -> None:
    """Review P2-5: the eligibility re-check happens before any data is touched."""
    store = make_store(tmp_path)
    acme = await make_tenant(engine, "acme")
    run = await seed_run(engine, acme, ended=days_ago(400), events=3, spans=0, jobs=0)

    async def bump(point: str) -> None:
        if point == "selected":
            async with engine.begin() as conn:
                await conn.execute(
                    text("UPDATE runs SET updated_at = :t, completed_at = :t WHERE id = :r"),
                    {"t": NOW, "r": run.run_id},
                )

    await enforce(retention_engine, store, enforcing(), hook=bump)
    assert await scalar(engine, "SELECT count(*) FROM runs WHERE id = :r", r=run.run_id) == 1
    assert await scalar(engine, "SELECT count(*) FROM events WHERE run_id = :r", r=run.run_id) == 3
    assert (
        await scalar(
            engine, "SELECT count(*) FROM retention_run_progress WHERE run_id = :r", r=run.run_id
        )
        == 0
    )

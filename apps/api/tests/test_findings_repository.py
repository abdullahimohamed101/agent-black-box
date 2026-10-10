"""FindingsRepository, the findings tables and the runs-module queries Phase 10 added."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from abb_event_schema.ids import IdKind, from_uuid, to_uuid
from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.db import tables as t
from abb_api.findings.detectors import DETECTORS_VERSION
from abb_api.findings.model import Finding, Severity, finding_uuid
from abb_api.findings.repository import AnalysisState, FindingsRepository
from abb_api.runs.queries import RunQueries
from abb_api.tenancy import TenantContext
from tests.detector_helpers import findings_for
from tests.factories import make_project, make_run, make_workspace

START = datetime(2026, 10, 12, 9, 0, 0, tzinfo=UTC)
STATE = AnalysisState(
    detectors_version=DETECTORS_VERSION,
    summary_version_seen=2,
    ordering_mode_seen="sequence",
    event_count_seen=89,
    truncated=False,
)


class World:
    def __init__(self, ws: uuid.UUID, project: uuid.UUID, run: uuid.UUID) -> None:
        self.ws, self.project, self.run = ws, project, run
        self.tenant = TenantContext(ws)


async def world(engine: AsyncEngine, slug: str = "a") -> World:
    async with engine.begin() as conn:
        ws = await make_workspace(conn, f"ws-{slug}-{uuid.uuid4().hex[:6]}")
        project = await make_project(conn, ws)
        run = await make_run(conn, ws, project, started_at=START, agent_slug="coder")
    return World(ws, project, run)


async def replace(
    engine: AsyncEngine, w: World, findings: list[Finding], state: AnalysisState = STATE
) -> None:
    async with engine.begin() as conn:
        await FindingsRepository(conn, w.tenant).replace_run(
            w.run,
            project_id=w.project,
            agent_slug="coder",
            run_started_at=START,
            findings=findings,
            state=state,
        )


async def read(engine: AsyncEngine, w: World, tenant: TenantContext | None = None) -> Any:
    async with engine.connect() as conn:
        return await FindingsRepository(conn, tenant or w.tenant).for_run(w.run)


async def test_findings_round_trip_with_derived_stable_ids(engine: AsyncEngine) -> None:
    w = await world(engine)
    produced = findings_for("looping")
    await replace(engine, w, produced)
    state, records = await read(engine, w)

    assert state.finding_count == 3 and state.max_severity == "critical"
    assert (state.detectors_version, state.summary_version_seen) == (DETECTORS_VERSION, 2)
    assert state.event_count_seen == 89 and state.truncated is False
    assert state.ordering_mode_seen == "sequence" and state.project_id == w.project
    assert [r.severity for r in records] == ["critical", "warning", "warning"]  # worst first
    by_print = {f.fingerprint: f for f in produced}
    for record in records:
        source = by_print[record.fingerprint]
        assert record.id == finding_uuid(w.ws, w.run, record.fingerprint)
        assert record.evidence == source.evidence and record.kind == source.kind.value
        assert record.evidence_event_ids == tuple(to_uuid(i) for i in source.evidence_event_ids)
        assert record.start_event_id == (
            to_uuid(source.start_event_id) if source.start_event_id else None
        )
        assert from_uuid(IdKind.EVENT, record.evidence_event_ids[0]).startswith("evt_")
        assert record.agent_slug == "coder" and record.project_id == w.project


async def test_rewriting_keeps_ids_and_created_at_and_moves_updated_at(engine: AsyncEngine) -> None:
    w = await world(engine)
    produced = findings_for("looping")
    await replace(engine, w, produced)
    _, first = await read(engine, w)
    await replace(engine, w, produced)
    _, second = await read(engine, w)

    assert [(r.id, r.created_at) for r in first] == [(r.id, r.created_at) for r in second]
    assert all(b.updated_at > a.updated_at for a, b in zip(first, second, strict=True))
    async with engine.connect() as conn:
        count = await conn.scalar(select(func.count()).select_from(t.findings))
    assert count == 3  # upserted by fingerprint, never appended


async def test_a_rewrite_follows_the_new_findings_and_deletes_the_rest(engine: AsyncEngine) -> None:
    w = await world(engine)
    produced = findings_for("looping")
    await replace(engine, w, produced)
    softer = Finding(**{**produced[0].__dict__, "severity": Severity.INFO})
    await replace(engine, w, [softer, produced[1]])
    state, records = await read(engine, w)
    assert state.finding_count == 2 and state.max_severity == "warning"
    assert {r.fingerprint for r in records} == {softer.fingerprint, produced[1].fingerprint}
    assert next(r for r in records if r.fingerprint == softer.fingerprint).severity == "info"

    await replace(engine, w, [], dataclass_state(computed=START))
    state, records = await read(engine, w)
    assert records == [] and state.finding_count == 0 and state.max_severity is None
    assert state.computed_at == START


def dataclass_state(computed: datetime | None = None) -> AnalysisState:
    return AnalysisState(
        detectors_version=DETECTORS_VERSION,
        summary_version_seen=2,
        ordering_mode_seen="time",
        event_count_seen=5,
        truncated=True,
        computed_at=computed,
    )


async def test_duplicate_fingerprints_collapse_to_one_row_with_the_higher_severity(
    engine: AsyncEngine,
) -> None:
    w = await world(engine)
    base = findings_for("looping")[1]
    worse = Finding(**{**base.__dict__, "severity": Severity.CRITICAL})
    await replace(engine, w, [base, worse, base])
    state, records = await read(engine, w)
    assert state.finding_count == 1 and records[0].severity == "critical"


async def test_findings_are_scoped_to_the_workspace(engine: AsyncEngine) -> None:
    mine, theirs = await world(engine, "mine"), await world(engine, "theirs")
    await replace(engine, mine, findings_for("looping"))
    # the same run id, read as the other tenant: nothing
    state, records = await read(engine, mine, tenant=theirs.tenant)
    assert state is None and records == []
    async with engine.connect() as conn:
        assert await FindingsRepository(conn, theirs.tenant).badges([mine.run]) == {}
    assert await read(engine, theirs) == (None, [])


async def test_badges_come_from_run_findings_alone(engine: AsyncEngine) -> None:
    w = await world(engine)
    other_run = None
    async with engine.begin() as conn:
        other_run = await make_run(conn, w.ws, w.project)
    await replace(engine, w, findings_for("failing"))
    async with engine.connect() as conn:
        badges = await FindingsRepository(conn, w.tenant).badges([w.run, other_run])
    assert set(badges) == {w.run}  # a run nobody analysed has no badge
    assert (badges[w.run].count, badges[w.run].max_severity) == (2, "critical")
    assert badges[w.run].detectors_version == DETECTORS_VERSION
    async with engine.connect() as conn:
        assert await FindingsRepository(conn, w.tenant).badges([]) == {}


async def test_deleting_a_run_deletes_its_findings(engine: AsyncEngine) -> None:
    w = await world(engine)
    await replace(engine, w, findings_for("looping"))
    async with engine.begin() as conn:
        await conn.execute(delete(t.runs).where(t.runs.c.id == w.run))
    async with engine.connect() as conn:
        assert await conn.scalar(select(func.count()).select_from(t.findings)) == 0
        assert await conn.scalar(select(func.count()).select_from(t.run_findings)) == 0


def row(w: World, **overrides: Any) -> dict[str, Any]:
    fingerprint = uuid.uuid4().hex + uuid.uuid4().hex
    values: dict[str, Any] = {
        "workspace_id": w.ws,
        "id": uuid.uuid4(),
        "run_id": w.run,
        "project_id": w.project,
        "run_started_at": START,
        "detector": "d",
        "detector_version": "d@1.0.0",
        "finding_type": "possible_loop",
        "kind": "probable",
        "severity": "info",
        "confidence": 0.5,
        "evidence_event_ids": [],
        "fingerprint": fingerprint,
        "evidence": {},
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "maybe"},
        {"severity": "fatal"},
        {"confidence": 1.5},
        {"confidence": -0.1},
        {"evidence": [1]},
        {"evidence": {"k": "x" * 20_000}},
        {"evidence_event_ids": [uuid.uuid4() for _ in range(51)]},
        {"fingerprint": "short"},
        {"detector": "d" * 65},
        {"detector_version": "v" * 33},
        {"finding_type": "t" * 65},
    ],
)
async def test_the_database_rejects_what_the_model_never_produces(
    engine: AsyncEngine, bad: dict[str, Any]
) -> None:
    w = await world(engine)
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(insert(t.findings).values(**row(w, **bad)))


async def test_a_finding_cannot_point_at_another_tenants_run_and_fingerprints_are_unique(
    engine: AsyncEngine,
) -> None:
    mine, theirs = await world(engine, "mine"), await world(engine, "theirs")
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(insert(t.findings).values(**row(mine, run_id=theirs.run)))
    first = row(mine)
    async with engine.begin() as conn:
        await conn.execute(insert(t.findings).values(**first))
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                insert(t.findings).values(**row(mine, fingerprint=first["fingerprint"]))
            )
    async with engine.begin() as conn:  # the same fingerprint on another run is fine
        other = await make_run(conn, mine.ws, mine.project)
        await conn.execute(
            insert(t.findings).values(**row(mine, run_id=other, fingerprint=first["fingerprint"]))
        )


async def test_run_findings_checks(engine: AsyncEngine) -> None:
    w = await world(engine)
    values = {
        "workspace_id": w.ws,
        "run_id": w.run,
        "project_id": w.project,
        "detectors_version": 1,
        "summary_version_seen": 2,
        "ordering_mode_seen": "sequence",
        "computed_at": START,
        "event_count_seen": 1,
        "truncated": False,
        "finding_count": 0,
    }
    for bad in ({"ordering_mode_seen": "random"}, {"max_severity": "fatal"}):
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(insert(t.run_findings).values(**{**values, **bad}))


# --------------------------------------------------------------------------- RunQueries


async def baseline_world(engine: AsyncEngine) -> tuple[World, World, list[uuid.UUID]]:
    """Runs of one agent over 40 days plus decoys: other agent, project, workspace, status."""
    w = await world(engine, "base")
    other_ws = await world(engine, "canary")
    async with engine.begin() as conn:
        other_project = await make_project(conn, w.ws, "q")
        ids: list[uuid.UUID] = []

        async def add(project: uuid.UUID, ws: uuid.UUID, days: float, cost: str, **kw: Any) -> None:
            kw.setdefault("agent_slug", "coder")
            kw.setdefault("status", "SUCCESS")
            kw.setdefault("summary_version", 2)
            ids.append(
                await make_run(
                    conn,
                    ws,
                    project,
                    started_at=START - timedelta(days=days),
                    cost_usd=Decimal(cost),
                    **kw,
                )
            )

        for i in range(1, 6):
            await add(w.project, w.ws, i, f"0.0{i}")  # five good runs, days 1..5 before
        await add(w.project, w.ws, 0.5, "0.50", status="FAILED")  # failed counts
        await add(w.project, w.ws, 2.5, "0.60", status="TIMED_OUT")
        await add(w.project, w.ws, 3.5, "0.70", status="BLOCKED")
        await add(w.project, w.ws, 31, "9.01")  # outside the 30-day window
        await add(w.project, w.ws, 1.5, "9.02", status="RUNNING")  # not finished
        await add(w.project, w.ws, 1.6, "9.03", status="CANCELLED")  # not a finished outcome
        await add(w.project, w.ws, 1.7, "9.04", summary_version=0)  # never summarised
        await add(w.project, w.ws, 1.8, "9.05", agent_slug="reviewer")
        await add(other_project, w.ws, 1.9, "9.06")
        await add(other_ws.project, other_ws.ws, 1.1, "777.777")  # the canary tenant, same agent
        await add(w.project, w.ws, -1, "9.07")  # started after the run under analysis
    return w, other_ws, ids


async def baseline(engine: AsyncEngine, w: World, **kw: Any) -> list[tuple[datetime, Decimal]]:
    args: dict[str, Any] = {"window_days": 30, "limit": 200}
    args.update(kw)
    async with engine.connect() as conn:
        return await RunQueries(conn, w.tenant).cost_baseline(w.project, "coder", START, **args)


async def test_cost_baseline_selects_only_comparable_finished_summarised_runs(
    engine: AsyncEngine,
) -> None:
    w, _, _ = await baseline_world(engine)
    rows = await baseline(engine, w)
    costs = [cost for _, cost in rows]
    assert sorted(costs) == sorted(
        Decimal(c) for c in ("0.01", "0.02", "0.03", "0.04", "0.05", "0.50", "0.60", "0.70")
    )
    assert [s for s, _ in rows] == sorted((s for s, _ in rows), reverse=True)  # newest first
    assert Decimal("777.777") not in costs and all(c < 1 for c in costs)


async def test_cost_baseline_window_limit_and_before_are_exact(engine: AsyncEngine) -> None:
    w, _, _ = await baseline_world(engine)
    assert len(await baseline(engine, w, limit=3)) == 3
    newest_three = {c for _, c in await baseline(engine, w, limit=3)}
    assert newest_three == {Decimal("0.50"), Decimal("0.01"), Decimal("0.02")}
    assert len(await baseline(engine, w, window_days=2)) == 3  # 0.5, 1 and 2 days before
    async with engine.connect() as conn:
        earlier = await RunQueries(conn, w.tenant).cost_baseline(
            w.project, "coder", START - timedelta(days=4), window_days=30, limit=200
        )
    # five days before (0.05) and 31 days before START, which is 27 days before this `before`
    assert {c for _, c in earlier} == {Decimal("0.05"), Decimal("9.01")}
    # the window is relative to the run under analysis, not to today
    far = START + timedelta(days=60)
    async with engine.connect() as conn:
        assert (
            await RunQueries(conn, w.tenant).cost_baseline(
                w.project, "coder", far, window_days=30, limit=200
            )
            == []
        )


async def test_cost_baseline_never_crosses_the_workspace(engine: AsyncEngine) -> None:
    w, other, _ = await baseline_world(engine)
    async with engine.connect() as conn:
        theirs = await RunQueries(conn, other.tenant).cost_baseline(
            other.project, "coder", START, window_days=30, limit=200
        )
        assert [c for _, c in theirs] == [Decimal("777.777")]
        # asking with the other tenant for this tenant's project finds nothing
        none = await RunQueries(conn, other.tenant).cost_baseline(
            w.project, "coder", START, window_days=30, limit=200
        )
        assert none == []


async def test_job_states_work_for_any_job_type_and_summary_states_still_delegates(
    engine: AsyncEngine,
) -> None:
    w = await world(engine)
    key = f"{w.ws}:{w.run}"
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO outbox_jobs (id, job_type, workspace_id, dedupe_key, status) VALUES "
                "(:a, 'detect_findings', :ws, :key, 'dead_letter'), "
                "(:b, 'summarize_run', :ws, :key, 'pending')"
            ),
            {"a": uuid.uuid4(), "b": uuid.uuid4(), "ws": w.ws, "key": key},
        )
        queries = RunQueries(conn, w.tenant)
        assert await queries.job_states([w.run], "detect_findings") == {w.run: "failed"}
        assert await queries.job_states([w.run], "summarize_run") == {w.run: "processing"}
        assert await queries.summary_states([w.run]) == {w.run: "processing"}
        assert await queries.job_states([w.run], "something_else") == {w.run: "current"}
        assert await queries.job_states([], "detect_findings") == {}

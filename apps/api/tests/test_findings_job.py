"""The detect_findings job: equivalence (INV-2), concurrency, isolation, bounds, cost baseline."""

import io
import json
import logging
import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from abb_event_schema.enums import RunStatus
from abb_event_schema.event import Event, finalize
from abb_event_schema.ids import to_uuid
from abb_event_schema.ordering import sort_events
from abb_event_schema.parse import parse_event_in
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api import cli
from abb_api.core.config import Settings
from abb_api.db import tables as t
from abb_api.findings import job as findings_job
from abb_api.findings.build import build_context, make_baseline
from abb_api.findings.detectors import DETECTORS_VERSION, run_all
from abb_api.findings.model import Detector, DetectorConfig, DetectorContext, Finding
from abb_api.findings.repository import FindingsRepository
from abb_api.jobs.handlers import HANDLERS, detect_findings
from abb_api.jobs.outbox import DETECT_FINDINGS, Job, OutboxRepository
from abb_api.jobs.worker import Worker
from abb_api.runs.queries import RunQueries
from tests.api_fixtures import Tick
from tests.conftest import make_settings
from tests.detector_helpers import FIXTURE_NAMES, RECEIVED, findings_for, load_runs
from tests.factories import make_project, make_run
from tests.ingest_helpers import Tenant, build_event, make_run_ids, make_tenant
from tests.test_summarizer import drain, ingest, run_row, worker_for

CANARY = "CMDCANARY-curl-H-Bearer-s3cr3t"
T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)


def wire_to_event(tenant: Tenant, wire: dict[str, Any]) -> Event:
    return finalize(
        parse_event_in(wire),
        workspace_id=tenant.workspace_id,
        project_id=tenant.projects["p"],
        received_at=RECEIVED,
    )


async def stored(engine: AsyncEngine, tenant: Tenant, run_id: str) -> tuple[Any, list[Any]]:
    async with engine.connect() as conn:
        return await FindingsRepository(conn, tenant.context).for_run(to_uuid(run_id))


def key(f: Any) -> tuple[str, str, str, str]:
    return (f.detector, f.finding_type, f.fingerprint, json.dumps(f.evidence, sort_keys=True))


async def jobs(engine: AsyncEngine, job_type: str = DETECT_FINDINGS) -> list[Any]:
    async with engine.connect() as conn:
        rows = await conn.execute(select(t.outbox_jobs).where(t.outbox_jobs.c.job_type == job_type))
        return list(rows)


# --------------------------------------------------------------------------- INV-2 equivalence


async def test_findings_equal_a_from_scratch_run_over_the_complete_event_set(
    engine: AsyncEngine, database_url: str
) -> None:
    """Random batch order and worker interleaving cannot change the final findings (INV-2)."""
    tenant = await make_tenant(engine, "acme")
    names = [n for n in FIXTURE_NAMES if n != "cost_spike"]
    wires = [w for n in names for run in load_runs(n).values() for w in run]
    events = [wire_to_event(tenant, w) for w in wires]
    rng = random.Random(11)
    rng.shuffle(events)
    worker = worker_for(engine, database_url)
    position = 0
    while position < len(events):
        size = rng.randint(1, 40)
        await ingest(engine, tenant, events[position : position + size])
        position += size
        if rng.random() < 0.5:
            await worker.run_once()
    await drain(worker)

    for name in names:
        (wire_run,) = load_runs(name).values()
        run_id = wire_run[0]["run_id"]
        expected = sorted(key(f) for f in findings_for(name))
        state, records = await stored(engine, tenant, run_id)
        assert sorted(key(r) for r in records) == expected, name
        assert state.finding_count == len(expected) and state.detectors_version == DETECTORS_VERSION
        assert state.event_count_seen == len(wire_run) and state.truncated is False
        assert state.ordering_mode_seen == "sequence"
    # the work went through the outbox: every job finished
    async with engine.connect() as conn:
        assert {r.status for r in await conn.execute(select(t.outbox_jobs.c.status))} == {"done"}


async def test_rebuilding_after_a_loss_reproduces_the_findings_with_the_same_ids(
    engine: AsyncEngine, database_url: str
) -> None:
    tenant = await make_tenant(engine, "acme")
    (wire_run,) = load_runs("looping").values()
    await ingest(engine, tenant, [wire_to_event(tenant, w) for w in wire_run])
    worker = worker_for(engine, database_url)
    await drain(worker)
    run_id = wire_run[0]["run_id"]
    _, before = await stored(engine, tenant, run_id)
    assert len(before) == 3

    # a rebuild over intact rows changes nothing but updated_at
    settings = make_settings(database_url)
    code = await cli.run(
        ["rebuild-findings", "--workspace", "acme"], settings, out=io.StringIO(), err=io.StringIO()
    )
    assert code == 0
    await drain(worker)
    _, again = await stored(engine, tenant, run_id)
    assert [(r.id, r.created_at, key(r)) for r in again] == [
        (r.id, r.created_at, key(r)) for r in before
    ]
    assert all(b.updated_at > a.updated_at for a, b in zip(before, again, strict=True))

    # a rebuild after the rows were lost re-derives the same findings with the same ids
    async with engine.begin() as conn:
        await conn.execute(delete(t.findings))
        await conn.execute(delete(t.run_findings))
    code = await cli.run(
        ["rebuild-findings", "--workspace", "acme"], settings, out=io.StringIO(), err=io.StringIO()
    )
    await drain(worker)
    _, rebuilt = await stored(engine, tenant, run_id)
    assert [(r.id, key(r)) for r in rebuilt] == [(r.id, key(r)) for r in before]


async def test_every_summary_asks_for_findings_even_when_nothing_changed(
    engine: AsyncEngine, database_url: str
) -> None:
    """A cost rebuild re-derives the summary of an unchanged run: the cost-spike input moved."""
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    await ingest(
        engine,
        tenant,
        [build_event(tenant, run, n=1, event_type="run.started", attributes={}, span_id=...)],
    )
    worker = worker_for(engine, database_url)
    await drain(worker)
    _, first = await stored(engine, tenant, run["run_id"])
    state_first, _ = await stored(engine, tenant, run["run_id"])
    async with engine.begin() as conn:
        await OutboxRepository(conn, tenant.context).enqueue_summarize([to_uuid(run["run_id"])])
    pending = [j for j in await jobs(engine, "summarize_run") if j.status == "pending"]
    assert len(pending) == 1
    await drain(worker)
    state_second, _ = await stored(engine, tenant, run["run_id"])
    assert state_second.computed_at > state_first.computed_at
    assert first == []


async def test_a_summary_enqueues_one_coalesced_detect_job_per_run(
    engine: AsyncEngine, database_url: str
) -> None:
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    settings = make_settings(database_url, findings_delay_seconds=30.0)
    worker = Worker(engine, HANDLERS, settings, owner="w")
    for n in (1, 2, 3):
        await ingest(engine, tenant, [build_event(tenant, run, n=n)])
        await worker.run_once()  # summarize
    pending = [j for j in await jobs(engine) if j.status == "pending"]
    assert (
        len(pending) == 1
        and pending[0].dedupe_key == f"{tenant.context.workspace_id}:{to_uuid(run['run_id'])}"
    )
    assert pending[0].available_at > datetime.now(UTC) + timedelta(seconds=20)  # delayed, not due


def test_the_delay_grows_with_the_run_and_is_capped() -> None:
    delay = findings_job.detect_delay
    assert delay(5.0, 1) == timedelta(seconds=5)
    assert delay(5.0, 10_000) == timedelta(seconds=5)
    assert delay(5.0, 30_000) == timedelta(seconds=15)
    assert delay(5.0, 1_000_000) == timedelta(seconds=60)
    assert delay(0.0, 1_000_000) == timedelta(0)
    assert delay(120.0, 1) == timedelta(seconds=120)  # an operator's larger base is respected


def test_settings_defaults_match_the_detector_config_and_are_validated() -> None:
    settings = make_settings("postgresql+asyncpg://x:y@localhost/db", findings_delay_seconds=5.0)
    assert findings_job.detector_config(settings) == DetectorConfig()
    assert settings.findings_max_events == 50_000
    for bad in (
        {"findings_loop_min_repetitions": 1},
        {"findings_max_events": 10},
        {"findings_context_growth_factor": 1.0},
        {"findings_cascade_window_calls": 2, "findings_cascade_min_failures": 3},
        {"findings_delay_seconds": -1},
    ):
        with pytest.raises(ValueError):
            Settings(database_url="postgresql+asyncpg://x:y@localhost/db", **bad)


# --------------------------------------------------------------------------- isolation


class Raising(Detector):
    name = "raising"
    version = "1.0.0"

    def detect(self, ctx: DetectorContext) -> list[Finding]:
        raise ValueError(CANARY)


async def test_a_detector_that_raises_dead_letters_only_its_own_job_without_leaking(
    engine: AsyncEngine,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    tenant = await make_tenant(engine, "acme")
    (wire_run,) = load_runs("failing").values()
    run_id = wire_run[0]["run_id"]
    worker = Worker(
        engine,
        HANDLERS,
        make_settings(
            database_url, worker_backoff_base_seconds=0.0, worker_backoff_max_seconds=0.0
        ),
        owner="w",
    )
    # first, a good analysis exists
    await ingest(engine, tenant, [wire_to_event(tenant, w) for w in wire_run])
    await drain(worker)
    _, good = await stored(engine, tenant, run_id)
    assert good

    # then the detectors break and the run is analysed again
    monkeypatch.setattr(findings_job, "run_all", lambda ctx: run_all(ctx, (Raising(),)))
    async with engine.begin() as conn:
        await OutboxRepository(conn, tenant.context).enqueue_detect([to_uuid(run_id)])
    for _ in range(12):
        await worker.run_once()
    (dead,) = [j for j in await jobs(engine) if j.status == "dead_letter"]
    assert dead.attempt_count == 5
    assert dead.last_error == "DetectorError: detector raising failed over 15 events"
    assert CANARY not in dead.last_error

    logged = "\n".join(
        f"{r.getMessage()} {r.exc_text or ''} {r.__dict__.get('error_type', '')}"
        for r in caplog.records
    )
    assert "job failed" in logged and CANARY not in logged
    assert all(CANARY not in str(r.args) for r in caplog.records)

    # the summary is untouched, the route would say failed, and the previous analysis is still there
    async with engine.connect() as conn:
        queries = RunQueries(conn, tenant.context)
        assert await queries.summary_states([to_uuid(run_id)]) == {to_uuid(run_id): "current"}
        assert await queries.job_states([to_uuid(run_id)], DETECT_FINDINGS) == {
            to_uuid(run_id): "failed"
        }
    _, still = await stored(engine, tenant, run_id)
    assert [key(r) for r in still] == [key(r) for r in good]


async def test_an_error_while_assembling_the_analysis_is_also_a_fixed_message(
    engine: AsyncEngine, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = await make_tenant(engine, "acme")
    (wire_run,) = load_runs("healthy").values()
    await ingest(engine, tenant, [wire_to_event(tenant, w) for w in wire_run])
    worker = Worker(
        engine,
        HANDLERS,
        make_settings(database_url, worker_max_attempts=1),
        owner="w",
    )

    def boom(*args: Any, **kwargs: Any) -> DetectorContext:
        raise ValueError(CANARY)

    monkeypatch.setattr("abb_api.findings.job.build_context", boom)
    await worker.run_once()  # summarize
    await worker.run_once()  # detect
    (dead,) = [j for j in await jobs(engine) if j.status == "dead_letter"]
    assert dead.last_error == "DetectorError: detector analysis failed over 37 events"


async def test_a_job_for_an_unknown_run_or_another_workspace_is_a_no_op(
    engine: AsyncEngine, database_url: str
) -> None:
    mine, theirs = await make_tenant(engine, "mine"), await make_tenant(engine, "theirs")
    run = make_run_ids()
    await ingest(engine, theirs, [build_event(theirs, run, n=1)])
    async with engine.begin() as conn:
        out = OutboxRepository(conn, mine.context)
        # a run that does not exist, and a run that belongs to the other workspace
        await out.enqueue_detect([uuid.uuid4(), to_uuid(run["run_id"])])
    worker = worker_for(engine, database_url)
    await drain(worker)
    async with engine.connect() as conn:
        assert await conn.scalar(select(func.count()).select_from(t.run_findings)) == 1  # theirs
        mine_rows = await conn.scalar(
            select(func.count())
            .select_from(t.run_findings)
            .where(t.run_findings.c.workspace_id == mine.context.workspace_id)
        )
    assert mine_rows == 0
    assert {j.status for j in await jobs(engine)} == {"done"}


# --------------------------------------------------------------------------- concurrency


async def test_two_detect_jobs_for_one_run_never_both_write_and_the_loser_comes_back(
    engine: AsyncEngine, database_url: str
) -> None:
    tenant = await make_tenant(engine, "acme")
    (wire_run,) = load_runs("looping").values()
    run_id = to_uuid(wire_run[0]["run_id"])
    await ingest(engine, tenant, [wire_to_event(tenant, w) for w in wire_run])
    worker = Worker(
        engine, {"summarize_run": HANDLERS["summarize_run"]}, make_settings(database_url), owner="w"
    )
    await worker.run_once()  # summarize only: the detect job is left pending
    (pending,) = [j for j in await jobs(engine) if j.status == "pending"]

    settings = make_settings(database_url, findings_delay_seconds=2.0)
    async with engine.connect() as holder:  # the "other worker", still inside its transaction
        await holder.begin()
        assert await findings_job.try_lock_run(holder, tenant.context, run_id)
        async with engine.begin() as conn:
            job = Job(pending.id, DETECT_FINDINGS, pending.workspace_id, pending.dedupe_key,
                      pending.payload, 1, "w2")  # fmt: skip
            outcome = await findings_job.detect_run(conn, tenant.context, run_id, settings)
            assert outcome == "busy"
            # the handler re-enqueues itself, coalesced, instead of waiting for the lock
            from abb_api.jobs import context as job_context

            token = job_context.set_job_settings(settings)
            try:
                await detect_findings(job, conn)
            finally:
                job_context.reset_job_settings(token)
        state, records = await stored(engine, tenant, str(wire_run[0]["run_id"]))
        assert state is None and records == []  # the loser wrote nothing
        await holder.rollback()
    # the original job is still pending (never claimed in this test) and the re-enqueue coalesced
    pending_jobs = [j for j in await jobs(engine) if j.status == "pending"]
    assert len(pending_jobs) == 1
    # once the lock is free, the job runs and writes exactly one row set
    await drain(worker_for(engine, database_url))
    state, records = await stored(engine, tenant, str(wire_run[0]["run_id"]))
    assert state.finding_count == 3 and len(records) == 3


async def test_a_losing_job_that_is_not_pending_enqueues_a_delayed_successor(
    engine: AsyncEngine, database_url: str
) -> None:
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    await ingest(engine, tenant, [build_event(tenant, run, n=1)])
    run_id = to_uuid(run["run_id"])
    worker = Worker(
        engine, {"summarize_run": HANDLERS["summarize_run"]}, make_settings(database_url), owner="w"
    )
    await worker.run_once()
    async with engine.begin() as conn:  # claim-like: mark the pending job running
        await conn.execute(
            t.outbox_jobs.update()
            .where(t.outbox_jobs.c.job_type == DETECT_FINDINGS)
            .values(status="running", lease_owner="w2")
        )
    (running,) = await jobs(engine)
    job = Job(running.id, DETECT_FINDINGS, running.workspace_id, running.dedupe_key,
              running.payload, 1, "w2")  # fmt: skip
    async with engine.connect() as holder:
        await holder.begin()
        await findings_job.try_lock_run(holder, tenant.context, run_id)
        async with engine.begin() as conn:
            from abb_api.jobs import context as job_context

            token = job_context.set_job_settings(make_settings(database_url))
            try:
                await detect_findings(job, conn)
            finally:
                job_context.reset_job_settings(token)
        await holder.rollback()
    states = sorted(j.status for j in await jobs(engine))
    assert states == ["pending", "running"]  # a successor, due later; the loser finishes normally
    (successor,) = [j for j in await jobs(engine) if j.status == "pending"]
    assert successor.available_at > datetime.now(UTC)


# --------------------------------------------------------------------------- the row can lag


async def test_status_and_counters_come_from_the_events_not_from_a_lagging_run_row(
    engine: AsyncEngine, database_url: str
) -> None:
    tenant = await make_tenant(engine, "acme")
    (wire_run,) = load_runs("failing").values()
    run_id = wire_run[0]["run_id"]
    await ingest(engine, tenant, [wire_to_event(tenant, w) for w in wire_run])
    row = await run_row(engine, run_id)
    assert row.summary_version == 0 and row.status != "FAILED"  # never summarised: the row lags
    async with engine.begin() as conn:
        await OutboxRepository(conn, tenant.context).enqueue_detect([to_uuid(run_id)])
    worker = Worker(
        engine, {DETECT_FINDINGS: HANDLERS[DETECT_FINDINGS]}, make_settings(database_url), owner="w"
    )
    await worker.run_once()
    state, records = await stored(engine, tenant, run_id)
    assert state.summary_version_seen == 0  # the mismatch is visible
    chain = next(r for r in records if r.finding_type == "failure_chain")
    assert chain.severity == "critical" and chain.evidence["category"] == "command_failure"
    assert (
        build_context([wire_to_event(tenant, w) for w in wire_run]).facts.status is RunStatus.FAILED
    )


# --------------------------------------------------------------------------- cost spike (AC-8)


async def cost_world(
    engine: AsyncEngine, database_url: str, baseline_runs: int
) -> tuple[Tenant, dict[str, str], Worker]:
    tenant = await make_tenant(engine, "acme", ("p", "q"))
    canary = await make_tenant(engine, "globex")
    ws, project = tenant.context.workspace_id, tenant.project_uuids["p"]
    async with engine.begin() as conn:

        async def seed(
            workspace: uuid.UUID, proj: uuid.UUID, days: float, cost: str, **kw: Any
        ) -> None:
            kw.setdefault("agent_slug", "coding-agent")
            kw.setdefault("status", "SUCCESS")
            kw.setdefault("summary_version", 2)
            await make_run(
                conn,
                workspace,
                proj,
                started_at=T0 - timedelta(days=days),
                cost_usd=Decimal(cost),
                **kw,
            )

        for i in range(baseline_runs):
            await seed(ws, project, 1 + i * 0.1, "0.02" if i < 20 else "0.03")
        await seed(ws, project, 2.0, "9.01", summary_version=0)  # never summarised
        await seed(ws, project, 2.1, "9.02", agent_slug="reviewer")
        await seed(ws, tenant.project_uuids["q"], 2.2, "9.03")
        await seed(ws, project, 45.0, "9.04")  # outside the window
        await seed(ws, project, -1.0, "9.05")  # started after the run under analysis
        canary_project = await make_project(conn, canary.context.workspace_id, "z")
        for i in range(30):
            await seed(canary.context.workspace_id, canary_project, 1 + i * 0.1, "777.777")
    run = make_run_ids()
    llm = {"llm.provider": "p", "llm.model": "m", "llm.input_tokens": 1, "cost.estimated_usd": 0.9}
    await ingest(
        engine,
        tenant,
        [
            build_event(tenant, run, n=1, event_type="run.started", attributes={}, span_id=...),
            build_event(
                tenant,
                run,
                n=2,
                event_type="llm.request.completed",
                attributes=llm,
                status="success",
            ),
            build_event(
                tenant,
                run,
                n=3,
                event_type="run.completed",
                attributes={},
                span_id=...,
                status="success",
            ),
        ],
    )
    return tenant, run, worker_for(engine, database_url)


async def test_cost_spike_uses_exactly_the_comparable_baseline_runs(
    engine: AsyncEngine, database_url: str
) -> None:
    tenant, run, worker = await cost_world(engine, database_url, 25)
    await drain(worker)
    assert (await run_row(engine, run["run_id"])).cost_usd == Decimal("0.9")
    _, records = await stored(engine, tenant, run["run_id"])
    (spike,) = [r for r in records if r.finding_type == "cost_spike"]
    baseline = spike.evidence["baseline"]
    assert baseline["n"] == 25  # 25 comparable runs; none of the decoys, none of the 777.777 ones
    assert Decimal(baseline["median_usd"]) == Decimal("0.02")
    assert Decimal(baseline["p95_usd"]) == Decimal("0.03")
    assert Decimal(spike.evidence["cost_usd"]) == Decimal("0.9")
    assert spike.severity == "critical" and spike.kind == "probable"

    # the stored figures equal a direct query over the same data
    row = await run_row(engine, run["run_id"])
    async with engine.connect() as conn:
        direct = await RunQueries(conn, tenant.context).cost_baseline(
            row.project_id, "coding-agent", row.started_at, window_days=30, limit=200
        )
    expected = make_baseline(direct, agent_slug="coding-agent", window_days=30, min_runs=20)
    assert expected is not None
    assert (expected.n, expected.median_usd, expected.p95_usd) == (
        baseline["n"], Decimal(baseline["median_usd"]), Decimal(baseline["p95_usd"]),
    )  # fmt: skip
    assert baseline["agent_slug"] == "coding-agent" and baseline["window_days"] == 30
    assert baseline["newest_started_at"] == expected.newest_started_at.isoformat()


async def test_no_cost_spike_below_the_minimum_baseline(
    engine: AsyncEngine, database_url: str
) -> None:
    tenant, run, worker = await cost_world(engine, database_url, 19)
    await drain(worker)
    _, records = await stored(engine, tenant, run["run_id"])
    assert not [r for r in records if r.finding_type == "cost_spike"]


async def test_a_baseline_that_moved_changes_only_the_cost_spike_finding(
    engine: AsyncEngine, database_url: str
) -> None:
    tenant, run, worker = await cost_world(engine, database_url, 25)
    await drain(worker)
    _, before = await stored(engine, tenant, run["run_id"])
    async with engine.begin() as conn:  # the baseline runs become expensive
        await conn.execute(
            t.runs.update()
            .where(t.runs.c.cost_usd.in_([Decimal("0.02"), Decimal("0.03")]))
            .values(cost_usd=Decimal("1.50"))
        )
        await OutboxRepository(conn, tenant.context).enqueue_detect([to_uuid(run["run_id"])])
    await drain(worker)
    _, after = await stored(engine, tenant, run["run_id"])
    assert any(r.finding_type == "cost_spike" for r in before)
    assert not any(r.finding_type == "cost_spike" for r in after)


# --------------------------------------------------------------------------- bounds (AC-9)


def tied_events(tenant: Tenant, run: dict[str, str], count: int, *, sequences: bool) -> list[Event]:
    """`count` events in groups of 7 that tie on sequence; in odd groups they also tie on
    occurred_at (event_id decides), in even groups occurred_at runs against the order of creation
    (and so of event_id). Group boundaries straddle the cap, so a capped load that ordered by
    (sequence, event_id) alone would pick different events than the canonical prefix."""
    out = []
    for i in range(count):
        group, offset = divmod(i, 7)
        seconds = group * 10 + (0 if group % 2 else 6 - offset)
        out.append(
            build_event(
                tenant,
                run,
                n=0,
                event_type="tool.call.completed",
                attributes={"tool.name": f"t{i % 11}"},
                sequence=group if sequences else ...,
                occurred_at=(T0 + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z"),
            )
        )
    return out


@pytest.mark.parametrize("sequences", [True, False])
async def test_a_capped_load_is_the_canonical_prefix_even_with_ties(
    engine: AsyncEngine, database_url: str, monkeypatch: pytest.MonkeyPatch, sequences: bool
) -> None:
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    events = tied_events(tenant, run, 450, sequences=sequences)
    random.Random(5).shuffle(events)
    await ingest(engine, tenant, events)
    seen: list[DetectorContext] = []
    real = build_context

    def spy(evs: Any, **kw: Any) -> DetectorContext:
        ctx = real(evs, **kw)
        seen.append(ctx)
        return ctx

    monkeypatch.setattr("abb_api.findings.job.build_context", spy)
    worker = Worker(
        engine, HANDLERS, make_settings(database_url, findings_max_events=200), owner="w"
    )
    await drain(worker)

    state, _ = await stored(engine, tenant, run["run_id"])
    assert (state.event_count_seen, state.truncated) == (200, True)
    assert state.ordering_mode_seen == ("sequence" if sequences else "time")
    wanted = [e.event_id for e in sort_events(events)[:200]]
    assert [
        e.event_id for e in seen[-1].events
    ] == wanted  # a (sequence, event_id)-only order fails


async def test_not_truncated_when_the_run_fits(engine: AsyncEngine, database_url: str) -> None:
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    await ingest(engine, tenant, tied_events(tenant, run, 200, sequences=True))
    worker = Worker(
        engine, HANDLERS, make_settings(database_url, findings_max_events=200), owner="w"
    )
    await drain(worker)
    state, _ = await stored(engine, tenant, run["run_id"])
    assert (state.event_count_seen, state.truncated) == (200, False)


@pytest.mark.timeout(240)
async def test_a_sixty_thousand_event_run_is_analysed_up_to_the_default_cap(
    engine: AsyncEngine, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    events = tied_events(tenant, run, 60_000, sequences=True)
    for start in range(0, len(events), 5_000):
        await ingest(engine, tenant, events[start : start + 5_000])
    seen: list[DetectorContext] = []
    real = build_context

    def spy(evs: Any, **kw: Any) -> DetectorContext:
        seen.append(real(evs, **kw))
        return seen[-1]

    monkeypatch.setattr("abb_api.findings.job.build_context", spy)
    worker = Worker(engine, HANDLERS, make_settings(database_url), owner="w")  # the default cap
    await drain(worker)
    state, _ = await stored(engine, tenant, run["run_id"])
    assert state.event_count_seen == 50_000 and state.truncated is True
    assert state.ordering_mode_seen == "sequence"
    assert [e.event_id for e in seen[-1].events] == [
        e.event_id for e in sort_events(events)[:50_000]
    ]
    assert (await run_row(engine, run["run_id"])).summary["event_count"] == 60_000


# --------------------------------------------------------------------------- logs


async def test_the_job_logs_counts_and_ids_only(
    engine: AsyncEngine, database_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    tenant = await make_tenant(engine, "acme")
    (wire_run,) = load_runs("failing").values()
    events = [wire_to_event(tenant, w) for w in wire_run]
    events = [
        e.model_copy(update={"attributes": {**e.attributes, "shell.command": CANARY}})
        for e in events
    ]
    await ingest(engine, tenant, events)
    await drain(worker_for(engine, database_url))
    computed = [r for r in caplog.records if r.getMessage() == "findings computed"]
    assert len(computed) == 1
    extra = {
        k: v
        for k, v in computed[0].__dict__.items()
        if k in ("run_id", "event_count", "findings", "truncated", "duration_ms")
    }
    assert set(extra) == {"run_id", "event_count", "findings", "truncated", "duration_ms"}
    assert all(CANARY not in f"{r.getMessage()} {r.__dict__}" for r in caplog.records)


# --------------------------------------------------------------------------- CLI (AC-10)


async def invoke(settings: Settings, *argv: str) -> tuple[int, str]:
    err = io.StringIO()
    code = await cli.run(list(argv), settings, clock=Tick(), out=io.StringIO(), err=err)
    return code, err.getvalue()


async def test_rebuild_findings_queues_the_newest_runs_and_writes_one_audit_row(
    engine: AsyncEngine, database_url: str
) -> None:
    settings = make_settings(database_url)
    tenant = await make_tenant(engine, "acme", ("p", "q"))
    ws, p, q = tenant.context.workspace_id, tenant.project_uuids["p"], tenant.project_uuids["q"]
    async with engine.begin() as conn:
        for i in range(4):
            await make_run(conn, ws, p, started_at=T0 - timedelta(days=i))
        await make_run(conn, ws, q, started_at=T0)
    code, err = await invoke(settings, "rebuild-findings", "--workspace", "acme", "--limit", "3")
    assert code == 0 and "queued 3 of 3 run(s)" in err and "WARNING" in err
    code, err = await invoke(settings, "rebuild-findings", "--workspace", "acme", "--project", "p")
    assert code == 0 and "queued 2 of 4 run(s)" in err and "WARNING" not in err  # 2 already queued
    code, err = await invoke(
        settings,
        "rebuild-findings",
        "--workspace",
        "acme",
        "--since",
        (T0 - timedelta(hours=1)).isoformat(),
    )
    assert code == 0 and "of 2 run(s)" in err  # the two runs started at T0, one per project
    async with engine.connect() as conn:
        audit = list(
            await conn.execute(
                select(t.audit_log)
                .where(t.audit_log.c.action == "findings.rebuild")
                .order_by(t.audit_log.c.id)
            )
        )
    assert len(audit) == 3 and all(a.actor_kind == "cli" for a in audit)
    assert audit[0].details == {"queued": 3, "matched": 3}
    assert len([j for j in await jobs(engine) if j.status == "pending"]) == 5


@pytest.mark.parametrize("limit", ["0", "10001", "-1", "many"])
async def test_rebuild_findings_refuses_a_limit_outside_its_bound(
    database_url: str, limit: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        await cli.run(
            ["rebuild-findings", "--workspace", "acme", "--limit", limit],
            make_settings(database_url),
        )
    assert exit_info.value.code == 2
    assert "argument --limit" in capsys.readouterr().err


async def test_rebuild_findings_accepts_the_bounds_and_reports_a_missing_workspace(
    engine: AsyncEngine, database_url: str
) -> None:
    settings = make_settings(database_url)
    code, err = await invoke(
        settings, "rebuild-findings", "--workspace", "nope", "--limit", "10000"
    )
    assert code == 1 and "not found" in err
    code, err = await invoke(settings, "rebuild-findings", "--workspace", "nope", "--limit", "1")
    assert code == 1

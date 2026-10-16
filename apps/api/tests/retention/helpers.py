"""Builders for retention tests: runs that ended at a chosen time, with data in every backend."""

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from abb_api.artifacts.service import storage_key
from abb_api.artifacts.store import LocalFsArtifactStore
from abb_api.db import tables as t
from tests.ingest_helpers import Tenant

# A fixed "now": retention tests never read the wall clock.
NOW = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)


def days_ago(days: float, now: datetime = NOW) -> datetime:
    return now - timedelta(days=days)


@dataclass
class SeededRun:
    run_id: uuid.UUID
    events: int
    spans: int
    artifact_bytes: int
    artifacts: int
    other_rows: int  # run-bound rows counted by the trace tier besides events
    artifact_keys: list[str] = field(default_factory=list)
    artifact_ids: list[uuid.UUID] = field(default_factory=list)


@dataclass
class Seed:
    runs: list[SeededRun] = field(default_factory=list)


async def seed_run(
    engine: AsyncEngine,
    tenant: Tenant,
    *,
    ended: datetime,
    events: int = 3,
    spans: int = 2,
    artifact_sizes: tuple[int, ...] = (),
    status: str = "SUCCESS",
    completed: bool = True,
    project: str | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    jobs: int = 1,
    store: LocalFsArtifactStore | None = None,
    full: bool = False,
) -> SeededRun:
    """A run whose end is `ended` (server-side times agree), plus its derived and bound rows."""
    ws, project_id = (
        tenant.context.workspace_id,
        tenant.project_uuids[project or next(iter(tenant.project_uuids))],
    )
    run_id = uuid.uuid4()
    created = created_at or ended - timedelta(minutes=5)
    updated = updated_at or ended
    async with engine.begin() as conn:
        await conn.execute(
            insert(t.runs).values(
                workspace_id=ws,
                id=run_id,
                project_id=project_id,
                trace_id=uuid.uuid4(),
                status=status,
                started_at=created,
                completed_at=ended if completed else None,
                created_at=created,
                updated_at=updated,
            )
        )
        for n in range(events):
            await conn.execute(
                insert(t.events).values(
                    workspace_id=ws,
                    event_id=uuid.uuid4(),
                    project_id=project_id,
                    run_id=run_id,
                    trace_id=uuid.uuid4(),
                    agent_id="a",
                    event_type="tool.call.completed",
                    occurred_at=created + timedelta(seconds=n),
                    received_at=created + timedelta(seconds=n),
                    schema_version="1.0",
                    content_hash=b"x" * 32,
                )
            )
        for _ in range(spans):
            await conn.execute(
                insert(t.spans).values(
                    workspace_id=ws,
                    id=uuid.uuid4(),
                    run_id=run_id,
                    trace_id=uuid.uuid4(),
                    agent_slug="a",
                )
            )
        keys: list[str] = []
        ids: list[uuid.UUID] = []
        for size in artifact_sizes:
            artifact_id = uuid.uuid4()
            uri = "file://x"
            if store is not None:
                body = b"x" * size
                uri = storage_key(ws, artifact_id, hashlib.sha256(body).digest())
                await store.put(uri, body)
            keys.append(uri)
            ids.append(artifact_id)
            await conn.execute(
                insert(t.artifacts).values(
                    workspace_id=ws,
                    id=artifact_id,
                    run_id=run_id,
                    project_id=project_id,
                    artifact_type="stdout",
                    storage_uri=uri,
                    size_bytes=size,
                    created_at=created,
                )
            )
        if store is not None and artifact_sizes:
            await conn.execute(
                text(
                    "INSERT INTO project_artifact_usage (workspace_id, project_id, bytes_stored) "
                    "VALUES (:w, :p, :n) ON CONFLICT (workspace_id, project_id) "
                    "DO UPDATE SET bytes_stored = project_artifact_usage.bytes_stored + :n"
                ),
                {"w": ws, "p": project_id, "n": sum(artifact_sizes)},
            )
        for _ in range(jobs):
            await conn.execute(
                insert(t.outbox_jobs).values(
                    id=uuid.uuid4(),
                    job_type="summarize_run",
                    workspace_id=ws,
                    dedupe_key=f"{ws}:{run_id}",
                    payload={"run_id": str(run_id)},
                    status="done",
                    updated_at=NOW,
                )
            )
        extra = 0
        if full:
            extra = await _seed_derived(conn, ws, project_id, run_id, created)
    return SeededRun(
        run_id=run_id,
        events=events,
        spans=spans,
        artifact_bytes=sum(artifact_sizes),
        artifacts=len(artifact_sizes),
        other_rows=spans + jobs + extra,
        artifact_keys=keys,
        artifact_ids=ids,
    )


async def _seed_derived(
    conn: AsyncConnection,
    ws: uuid.UUID,
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    started: datetime,
) -> int:
    """One row in every other run-derived table; returns how many rows that is."""
    await conn.execute(
        insert(t.cost_calculations).values(
            workspace_id=ws, event_id=uuid.uuid4(), run_id=run_id, project_id=project_id,
            agent_slug="a", occurred_at=started, run_started_at=started, input_tokens=1,
            output_tokens=1, cached_input_tokens=0, source="estimated",
            estimated_total=Decimal("0.5"),
        )
    )  # fmt: skip
    await conn.execute(
        insert(t.findings).values(
            workspace_id=ws, id=uuid.uuid4(), run_id=run_id, project_id=project_id,
            run_started_at=started, detector="d", detector_version="1", finding_type="f",
            kind="reliability", severity="warning", confidence=0.5, evidence_event_ids=[],
            fingerprint=uuid.uuid4().hex + uuid.uuid4().hex, evidence={},
        )
    )  # fmt: skip
    await conn.execute(
        insert(t.run_findings).values(
            workspace_id=ws, run_id=run_id, project_id=project_id, detectors_version=1,
            summary_version_seen=1, ordering_mode_seen="sequence", computed_at=started,
            event_count_seen=1, truncated=False, finding_count=1,
        )
    )  # fmt: skip
    await conn.execute(
        insert(t.run_search).values(
            workspace_id=ws, run_id=run_id, project_id=project_id, started_at=started,
            summary_version=1, document_hash=b"h",
        )
    )  # fmt: skip
    await conn.execute(
        insert(t.evaluations).values(
            workspace_id=ws, id=uuid.uuid4(), run_id=run_id, evaluator_name="e",
            evaluator_version="1",
        )
    )  # fmt: skip
    return 5


async def add_audit(engine: AsyncEngine, workspace: uuid.UUID, age_days: float) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            insert(t.audit_log).values(
                workspace_id=workspace,
                actor_kind="system",
                actor_id="test",
                action="test.event",
                outcome="allowed",
                occurred_at=days_ago(age_days),
            )
        )


async def add_orphan_artifact(
    engine: AsyncEngine, tenant: Tenant, age_days: float, size: int
) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            insert(t.artifacts).values(
                workspace_id=tenant.context.workspace_id,
                id=uuid.uuid4(),
                run_id=uuid.uuid4(),  # no such run (it never arrived, KI-040)
                project_id=next(iter(tenant.project_uuids.values())),
                artifact_type="stdout",
                storage_uri="file://orphan",
                size_bytes=size,
                created_at=days_ago(age_days),
            )
        )


async def seed_world(engine: AsyncEngine, acme: Tenant, globex: Tenant) -> None:
    """Defaults 30/90/365/365 and the hand-computed figures in tests/retention/test_preview.py."""
    # (ended days ago; events; spans; artifact bytes; run-bound jobs)
    await seed_run(
        engine, acme, ended=days_ago(10), events=3, spans=2, artifact_sizes=(100, 200), jobs=1
    )
    await seed_run(
        engine, acme, ended=days_ago(40), events=4, spans=1, artifact_sizes=(1000,), jobs=2
    )
    await seed_run(
        engine, acme, ended=days_ago(100), events=5, spans=3, artifact_sizes=(10,), jobs=1
    )
    await seed_run(engine, acme, ended=days_ago(400), events=2, spans=2, jobs=1)
    # Exactly 30 days: not yet expired for artifacts. One second more: expired.
    await seed_run(engine, acme, ended=days_ago(30), events=1, spans=0, artifact_sizes=(5,), jobs=0)
    await seed_run(
        engine, acme, ended=NOW - timedelta(days=30, seconds=1), events=1, spans=0,
        artifact_sizes=(7,), jobs=0,
    )  # fmt: skip
    await add_orphan_artifact(engine, acme, 31, 50)  # no run: ages by its own upload time
    await add_orphan_artifact(engine, acme, 29, 60)
    for age in (400, 366, 365, 364):  # 365 exactly: kept
        await add_audit(engine, acme.context.workspace_id, age)
    # Another tenant with very old data: must never show up in acme's figures.
    await seed_run(engine, globex, ended=days_ago(900), events=9, spans=9, artifact_sizes=(9999,))
    await add_audit(engine, globex.context.workspace_id, 900)

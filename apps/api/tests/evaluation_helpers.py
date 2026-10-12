"""Seed a summarised run, evaluators and pending evaluations without going through the API."""

import uuid
from typing import Any

from abb_event_schema.ids import IdKind, to_uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.db import tables as t
from abb_api.evaluations.repository import (
    EvaluationRepository,
    EvaluatorRecord,
    EvaluatorRepository,
)
from abb_api.ids import new_uuid
from abb_api.jobs.outbox import EVALUATE_RUN, OutboxRepository, evaluate_key
from abb_api.jobs.worker import Worker
from tests.ingest_helpers import Tenant, build_event, make_run_ids
from tests.test_summarizer import drain, ingest


async def seeded_run(
    engine: AsyncEngine,
    tenant: Tenant,
    worker: Worker,
    specs: list[tuple[str, dict[str, Any]]] | None = None,
    *,
    terminal: str = "success",
    project: str = "p",
) -> uuid.UUID:
    """A summarised run: started, the given events, then completed with `terminal` status."""
    run = make_run_ids()
    body = specs if specs is not None else [("tool.call.completed", {"tool.name": "github"})]
    events = [
        build_event(tenant, run, n=n, event_type=kind, attributes=attrs, project=project)
        for n, (kind, attrs) in enumerate(
            [("run.started", {}), *body, ("run.completed", {})], start=1
        )
    ]
    events[-1] = build_event(
        tenant, run, n=len(events), event_type="run.completed", attributes={},
        status=terminal, project=project,
    )  # fmt: skip
    await ingest(engine, tenant, events)
    await drain(worker)
    return to_uuid(run["run_id"])


async def add_evaluator(
    engine: AsyncEngine,
    tenant: Tenant,
    implementation: str = "run_succeeded",
    config: dict[str, Any] | None = None,
    *,
    name: str | None = None,
    kind: str = "deterministic",
    project: str | None = None,
) -> EvaluatorRecord:
    async with engine.begin() as conn:
        return await EvaluatorRepository(conn, tenant.context).create(
            new_uuid(IdKind.EVALUATOR),
            project_id=tenant.project_uuids[project] if project else None,
            name=name or f"ev-{uuid.uuid4().hex[:10]}",
            kind=kind,
            implementation=implementation,
            version=1,
            config=config or {},
            description=None,
            created_by=None,
        )


async def trigger(
    engine: AsyncEngine, tenant: Tenant, run_id: uuid.UUID, *evaluators: EvaluatorRecord
) -> list[uuid.UUID]:
    """Pending rows for the evaluators and one coalesced `evaluate_run` job, in one transaction."""
    ids = [new_uuid(IdKind.EVALUATION) for _ in evaluators]
    async with engine.begin() as conn:
        repo = EvaluationRepository(conn, tenant.context)
        for evaluation_id, evaluator in zip(ids, evaluators, strict=True):
            await repo.insert_pending(
                evaluation_id, run_id=run_id, evaluator=evaluator, requested_by=None
            )
        await OutboxRepository(conn, tenant.context).enqueue(
            job_type=EVALUATE_RUN,
            dedupe_key=evaluate_key(tenant.context.workspace_id, run_id),
            payload={"run_id": str(run_id)},
        )
    return ids


async def evaluation_rows(engine: AsyncEngine, run_id: uuid.UUID) -> list[Any]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            select(t.evaluations)
            .where(t.evaluations.c.run_id == run_id)
            .order_by(t.evaluations.c.created_at, t.evaluations.c.id)
        )
        return list(rows)


async def jobs_of(engine: AsyncEngine, job_type: str) -> list[Any]:
    async with engine.connect() as conn:
        rows = await conn.execute(select(t.outbox_jobs).where(t.outbox_jobs.c.job_type == job_type))
        return list(rows)

"""Persistence of evaluators and evaluations: every method is bound to one tenant (INV-3).

A terminal evaluation is never updated (INV-1): the transitions below all carry a state predicate
and the database refuses anything else (migration 0070). Count-then-insert bounds run under
advisory locks keyed by workspace or run, never under the run row lock the summarizer holds.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.db import tables as t
from abb_api.tenancy import TenantContext


@dataclass(frozen=True)
class EvaluatorRecord:
    id: uuid.UUID
    project_id: uuid.UUID | None
    name: str
    kind: str
    implementation: str
    version: int
    config: dict[str, Any]
    description: str | None
    created_by: uuid.UUID | None
    created_at: datetime
    archived_at: datetime | None


_E = t.evaluators.c
_COLUMNS = (
    _E.id, _E.project_id, _E.name, _E.kind, _E.implementation, _E.version, _E.config,
    _E.description, _E.created_by, _E.created_at, _E.archived_at,
)  # fmt: skip


def _record(row: Any) -> EvaluatorRecord:
    return EvaluatorRecord(**{c.name: getattr(row, c.name) for c in _COLUMNS})


class EvaluatorRepository:
    def __init__(
        self, conn: AsyncConnection, tenant: TenantContext, project_scope: uuid.UUID | None = None
    ) -> None:
        self._conn = conn
        self._tenant = tenant
        self._scope = project_scope  # a project-bound key sees its project's and workspace-wide

    def _base(self) -> Any:
        statement = select(*_COLUMNS).where(_E.workspace_id == self._tenant.workspace_id)
        if self._scope is not None:
            statement = statement.where((_E.project_id.is_(None)) | (_E.project_id == self._scope))
        return statement

    async def lock_for_create(self) -> None:
        """Serialise the count-then-insert of the workspace's evaluator bound (D10)."""
        key = f"ws:{self._tenant.workspace_id}:evaluators"
        await self._conn.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0))))

    async def count_active(self) -> int:
        return int(
            (
                await self._conn.execute(
                    select(func.count()).where(
                        _E.workspace_id == self._tenant.workspace_id, _E.archived_at.is_(None)
                    )
                )
            ).scalar_one()
        )

    async def create(
        self,
        evaluator_id: uuid.UUID,
        *,
        project_id: uuid.UUID | None,
        name: str,
        kind: str,
        implementation: str,
        version: int,
        config: dict[str, Any],
        description: str | None,
        created_by: uuid.UUID | None,
    ) -> EvaluatorRecord:
        await self._conn.execute(
            t.evaluators.insert().values(
                workspace_id=self._tenant.workspace_id,
                id=evaluator_id,
                project_id=project_id,
                name=name,
                kind=kind,
                implementation=implementation,
                version=version,
                config=config,
                description=description,
                created_by=created_by,
            )
        )
        created = await self.get(evaluator_id)
        assert created is not None
        return created

    async def get(self, evaluator_id: uuid.UUID) -> EvaluatorRecord | None:
        row = (await self._conn.execute(self._base().where(_E.id == evaluator_id))).first()
        return _record(row) if row else None

    async def list(self, *, include_archived: bool, limit: int) -> list[EvaluatorRecord]:
        statement = self._base()
        if not include_archived:
            statement = statement.where(_E.archived_at.is_(None))
        rows = await self._conn.execute(statement.order_by(_E.created_at, _E.id).limit(limit))
        return [_record(r) for r in rows]

    async def archive(self, evaluator_id: uuid.UUID) -> bool:
        """Archive (idempotent: the first time wins). False when the evaluator is not visible."""
        if await self.get(evaluator_id) is None:
            return False
        await self._conn.execute(
            update(t.evaluators)
            .where(
                _E.workspace_id == self._tenant.workspace_id,
                _E.id == evaluator_id,
                _E.archived_at.is_(None),
            )
            .values(archived_at=func.now())
        )
        return True


@dataclass(frozen=True)
class PendingEvaluation:
    id: uuid.UUID
    evaluator_id: uuid.UUID | None
    evaluator_name: str
    evaluator_version: str
    kind: str
    implementation: str | None
    config_snapshot: dict[str, Any]


class EvaluationRepository:
    """Evaluation rows. Transitions carry a state predicate: pending -> running -> terminal."""

    def __init__(self, conn: AsyncConnection, tenant: TenantContext) -> None:
        self._conn = conn
        self._tenant = tenant

    def savepoint(self) -> Any:
        """A nested transaction around one write, so a refused value rolls back only that write."""
        return self._conn.begin_nested()

    def _mine(self, evaluation_id: uuid.UUID) -> tuple[Any, ...]:
        c = t.evaluations.c
        return (c.workspace_id == self._tenant.workspace_id, c.id == evaluation_id)

    async def insert_pending(
        self,
        evaluation_id: uuid.UUID,
        *,
        run_id: uuid.UUID,
        evaluator: EvaluatorRecord,
        requested_by: uuid.UUID | None,
    ) -> None:
        """A new `pending` row holding a snapshot of the evaluator (name, version, config)."""
        await self._conn.execute(
            t.evaluations.insert().values(
                workspace_id=self._tenant.workspace_id,
                id=evaluation_id,
                run_id=run_id,
                evaluator_id=evaluator.id,
                evaluator_name=evaluator.name,
                evaluator_version=str(evaluator.version),
                kind=evaluator.kind,
                implementation=evaluator.implementation,
                config_snapshot=evaluator.config,
                state="pending",
                requested_by=requested_by,
            )
        )

    async def claim_pending(self, run_id: uuid.UUID, limit: int) -> list[PendingEvaluation]:
        """The run's pending rows, oldest first, locked so a concurrent handler skips them."""
        c = t.evaluations.c
        rows = await self._conn.execute(
            select(
                c.id, c.evaluator_id, c.evaluator_name, c.evaluator_version, c.kind,
                c.implementation, c.config_snapshot,
            )
            .where(
                c.workspace_id == self._tenant.workspace_id,
                c.run_id == run_id,
                c.state == "pending",
            )
            .order_by(c.created_at, c.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )  # fmt: skip
        return [PendingEvaluation(**r._mapping) for r in rows]

    async def count_pending(self, run_id: uuid.UUID) -> int:
        c = t.evaluations.c
        return int(
            (
                await self._conn.execute(
                    select(func.count()).where(
                        c.workspace_id == self._tenant.workspace_id,
                        c.run_id == run_id,
                        c.state == "pending",
                    )
                )
            ).scalar_one()
        )

    async def start(self, evaluation_id: uuid.UUID) -> bool:
        """pending -> running. False when the row is not pending (already handled)."""
        c = t.evaluations.c
        result = await self._conn.execute(
            update(t.evaluations)
            .where(*self._mine(evaluation_id), c.state == "pending")
            .values(state="running", started_at=func.now())
        )
        return bool(result.rowcount == 1)

    async def complete(
        self,
        evaluation_id: uuid.UUID,
        *,
        score: float | None,
        label: str,
        explanation: str,
        metrics: dict[str, Any],
        evidence_event_ids: list[str],
        metadata: dict[str, Any],
    ) -> bool:
        c = t.evaluations.c
        result = await self._conn.execute(
            update(t.evaluations)
            .where(*self._mine(evaluation_id), c.state == "running")
            .values(
                state="completed", score=score, label=label, explanation=explanation,
                metrics=metrics, evidence_event_ids=evidence_event_ids, metadata=metadata,
                completed_at=func.now(),
            )
        )  # fmt: skip
        return bool(result.rowcount == 1)

    async def fail(self, evaluation_id: uuid.UUID, *, code: str, error: str) -> bool:
        """pending|running -> failed: a fixed code and a templated message (never `str(exc)`)."""
        c = t.evaluations.c
        result = await self._conn.execute(
            update(t.evaluations)
            .where(*self._mine(evaluation_id), c.state.in_(("pending", "running")))
            .values(
                state="failed", error_code=code, error=error[:500], completed_at=func.now(),
            )
        )  # fmt: skip
        return bool(result.rowcount == 1)

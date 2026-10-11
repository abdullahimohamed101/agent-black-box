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

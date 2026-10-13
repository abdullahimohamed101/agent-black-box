"""The approvals store (INV-3, INV-7, ADR-141).

`ApprovalStore` is the contract; `PgApprovalStore` implements it on `approvals`. Every statement
carries the workspace. Every transition is ONE statement that names the state it expects
(`... WHERE state = 'REQUESTED' ... RETURNING`), so a concurrent transition has exactly one winner
and the loser sees no row; nothing reads then writes. The database repeats the rules (CHECKs and
the `approvals_guard` trigger, migration 0093), so these statements are the only way, not the only
guard. Times are supplied by the caller (the application clock), never read from the database,
so the whole machine is testable with an injected clock.
"""

import uuid
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.approvals.model import (
    APPROVED,
    CANCELLED,
    DENIED,
    EXPIRED,
    REQUESTED,
    Approval,
    NewApproval,
)
from abb_api.db import tables as t
from abb_api.tenancy import TenantContext

_C = t.approvals.c


def _approval(row: Any) -> Approval:
    return Approval(**{name: getattr(row, name) for name in Approval.__dataclass_fields__})


def _open() -> Any:
    return or_(_C.state == REQUESTED, and_(_C.state == APPROVED, _C.consumed_at.is_(None)))


class ApprovalStore(Protocol):
    async def lock_intent(self, run_id: uuid.UUID, key_id: str, intent_hash: str) -> None: ...

    async def find_open(
        self, run_id: uuid.UUID, key_id: str, intent_hash: str, now: datetime
    ) -> Approval | None: ...

    async def insert(self, new: NewApproval) -> Approval: ...

    async def get(
        self,
        approval_id: uuid.UUID,
        *,
        project_id: uuid.UUID | None = None,
        key_id: str | None = None,
    ) -> Approval | None: ...

    async def listing(
        self,
        *,
        pending_at: datetime | None,
        decided: bool,
        project_id: uuid.UUID | None,
        limit: int,
        before: tuple[datetime, uuid.UUID] | None,
    ) -> list[Approval]: ...

    async def pending_count(self, now: datetime, project_id: uuid.UUID | None) -> int: ...

    async def open_counts(self, key_id: str, now: datetime) -> tuple[int, int]: ...

    async def decide(
        self,
        approval_id: uuid.UUID,
        *,
        state: str,
        user_id: uuid.UUID,
        comment: str | None,
        now: datetime,
    ) -> Approval | None: ...

    async def cancel(
        self, approval_id: uuid.UUID, *, project_id: uuid.UUID, key_id: str, now: datetime
    ) -> Approval | None: ...

    async def consume(
        self,
        approval_id: uuid.UUID,
        *,
        project_id: uuid.UUID,
        key_id: str,
        intent_hash: str,
        run_id: uuid.UUID,
        now: datetime,
    ) -> Approval | None: ...

    async def expire(self, approval_id: uuid.UUID, now: datetime) -> Approval | None: ...

    async def expire_due(
        self, now: datetime, *, limit: int, run_id: uuid.UUID | None = None
    ) -> list[Approval]: ...

    async def run_trace(self, run_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID] | None: ...


class PgApprovalStore:
    def __init__(self, conn: AsyncConnection, tenant: TenantContext) -> None:
        self._conn = conn
        self._tenant = tenant

    def _mine(self, approval_id: uuid.UUID) -> list[Any]:
        return [_C.workspace_id == self._tenant.workspace_id, _C.id == approval_id]

    async def lock_intent(self, run_id: uuid.UUID, key_id: str, intent_hash: str) -> None:
        """Serialise creators of the same open approval for the rest of the transaction (the
        partial unique index is the backstop)."""
        name = f"ws:{self._tenant.workspace_id}:approval:{run_id}:{key_id}:{intent_hash}"
        await self._conn.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(name, 0))))

    async def find_open(
        self, run_id: uuid.UUID, key_id: str, intent_hash: str, now: datetime
    ) -> Approval | None:
        row = (
            await self._conn.execute(
                select(t.approvals).where(
                    _C.workspace_id == self._tenant.workspace_id,
                    _C.run_id == run_id,
                    _C.requested_by_key_id == key_id,
                    _C.intent_hash == intent_hash,
                    _open(),
                    _C.expires_at > now,
                )
            )
        ).first()
        return _approval(row) if row else None

    async def insert(self, new: NewApproval) -> Approval:
        values = {name: getattr(new, name) for name in NewApproval.__dataclass_fields__}
        row = (
            await self._conn.execute(
                t.approvals.insert()
                .values(workspace_id=self._tenant.workspace_id, state=REQUESTED, **values)
                .returning(t.approvals)
            )
        ).one()
        return _approval(row)

    async def get(
        self,
        approval_id: uuid.UUID,
        *,
        project_id: uuid.UUID | None = None,
        key_id: str | None = None,
    ) -> Approval | None:
        """One approval of this workspace. With `project_id` and `key_id` (an agent's view) it is
        also confined to that project and to the key that asked: anything else is None."""
        statement = select(t.approvals).where(*self._mine(approval_id))
        if project_id is not None:
            statement = statement.where(_C.project_id == project_id)
        if key_id is not None:
            statement = statement.where(_C.requested_by_key_id == key_id)
        row = (await self._conn.execute(statement)).first()
        return _approval(row) if row else None

    async def listing(
        self,
        *,
        pending_at: datetime | None,
        decided: bool,
        project_id: uuid.UUID | None,
        limit: int,
        before: tuple[datetime, uuid.UUID] | None,
    ) -> list[Approval]:
        """Newest first. `pending_at` set: only requests that are still open at that instant.
        `decided`: everything that is not a pending request (a request past its time counts as
        decided; the expiry sweep will mark it)."""
        statement = select(t.approvals).where(_C.workspace_id == self._tenant.workspace_id)
        if pending_at is not None:
            statement = statement.where(_C.state == REQUESTED, _C.expires_at > pending_at)
        if decided:
            statement = statement.where(_C.state != REQUESTED)
        if project_id is not None:
            statement = statement.where(_C.project_id == project_id)
        if before is not None:
            statement = statement.where(
                or_(
                    _C.requested_at < before[0],
                    and_(_C.requested_at == before[0], _C.id < before[1]),
                )
            )
        rows = await self._conn.execute(
            statement.order_by(_C.requested_at.desc(), _C.id.desc()).limit(limit)
        )
        return [_approval(r) for r in rows]

    async def pending_count(self, now: datetime, project_id: uuid.UUID | None) -> int:
        statement = select(func.count()).where(
            _C.workspace_id == self._tenant.workspace_id,
            _C.state == REQUESTED,
            _C.expires_at > now,
        )
        if project_id is not None:
            statement = statement.where(_C.project_id == project_id)
        return int((await self._conn.execute(statement)).scalar_one())

    async def open_counts(self, key_id: str, now: datetime) -> tuple[int, int]:
        """Requests still waiting: `(from this key, in the whole workspace)`."""
        base = select(func.count()).where(
            _C.workspace_id == self._tenant.workspace_id,
            _C.state == REQUESTED,
            _C.expires_at > now,
        )
        per_key = (
            await self._conn.execute(base.where(_C.requested_by_key_id == key_id))
        ).scalar_one()
        total = (await self._conn.execute(base)).scalar_one()
        return int(per_key), int(total)

    async def decide(
        self,
        approval_id: uuid.UUID,
        *,
        state: str,
        user_id: uuid.UUID,
        comment: str | None,
        now: datetime,
    ) -> Approval | None:
        assert state in (APPROVED, DENIED)
        row = (
            await self._conn.execute(
                update(t.approvals)
                .where(*self._mine(approval_id), _C.state == REQUESTED, _C.expires_at > now)
                .values(
                    state=state,
                    decided_by_user_id=user_id,
                    decided_at=now,
                    decision_comment=comment,
                )
                .returning(t.approvals)
            )
        ).first()
        return _approval(row) if row else None

    async def cancel(
        self, approval_id: uuid.UUID, *, project_id: uuid.UUID, key_id: str, now: datetime
    ) -> Approval | None:
        row = (
            await self._conn.execute(
                update(t.approvals)
                .where(
                    *self._mine(approval_id),
                    _C.state == REQUESTED,
                    _C.project_id == project_id,
                    _C.requested_by_key_id == key_id,
                    _C.expires_at > now,
                )
                .values(state=CANCELLED, closed_at=now)
                .returning(t.approvals)
            )
        ).first()
        return _approval(row) if row else None

    async def consume(
        self,
        approval_id: uuid.UUID,
        *,
        project_id: uuid.UUID,
        key_id: str,
        intent_hash: str,
        run_id: uuid.UUID,
        now: datetime,
    ) -> Approval | None:
        """The one statement that spends an approval: it succeeds for exactly one caller, only
        while the approval is approved, unspent, unexpired, the caller's own, and bound to this
        very intent (the hash is compared again here, next to the write)."""
        row = (
            await self._conn.execute(
                update(t.approvals)
                .where(
                    *self._mine(approval_id),
                    _C.state == APPROVED,
                    _C.consumed_at.is_(None),
                    _C.project_id == project_id,
                    _C.requested_by_key_id == key_id,
                    _C.intent_hash == intent_hash,
                    _C.run_id == run_id,
                    _C.expires_at > now,
                )
                .values(consumed_at=now, consumed_run_id=run_id)
                .returning(t.approvals)
            )
        ).first()
        return _approval(row) if row else None

    async def expire(self, approval_id: uuid.UUID, now: datetime) -> Approval | None:
        row = (
            await self._conn.execute(
                update(t.approvals)
                .where(*self._mine(approval_id), _open(), _C.expires_at <= now)
                .values(state=EXPIRED, closed_at=now)
                .returning(t.approvals)
            )
        ).first()
        return _approval(row) if row else None

    async def expire_due(
        self, now: datetime, *, limit: int, run_id: uuid.UUID | None = None
    ) -> list[Approval]:
        """Mark up to `limit` open approvals whose time has passed (oldest first). Rows another
        sweeper holds are skipped, so concurrent sweeps never both expire one."""
        due = (
            select(_C.id)
            .where(_C.workspace_id == self._tenant.workspace_id, _open(), _C.expires_at <= now)
            .order_by(_C.expires_at, _C.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        if run_id is not None:
            due = due.where(_C.run_id == run_id)
        # Two statements, not one with a subquery: the planner may re-run a LIMIT ... FOR UPDATE
        # subquery and update more rows than asked. The rows are locked until the transaction ends.
        ids = [r.id for r in await self._conn.execute(due)]
        if not ids:
            return []
        rows = await self._conn.execute(
            update(t.approvals)
            .where(
                _C.workspace_id == self._tenant.workspace_id,
                _C.id.in_(ids),
                _open(),
                _C.expires_at <= now,
            )
            .values(state=EXPIRED, closed_at=now)
            .returning(t.approvals)
        )
        return sorted((_approval(r) for r in rows), key=lambda a: (a.expires_at, a.id))

    async def run_trace(self, run_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID] | None:
        """`(project_id, trace_id)` of a run of this workspace, if it exists yet."""
        row = (
            await self._conn.execute(
                select(t.runs.c.project_id, t.runs.c.trace_id).where(
                    t.runs.c.workspace_id == self._tenant.workspace_id, t.runs.c.id == run_id
                )
            )
        ).first()
        return (row.project_id, row.trace_id) if row else None

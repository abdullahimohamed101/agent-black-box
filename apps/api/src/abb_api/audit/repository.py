"""Append to, and read, the audit log. Every statement carries the workspace (INV-3).

Writes never run inside the request's own transaction: `record_audit` opens a short transaction of
its own after the action committed, and a failed write is logged and swallowed, so an audit problem
can never turn a 403 into a 500 or undo an action (D11). The CLI appends on its own connection.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from abb_api.core.request_context import get_request_id
from abb_api.db import tables as t
from abb_api.tenancy import TenantContext

logger = logging.getLogger(__name__)

ActorKind = Literal["user", "api_key", "cli"]
Outcome = Literal["allowed", "denied"]

MAX_DETAIL_VALUE_LENGTH = 200
MAX_DETAIL_ITEMS = 20
# Keys that name secrets. Audit rows are read by many roles and exported, so they never hold these
# (INV-5); the check is on the key because values are free text.
_SECRET_KEY_PARTS = ("token", "secret", "cookie", "authorization", "password", "credential")
_SECRET_KEYS = frozenset({"code", "state", "nonce", "verifier"})


def clean_details(details: dict[str, Any]) -> dict[str, Any]:
    """A bounded, secret-free JSON object, or ValueError (a bug in the caller, caught by tests)."""
    if len(details) > MAX_DETAIL_ITEMS:
        raise ValueError("too many audit details")
    cleaned: dict[str, Any] = {}
    for key, value in details.items():
        lowered = key.lower()
        if lowered in _SECRET_KEYS or any(part in lowered for part in _SECRET_KEY_PARTS):
            raise ValueError(f"audit details may not carry {key!r}")
        cleaned[key] = _clean_value(key, value)
    return cleaned


def _clean_value(key: str, value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        if len(value) > MAX_DETAIL_VALUE_LENGTH:
            raise ValueError(f"audit detail {key!r} is too long")
        return value
    if isinstance(value, list | tuple) and len(value) <= MAX_DETAIL_ITEMS:
        return [_clean_value(key, item) for item in value]
    raise ValueError(f"audit detail {key!r} has an unsupported type")


@dataclass(frozen=True)
class AuditEntry:
    actor_kind: ActorKind
    actor_id: str
    action: str
    outcome: Outcome = "allowed"
    resource_kind: str | None = None
    resource_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None


@dataclass(frozen=True)
class AuditRow:
    id: int
    actor_kind: str
    actor_id: str
    action: str
    resource_kind: str | None
    resource_id: str | None
    outcome: str
    details: dict[str, Any]
    request_id: str | None
    occurred_at: datetime


class AuditRepository:
    def __init__(self, conn: AsyncConnection, tenant: TenantContext) -> None:
        self._conn = conn
        self._tenant = tenant

    async def append(self, entry: AuditEntry) -> None:
        await self._conn.execute(
            insert(t.audit_log).values(
                workspace_id=self._tenant.workspace_id,
                actor_kind=entry.actor_kind,
                actor_id=entry.actor_id,
                action=entry.action,
                resource_kind=entry.resource_kind,
                resource_id=entry.resource_id,
                outcome=entry.outcome,
                details=clean_details(entry.details),
                request_id=entry.request_id or get_request_id(),
            )
        )

    async def recent(self, limit: int = 100) -> list[AuditRow]:
        """Newest first. Cursor paging and `since` arrive with `GET /v1/audit`."""
        rows = await self._conn.execute(
            select(*(c for c in t.audit_log.c if c.name != "workspace_id"))
            .where(t.audit_log.c.workspace_id == self._tenant.workspace_id)
            .order_by(t.audit_log.c.id.desc())
            .limit(limit)
        )
        return [AuditRow(**r._asdict()) for r in rows]


async def record_audit(engine: AsyncEngine, workspace_id: uuid.UUID, entry: AuditEntry) -> bool:
    """Append in a transaction of its own; never raises. False when the row could not be written."""
    try:
        async with engine.begin() as conn:
            await AuditRepository(conn, TenantContext(workspace_id)).append(entry)
    except Exception as exc:
        # Only the type: the message may quote the row.
        logger.warning(
            "audit write failed", extra={"error_type": type(exc).__name__, "action": entry.action}
        )
        return False
    return True

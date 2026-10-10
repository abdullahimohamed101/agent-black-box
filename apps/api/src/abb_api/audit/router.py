"""`GET /v1/audit`: the workspace's administrative history, newest first (D11, ADR-062).

People only: API keys hold no `audit.read`. Reads are not audited. Filters beyond `since` are
deliberately absent (Phase 15 review H-2); the page is keyset-paged on the row id.
"""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.audit.repository import AuditRepository, AuditRow
from abb_api.authz import actions
from abb_api.authz.dependencies import require
from abb_api.authz.principal import Principal
from abb_api.core.errors import AppError, ErrorCategory, ErrorEnvelope
from abb_api.runs.cursors import Cursor, as_int64, cursor_invalid, decode, encode

router = APIRouter(tags=["audit"])

Reader = Annotated[Principal, Depends(require(actions.AUDIT_READ))]

_ERRORS: dict[int | str, dict[str, Any]] = {
    code: {"description": text, "model": ErrorEnvelope}
    for code, text in {
        400: "The cursor is not valid.",
        401: "Missing or invalid credential.",
        403: "The caller's role may not read the audit log.",
        404: "Workspace not found, or the caller is not a member.",
        422: "A parameter is invalid.",
        503: "A dependency is unavailable; retry with backoff.",
    }.items()
}


class AuditEntryOut(BaseModel):
    id: str
    occurred_at: str
    actor_kind: str
    actor_id: str
    action: str
    outcome: str
    resource_kind: str | None
    resource_id: str | None
    details: dict[str, Any]
    request_id: str | None


class AuditPage(BaseModel):
    items: list[AuditEntryOut]
    next_cursor: str | None


def _out(row: AuditRow) -> AuditEntryOut:
    return AuditEntryOut(
        id=str(row.id),
        occurred_at=row.occurred_at.astimezone(UTC).isoformat(),
        actor_kind=row.actor_kind,
        actor_id=row.actor_id,
        action=row.action,
        outcome=row.outcome,
        resource_kind=row.resource_kind,
        resource_id=row.resource_id,
        details=row.details,
        request_id=row.request_id,
    )


@router.get(
    "/v1/audit",
    response_model=AuditPage,
    responses=_ERRORS,
    summary="Page through the workspace audit log",
    description=(
        "Newest first. Page with `next_cursor`. `since` (an ISO timestamp with a timezone) keeps "
        "entries at or after that instant. Owners, admins and the security role only."
    ),
)
async def list_audit(
    request: Request,
    principal: Reader,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    since: Annotated[datetime | None, Query()] = None,
) -> AuditPage:
    if since is not None and since.tzinfo is None:
        raise AppError(
            "INVALID_SINCE",
            "`since` needs a timezone, for example 2026-01-01T00:00:00Z.",
            category=ErrorCategory.VALIDATION,
            status_code=422,
        )
    before_id: int | None = None
    if cursor is not None:
        decoded = decode(cursor, "audit", width=1)
        before_id = as_int64(decoded.key[0])
        if before_id < 1:
            raise cursor_invalid()
    engine: AsyncEngine = request.app.state.engine
    async with engine.connect() as conn:
        rows = await AuditRepository(conn, principal.tenant).page(
            limit=limit + 1, before_id=before_id, since=since
        )
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode(Cursor(kind="audit", key=[rows[-1].id])) if more else None
    return AuditPage(items=[_out(r) for r in rows], next_cursor=next_cursor)

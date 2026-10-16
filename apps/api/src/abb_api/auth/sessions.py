"""A person's own sessions: list them, revoke one, revoke the others (KI-068).

Everything here is scoped to the signed-in person by the session they present. A session id that
belongs to someone else, is unknown, or is already dead is the same 404, so the ids of other
people's sessions cannot be probed. Neither the cookie value nor its hash ever leaves the server.
"""

import logging
import uuid
from datetime import datetime

from abb_event_schema.ids import IdKind
from fastapi import Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.audit.repository import AuditEntry, record_audit
from abb_api.auth.repository import SessionRecord, SessionRepository
from abb_api.auth.service import SessionContext
from abb_api.core.errors import AppError, ErrorCategory
from abb_api.ids import public_id
from abb_api.ingestion.service import rate_limited
from abb_api.limits import AsyncRateLimiter
from abb_api.workspaces.repository import memberships_of

logger = logging.getLogger(__name__)

MAX_LISTED = 100  # a person with more working sessions than this still revokes them all at once
MAX_AUDITED_WORKSPACES = 20  # audit rows are per workspace: the person's workspaces, bounded


class SessionOut(BaseModel):
    id: str = Field(description="Opaque handle for this session. Not the cookie value.")
    device: str | None = Field(description='Coarse label such as "Firefox on Linux", or null.')
    created_at: str
    last_seen_at: str = Field(description="Accurate to about five minutes.")
    expires_at: str = Field(description="When it ends regardless of use.")
    idle_expires_at: str = Field(description="When it ends if it is not used before then.")
    current: bool = Field(description="The session making this request.")


class SessionList(BaseModel):
    items: list[SessionOut]


class RevokedSessions(BaseModel):
    revoked: int


def session_not_found() -> AppError:
    return AppError(
        "SESSION_NOT_FOUND",
        "Session not found.",
        category=ErrorCategory.NOT_FOUND,
        status_code=404,
    )


def current_session_conflict() -> AppError:
    return AppError(
        "CURRENT_SESSION",
        "This is the session you are using. Sign out to end it.",
        category=ErrorCategory.CONFLICT,
        status_code=409,
    )


def _out(record: SessionRecord, current: uuid.UUID) -> SessionOut:
    return SessionOut(
        id=str(record.id),
        device=record.device_hint,
        created_at=record.created_at.isoformat(),
        last_seen_at=record.last_seen_at.isoformat(),
        expires_at=record.expires_at.isoformat(),
        idle_expires_at=record.idle_expires_at.isoformat(),
        current=record.id == current,
    )


def _engine(request: Request) -> AsyncEngine:
    engine: AsyncEngine = request.app.state.engine
    return engine


def _now(request: Request) -> datetime:
    now: datetime = request.app.state.clock()
    return now


async def _throttle(request: Request, context: SessionContext) -> None:
    limiter: AsyncRateLimiter = request.app.state.session_limiter
    wait = await limiter.acquire(str(context.user.id), events=1, bytes_=0)
    if wait is not None:
        raise rate_limited(wait)


async def list_sessions(request: Request, context: SessionContext) -> SessionList:
    async with _engine(request).connect() as conn:
        found = await SessionRepository(conn).list_usable(
            context.user.id, _now(request), MAX_LISTED
        )
    return SessionList(items=[_out(s, context.session.id) for s in found])


def _revoked(request: Request, reason: str, count: int = 1) -> None:
    request.app.state.observability.catalogue.session_revocations_total.inc(
        reason, amount=float(count)
    )


async def revoke_session(request: Request, context: SessionContext, session_id: str) -> None:
    """Revoke one session of the caller; the one in use is refused (that is signing out)."""
    try:
        target = uuid.UUID(session_id)
    except ValueError:
        raise session_not_found() from None
    if str(target) != session_id:  # only the canonical spelling names a session
        raise session_not_found()
    if target == context.session.id:
        raise current_session_conflict()
    await _throttle(request, context)
    async with _engine(request).begin() as conn:
        if not await SessionRepository(conn).revoke_own(context.user.id, target, _now(request)):
            raise session_not_found()
    _revoked(request, "revoke_one")
    await _audit(request, context, "session.revoke", resource_id=str(target))


async def revoke_other_sessions(request: Request, context: SessionContext) -> RevokedSessions:
    await _throttle(request, context)
    async with _engine(request).begin() as conn:
        count = await SessionRepository(conn).revoke_others(
            context.user.id, context.session.id, _now(request)
        )
    if count:
        _revoked(request, "revoke_others", count)
        await _audit(request, context, "session.revoke_others", count=count)
    return RevokedSessions(revoked=count)


async def _audit(
    request: Request,
    context: SessionContext,
    action: str,
    *,
    resource_id: str | None = None,
    **details: int,
) -> None:
    """One row in each workspace the person belongs to (the audit log is per workspace).

    Details are counts and ids only. A person with no workspace yet leaves a log line only.
    """
    engine = _engine(request)
    actor = f"user:{public_id(IdKind.USER, context.user.id)}"
    try:
        async with engine.connect() as conn:
            workspaces = await memberships_of(conn, context.user.id)
    except Exception:  # an audit problem never undoes or fails the action (D11)
        logger.warning("session audit could not list workspaces", exc_info=True)
        return
    for membership in workspaces[:MAX_AUDITED_WORKSPACES]:
        await record_audit(
            engine,
            membership.workspace_id,
            AuditEntry(
                actor_kind="user",
                actor_id=actor,
                action=action,
                resource_kind="session",
                resource_id=resource_id,
                details=details,
            ),
        )
    logger.info(action.replace(".", " "), extra={"user": actor, **details})

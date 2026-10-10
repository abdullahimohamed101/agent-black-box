"""Turning a presented API key into a Principal (or one uniform 401)."""

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.auth.keys import parse_key, verify_secret
from abb_api.auth.repository import (
    ApiKeyLookup,
    SessionRecord,
    SessionRepository,
    StoredApiKey,
    UserRecord,
    UserRepository,
)
from abb_api.authz.matrix import role_actions, scope_actions
from abb_api.authz.principal import Principal
from abb_api.clock import Clock
from abb_api.core.errors import AppError, ErrorCategory
from abb_api.workspaces.repository import MembershipRepository

logger = logging.getLogger(__name__)


def invalid_key() -> AppError:
    # One response for unknown, malformed, wrong-secret, revoked and expired keys: the caller
    # learns nothing about which keys exist. The reason is logged server-side by the caller.
    return AppError(
        "API_KEY_INVALID",
        "The API key is missing, malformed, expired or revoked.",
        category=ErrorCategory.AUTHENTICATION,
        status_code=401,
    )


def _rejection_reason(
    well_formed: bool, stored: StoredApiKey | None, secret_ok: bool, now: datetime
) -> str | None:
    if not well_formed:
        return "malformed_or_missing"
    if stored is None:
        return "unknown_key"
    if not secret_ok:
        return "bad_secret"
    if stored.revoked_at is not None:
        return "revoked"
    if stored.expires_at is not None and stored.expires_at <= now:
        return "expired"
    return None


async def authenticate(conn: AsyncConnection, token: str | None, clock: Clock) -> Principal:
    parsed = parse_key(token) if token else None
    stored = await ApiKeyLookup(conn).find(parsed.key_id) if parsed else None
    # Always verify, even when there is nothing to match, to keep timing uniform.
    secret = parsed.secret if parsed else ""
    secret_ok = verify_secret(secret, stored.secret_hash if stored else None)
    now = clock()
    reason = _rejection_reason(parsed is not None, stored, secret_ok, now)
    if reason is not None or stored is None:
        # The caller gets one uniform 401; the real reason stays in our logs (never the secret).
        logger.info(
            "api key rejected",
            extra={"reason": reason, "key_id": parsed.key_id if parsed else None},
        )
        raise invalid_key()
    await ApiKeyLookup(conn).touch_last_used(stored, now)
    return Principal(
        kind="api_key",
        workspace_id=stored.workspace_id,
        project_id=stored.project_id,
        actions=scope_actions(stored.scopes),
        actor_id=f"key:{stored.key_id}",
    )


# ---------------------------------------------------------------- sessions

_SESSION_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")  # token_urlsafe(32)


def hash_session_token(token: str) -> bytes:
    """Sessions are stored and found by this hash; the cookie value itself is never kept."""
    return hashlib.sha256(token.encode("ascii")).digest()


def session_invalid() -> AppError:
    # One response for missing, malformed, unknown, expired and revoked sessions.
    return AppError(
        "SESSION_INVALID",
        "The session is missing, expired or revoked. Sign in again.",
        category=ErrorCategory.AUTHENTICATION,
        status_code=401,
    )


@dataclass(frozen=True)
class SessionContext:
    user: UserRecord
    session: SessionRecord


async def authenticate_session(
    conn: AsyncConnection, token: str | None, clock: Clock, idle: timedelta
) -> SessionContext:
    """Resolve a session cookie to its user, or raise the uniform 401."""
    if token is None or not _SESSION_TOKEN.fullmatch(token):
        logger.info("session rejected", extra={"reason": "malformed_or_missing"})
        raise session_invalid()
    sessions = SessionRepository(conn)
    found = await sessions.find_by_hash(hash_session_token(token))
    now = clock()
    if found is None or not found.usable(now):
        reason = "unknown" if found is None else ("revoked" if found.revoked_at else "expired")
        logger.info("session rejected", extra={"reason": reason})
        raise session_invalid()
    user = await UserRepository(conn).get(found.user_id)
    if user is None:  # cannot happen (foreign key); fail closed
        raise session_invalid()
    await sessions.slide(found, now=now, idle=idle)
    return SessionContext(user, found)


async def credential_still_grants(
    conn: AsyncConnection, principal: Principal, action: str, clock: Clock
) -> bool:
    """Whether the credential behind `principal` still exists and still grants `action` (KI-033).

    Open streams call this periodically instead of holding the answer from when they opened. It
    never slides a session or touches `last_used_at`: watching a stream is not activity. A key is
    found by its id (the secret is not kept), a session by its row id, and a person's role is read
    afresh, so a revocation, an expiry, a removal or a demotion all end the stream.
    """
    now = clock()
    if principal.kind == "api_key":
        stored = await ApiKeyLookup(conn).find(principal.actor_id.removeprefix("key:"))
        return (
            stored is not None
            and stored.workspace_id == principal.workspace_id
            and stored.project_id == principal.project_id
            and stored.is_active(now)
            and action in scope_actions(stored.scopes)
        )
    if principal.session_id is None or principal.user_id is None:
        return False
    try:
        session_id = uuid.UUID(principal.session_id)
    except ValueError:
        return False
    session = await SessionRepository(conn).find_by_id(session_id)
    if session is None or session.user_id != principal.user_id or not session.usable(now):
        return False
    role = await MembershipRepository(conn, principal.tenant).role_of(principal.user_id)
    return role is not None and action in role_actions(role)

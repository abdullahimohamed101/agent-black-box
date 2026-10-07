"""Turning a presented API key into a Principal (or one uniform 401)."""

from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.auth.keys import parse_key, verify_secret
from abb_api.auth.repository import ApiKeyLookup
from abb_api.clock import Clock
from abb_api.core.errors import AppError, ErrorCategory
from abb_api.tenancy import Principal


def invalid_key() -> AppError:
    # One response for unknown, malformed, wrong-secret, revoked and expired keys: the caller
    # learns nothing about which keys exist. The reason is logged server-side by the caller.
    return AppError(
        "API_KEY_INVALID",
        "The API key is missing, malformed, expired or revoked.",
        category=ErrorCategory.AUTHENTICATION,
        status_code=401,
    )


def insufficient_scope(required: str) -> AppError:
    return AppError(
        "INSUFFICIENT_SCOPE",
        f"This API key lacks the '{required}' scope.",
        category=ErrorCategory.AUTHORIZATION,
        status_code=403,
        details={"required_scope": required},
    )


async def authenticate(conn: AsyncConnection, token: str | None, clock: Clock) -> Principal:
    parsed = parse_key(token) if token else None
    stored = await ApiKeyLookup(conn).find(parsed.key_id) if parsed else None
    # Always verify, even when there is nothing to match, to keep timing uniform.
    secret = parsed.secret if parsed else ""
    secret_ok = verify_secret(secret, stored.secret_hash if stored else None)
    now = clock()
    if (
        not parsed
        or stored is None
        or not secret_ok
        or stored.revoked_at is not None
        or (stored.expires_at is not None and stored.expires_at <= now)
    ):
        raise invalid_key()
    await ApiKeyLookup(conn).touch_last_used(stored, now)
    return Principal(
        workspace_id=stored.workspace_id,
        project_id=stored.project_id,
        scopes=stored.scopes,
        key_id=stored.key_id,
    )


def require_scope(principal: Principal, scope: str) -> None:
    if not principal.has_scope(scope):
        raise insufficient_scope(scope)

"""The sign-in flow: start a login, finish it, end a session (ADR-060, spec §91)."""

import hashlib
import hmac
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import NoReturn

from abb_event_schema.ids import IdKind
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.auth.oidc import OidcClient, OidcError, OidcUnavailable, new_pkce
from abb_api.auth.repository import (
    LoginStateRepository,
    SessionRepository,
    UserRecord,
    UserRepository,
)
from abb_api.auth.service import hash_session_token
from abb_api.clock import Clock
from abb_api.core.config import Settings
from abb_api.core.domain import AlreadyExistsError
from abb_api.core.errors import AppError, ErrorCategory
from abb_api.ids import public_id

logger = logging.getLogger(__name__)

LOGIN_STATE_TTL = timedelta(minutes=10)
# Where a login may send the browser afterwards: the app's own routes and nothing else. No
# backslash, percent-escape, `@`, fragment or control character can match.
RETURN_TO = re.compile(
    r"/(?:(?:w|invite)(?:/[A-Za-z0-9._~-]{1,64}){0,6}/?(?:\?[A-Za-z0-9=&._~-]{0,256})?)?"
)
_STATE = re.compile(r"[A-Za-z0-9_-]{43}")


def login_failed(status: int = 400) -> AppError:
    # One public message for every way a login can fail; the reason goes to the log only.
    return AppError(
        "LOGIN_FAILED",
        "Sign-in failed. Start again.",
        category=ErrorCategory.AUTHENTICATION if status == 401 else ErrorCategory.VALIDATION,
        status_code=status,
    )


def auth_not_configured() -> AppError:
    return AppError(
        "AUTH_NOT_CONFIGURED",
        "Sign-in is not configured on this server.",
        category=ErrorCategory.DEPENDENCY,
        status_code=503,
    )


def return_to_invalid() -> AppError:
    return AppError(
        "RETURN_TO_INVALID",
        "return_to must be a path inside the application.",
        category=ErrorCategory.VALIDATION,
        status_code=422,
    )


def validated_return_to(value: str | None) -> str:
    if value is None:
        return "/"
    path = value.split("?", 1)[0]
    if (
        not value.isascii()
        or not RETURN_TO.fullmatch(value)
        or any(segment in (".", "..") for segment in path.split("/"))  # no dot-segments
    ):
        raise return_to_invalid()
    return value


def _hash(value: str) -> bytes:
    return hashlib.sha256(value.encode("ascii")).digest()


@dataclass(frozen=True)
class StartedLogin:
    authorize_url: str
    state: str


@dataclass(frozen=True)
class FinishedLogin:
    return_to: str
    session_token: str
    user_id: str


class LoginService:
    def __init__(
        self, engine: AsyncEngine, oidc: OidcClient, settings: Settings, clock: Clock
    ) -> None:
        self._engine = engine
        self._oidc = oidc
        self._settings = settings
        self._clock = clock

    async def start(self, return_to: str | None) -> StartedLogin:
        target = validated_return_to(return_to)
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        verifier, challenge = new_pkce()
        try:
            url = await self._oidc.authorize_url(state=state, nonce=nonce, code_challenge=challenge)
        except (OidcError, OidcUnavailable) as exc:
            logger.warning("login could not start", extra={"reason": _reason(exc)})
            raise _unavailable() from None
        async with self._engine.begin() as conn:
            await LoginStateRepository(conn).create(
                _hash(state),
                nonce=nonce,
                code_verifier=verifier,
                return_to=target,
                now=self._clock(),
                ttl=LOGIN_STATE_TTL,
            )
        return StartedLogin(url, state)

    async def finish(
        self, *, code: str | None, state: str | None, cookie_state: str | None, idp_error: bool
    ) -> FinishedLogin:
        # Login CSRF: the state in the URL must be the one this browser was given at the start.
        if (
            not state
            or not cookie_state
            or not _STATE.fullmatch(state)
            or not hmac.compare_digest(state.encode(), cookie_state.encode())
        ):
            self._fail("state_cookie_mismatch")
        now = self._clock()
        async with self._engine.begin() as conn:
            pending = await LoginStateRepository(conn).take(_hash(state), now)
        if pending is None:
            self._fail("state_unknown_or_expired")
        if idp_error:
            self._fail("idp_error")  # the provider's description is never echoed or logged
        if not code or len(code) > 2048:
            self._fail("code_missing")
        try:
            id_token = await self._oidc.exchange_code(code, pending.code_verifier)
            identity = await self._oidc.verify_id_token(id_token, nonce=pending.nonce)
        except OidcError as exc:
            self._fail(exc.reason, status=exc.status, kid=exc.kid)
        except OidcUnavailable as exc:
            logger.warning("login could not finish", extra={"reason": _reason(exc)})
            raise _unavailable() from None
        if not identity.email_verified:
            self._fail("email_not_verified", status=401)

        token = secrets.token_urlsafe(32)
        issuer = self._oidc.issuer
        async with self._engine.begin() as conn:
            users = UserRepository(conn)
            user = await self._resolve_user(
                users, issuer, identity.subject, identity.email, identity.name
            )
            if user is None:
                self._fail("identity_conflict", status=401)
            sessions = SessionRepository(conn)
            await sessions.purge(user.id, now)
            created = await sessions.create(
                user.id,
                hash_session_token(token),
                now=now,
                absolute=timedelta(hours=self._settings.session_absolute_hours),
                idle=timedelta(hours=self._settings.session_idle_hours),
            )
            await users.record_login(user.id, now)
        logger.info(
            "login succeeded",
            extra={"user": public_id(IdKind.USER, user.id), "session": str(created.id)[:8]},
        )
        return FinishedLogin(pending.return_to, token, public_id(IdKind.USER, user.id))

    async def _resolve_user(
        self, users: UserRepository, issuer: str, subject: str, email: str, name: str | None
    ) -> UserRecord | None:
        """The user for a verified identity, or None when the email belongs to someone else.

        The subject decides. A verified email may claim a user only while that user has no subject
        yet (pre-provisioned by the CLI or an invitation); it can never re-bind a linked one.
        """
        now = self._clock()
        known = await users.find_by_identity(issuer, subject)
        if known is not None:
            if known.email.lower() == email.lower():
                return known
            # The provider now vouches for another address: follow it, so a later invitation is
            # compared with the current verified email. A taken address changes nothing.
            if not await users.refresh_verified_email(known.id, email=email, verified_at=now):
                return None
            return await users.get(known.id)
        by_email = await users.find_by_email(email)
        if by_email is None:
            try:
                by_email = await users.create(email=email, name=name)
            except AlreadyExistsError:
                return None
        elif by_email.provider_subject is not None:
            return None
        if not await users.link_identity(
            by_email.id, provider=issuer, subject=subject, verified_at=now
        ):
            return None
        return await users.get(by_email.id)

    def _fail(self, reason: str, *, status: int = 400, kid: str | None = None) -> NoReturn:
        extra: dict[str, str] = {"reason": reason, "iss": self._oidc.issuer}
        if kid is not None:
            extra["kid"] = kid[:64]
        logger.warning("login failed", extra=extra)
        raise login_failed(status)


def _reason(exc: Exception) -> str:
    return exc.reason if isinstance(exc, OidcError) else "provider_unavailable"


def _unavailable() -> AppError:
    return AppError(
        "AUTH_UNAVAILABLE",
        "The identity provider is unavailable. Try again shortly.",
        category=ErrorCategory.DEPENDENCY,
        status_code=503,
        retryable=True,
    )

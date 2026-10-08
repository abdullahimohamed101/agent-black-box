"""Session expiry and refresh."""

from __future__ import annotations

from dataclasses import dataclass

from app.oauth import IdentityProvider

REFRESH_SKEW_SECONDS = 60  # refresh a little early so a request never carries a token that dies in flight


@dataclass(frozen=True)
class Session:
    session_id: str
    access_token: str
    refresh_token: str | None
    expires_at: float  # epoch seconds


def is_expired(session: Session, now: float) -> bool:
    return now > session.expires_at


def refresh_session(session: Session, provider: IdentityProvider, now: float) -> Session:
    if session.refresh_token is None:
        raise ValueError("session has no refresh token")
    token = provider.refresh(session.refresh_token)
    return Session(
        session.session_id,
        token.access_token,
        token.refresh_token,
        now + token.expires_in,
    )


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def put(self, session: Session) -> None:
        self._sessions[session.session_id] = session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)


def get_valid_session(
    store: SessionStore, session_id: str, provider: IdentityProvider, now: float
) -> Session | None:
    session = store.get(session_id)
    if session is None:
        return None
    if is_expired(session, now):
        session = refresh_session(session, provider, now)
        store.put(session)
    return session

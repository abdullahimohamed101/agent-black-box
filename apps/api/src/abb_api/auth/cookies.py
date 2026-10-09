"""Cookie names and attributes, decided in one place (ADR-060).

The browser only ever talks to the web app, so cookies are host-only for its origin. With https the
session cookie carries the `__Host-` prefix (browsers then insist on Secure, Path=/ and no Domain).
"""

from abb_api.core.config import Settings

LOGIN_COOKIE = "abb_login"
# The login cookie is scoped to the web app's auth routes, which relay the callback.
LOGIN_COOKIE_PATH = "/api/auth"
LOGIN_TTL_SECONDS = 600


def is_secure(settings: Settings) -> bool:
    return bool(settings.web_origin and settings.web_origin.lower().startswith("https://"))


def session_cookie_name(settings: Settings) -> str:
    return "__Host-abb_session" if is_secure(settings) else "abb_session"


def set_cookie(settings: Settings, name: str, value: str, *, path: str, max_age: int) -> str:
    parts = [f"{name}={value}", f"Max-Age={max_age}", f"Path={path}", "HttpOnly", "SameSite=Lax"]
    if is_secure(settings):
        parts.append("Secure")
    return "; ".join(parts)


def clear_cookie(settings: Settings, name: str, *, path: str) -> str:
    """Expire a cookie with the same attributes it was set with (a mismatch would not clear it)."""
    return set_cookie(settings, name, "", path=path, max_age=0)

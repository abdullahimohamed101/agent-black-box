"""A stand-in for the identity provider's token endpoint."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TokenResponse:
    access_token: str
    expires_in: int  # seconds
    refresh_token: str | None = None


class IdentityProvider:
    """Providers are allowed to omit `refresh_token` from a refresh response (RFC 6749 section 6)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def refresh(self, refresh_token: str) -> TokenResponse:
        self.calls.append(refresh_token)
        return TokenResponse(access_token=f"access-{len(self.calls)}", expires_in=3600)

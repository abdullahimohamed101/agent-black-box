"""Bearer-token check shared by the API's /metrics route and the worker's metrics server."""

import hashlib
import hmac

from pydantic import SecretStr

MIN_TOKEN_LENGTH = 24


class MetricsAuth:
    """Compares a presented ``Authorization`` header with the configured token in constant time.

    Both sides are hashed first, so neither the comparison time nor the length of the secret
    depends on what a caller sends. The token is never logged or rendered.
    """

    def __init__(self, token: SecretStr) -> None:
        self._digest = hashlib.sha256(token.get_secret_value().encode()).digest()

    def allows(self, authorization: str | None) -> bool:
        presented = ""
        if authorization:
            scheme, _, credential = authorization.partition(" ")
            if scheme.lower() == "bearer":
                presented = credential.strip()
        candidate = hashlib.sha256(presented.encode("utf-8", "replace")).digest()
        # An absent or empty token still goes through the same comparison, then fails below.
        matches = hmac.compare_digest(self._digest, candidate)
        return matches and bool(presented)

    def __repr__(self) -> str:  # pragma: no cover - keeps the digest out of tracebacks
        return "MetricsAuth()"

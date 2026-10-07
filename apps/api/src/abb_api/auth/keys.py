"""API key format, generation and verification (spec §92, plan D4).

    abb_live_<key_id>.<secret>

`key_id` is public and indexed (it identifies the row); `secret` is 32 random bytes, shown once.
Only sha256(secret) is stored. A fast hash is correct here: the secret already has 256 bits of
entropy, so there is nothing to brute-force that a slow hash would protect.
"""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

PREFIX = "abb_live_"
_KEY_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
_KEY_ID_LENGTH = 12
_KEY_RE = re.compile(r"abb_live_(?P<key_id>[a-z0-9]{12})\.(?P<secret>[A-Za-z0-9_-]{43})")

# Compared against when the key id is unknown, so "no such key" costs the same as "wrong secret".
_DUMMY_HASH = hashlib.sha256(b"abb-dummy-secret").digest()


@dataclass(frozen=True)
class GeneratedKey:
    key_id: str
    secret: str  # plaintext, returned to the caller exactly once
    secret_hash: bytes

    @property
    def token(self) -> str:
        return f"{PREFIX}{self.key_id}.{self.secret}"


@dataclass(frozen=True)
class ParsedKey:
    key_id: str
    secret: str


def hash_secret(secret: str) -> bytes:
    return hashlib.sha256(secret.encode("ascii")).digest()


def generate_key() -> GeneratedKey:
    key_id = "".join(secrets.choice(_KEY_ID_ALPHABET) for _ in range(_KEY_ID_LENGTH))
    secret = secrets.token_urlsafe(32)  # 43 url-safe characters
    return GeneratedKey(key_id=key_id, secret=secret, secret_hash=hash_secret(secret))


def parse_key(token: str) -> ParsedKey | None:
    match = _KEY_RE.fullmatch(token)
    if match is None:
        return None
    return ParsedKey(key_id=match.group("key_id"), secret=match.group("secret"))


def verify_secret(secret: str, stored_hash: bytes | None) -> bool:
    """Constant-time comparison. `stored_hash=None` (unknown key) still does a comparison."""
    expected = stored_hash if stored_hash is not None else _DUMMY_HASH
    matches = hmac.compare_digest(hash_secret(secret), expected)
    return matches and stored_hash is not None

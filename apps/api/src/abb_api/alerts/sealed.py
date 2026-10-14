"""Encryption of destination secrets at rest (ADR-150).

A destination's target (a webhook or Slack URL, or the recipient list) and a webhook's signing
secret are sealed with AES-256-GCM before they reach the database and opened only by the sender
(step 2) and the "send test" path. Nothing else decrypts: the API returns `masked_target`, which is
computed once at save.

Keys. The per-installation key material is a row of the append-only `server_secrets` table under
the name `alerts-encryption` (the Phase 14 mechanism, ADR-141 amendment 2): created on first use
under an advisory lock in a transaction of its own, shared by every API process and worker through
the database, never held in code or configuration. The AES key is derived from it with HKDF-SHA256
(a fixed `info`), so the same material is never used raw. Each ciphertext stores the id of the
`server_secrets` row it was sealed under (`key_id`); the newest row seals new data and any row
opens the data it sealed. Rotation therefore needs no re-encryption: INSERT a new row as the owner
role, restart the processes (they cache the current key), and destinations sealed under the old
row keep opening until they are saved again. Deleting or overwriting a key row is impossible for
the application role (migration 0094), so a lost row means lost secrets, never silently wrong ones.

Binding. The associated data is `(purpose, workspace_id, destination_id)`: a ciphertext copied to
another row, another workspace or another purpose (config <-> signing secret) fails to open.

Format: `0x01 || nonce(12) || ciphertext||tag`. A fresh random 96-bit nonce per seal; with the
number of seals this table can ever see, collisions are negligible (NIST SP 800-38D limit 2^32).
"""

import json
import secrets
import uuid
import weakref
from dataclasses import dataclass
from typing import Any, Final, Literal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

NAME: Final = "alerts-encryption"
_LOCK: Final = "abb.server-secret.alerts-encryption"
_INFO: Final = b"abb-api/alerts/destination-secrets/v1"
_VERSION: Final = b"\x01"
_NONCE_BYTES: Final = 12
Purpose = Literal["config", "signing"]

SIGNING_SECRET_PREFIX: Final = "whsec_"  # noqa: S105 - a format prefix, not a secret


class SealError(Exception):
    """A ciphertext could not be opened. The message is fixed text: it never quotes a value."""

    def __init__(self) -> None:
        super().__init__("a destination secret could not be decrypted")


@dataclass(frozen=True)
class Sealed:
    key_id: int
    ciphertext: bytes


class _Keys:
    def __init__(self) -> None:
        self.current: tuple[int, AESGCM] | None = None
        self.by_id: dict[int, AESGCM] = {}


_cache: weakref.WeakKeyDictionary[AsyncEngine, _Keys] = weakref.WeakKeyDictionary()


def _derive(material: bytes) -> AESGCM:
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO).derive(material)
    return AESGCM(key)


async def _newest(conn: AsyncConnection) -> tuple[int, bytes] | None:
    row = (
        await conn.execute(
            text("SELECT id, secret FROM server_secrets WHERE name = :n ORDER BY id DESC LIMIT 1"),
            {"n": NAME},
        )
    ).first()
    return (int(row.id), bytes(row.secret)) if row else None


async def _current(engine: AsyncEngine) -> tuple[int, AESGCM]:
    keys = _cache.setdefault(engine, _Keys())
    if keys.current is not None:
        return keys.current
    async with engine.begin() as conn:
        found = await _newest(conn)
        if found is None:
            await conn.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": _LOCK}
            )
            found = await _newest(conn)  # another process may have created it while we waited
            if found is None:
                await conn.execute(
                    text("INSERT INTO server_secrets (name, secret) VALUES (:n, :s)"),
                    {"n": NAME, "s": secrets.token_bytes(32)},
                )
                found = await _newest(conn)
    assert found is not None
    key_id, material = found
    keys.current = (key_id, _derive(material))
    keys.by_id[key_id] = keys.current[1]
    return keys.current


async def _by_id(engine: AsyncEngine, key_id: int) -> AESGCM:
    keys = _cache.setdefault(engine, _Keys())
    cached = keys.by_id.get(key_id)
    if cached is not None:
        return cached
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT secret FROM server_secrets WHERE id = :i AND name = :n"),
                {"i": key_id, "n": NAME},
            )
        ).first()
    if row is None:
        raise SealError
    keys.by_id[key_id] = _derive(bytes(row.secret))
    return keys.by_id[key_id]


def _aad(purpose: Purpose, workspace_id: uuid.UUID, destination_id: uuid.UUID) -> bytes:
    return b"abb-alert|" + purpose.encode() + b"|" + workspace_id.bytes + destination_id.bytes


async def seal(
    engine: AsyncEngine,
    plaintext: bytes,
    *,
    purpose: Purpose,
    workspace_id: uuid.UUID,
    destination_id: uuid.UUID,
) -> Sealed:
    key_id, aead = await _current(engine)
    nonce = secrets.token_bytes(_NONCE_BYTES)
    body = aead.encrypt(nonce, plaintext, _aad(purpose, workspace_id, destination_id))
    return Sealed(key_id, _VERSION + nonce + body)


async def open_sealed(
    engine: AsyncEngine,
    sealed: Sealed,
    *,
    purpose: Purpose,
    workspace_id: uuid.UUID,
    destination_id: uuid.UUID,
) -> bytes:
    """The plaintext, or `SealError` (wrong row, wrong workspace, wrong purpose, tampering, a key
    that no longer exists). Callers must not log or return the result."""
    blob = sealed.ciphertext
    if len(blob) < 1 + _NONCE_BYTES + 16 or blob[:1] != _VERSION:
        raise SealError
    aead = await _by_id(engine, sealed.key_id)
    try:
        return aead.decrypt(
            blob[1 : 1 + _NONCE_BYTES],
            blob[1 + _NONCE_BYTES :],
            _aad(purpose, workspace_id, destination_id),
        )
    except InvalidTag:
        raise SealError from None


# ---- helpers for the two payloads


def new_signing_secret() -> str:
    """A fresh webhook signing secret: 256 bits, URL-safe, shown to the owner once."""
    return SIGNING_SECRET_PREFIX + secrets.token_urlsafe(32)


def encode_config(config: dict[str, Any]) -> bytes:
    return json.dumps(config, separators=(",", ":"), sort_keys=True, ensure_ascii=True).encode()


def decode_config(plaintext: bytes) -> dict[str, Any]:
    value = json.loads(plaintext)
    if not isinstance(value, dict):
        raise SealError
    return value

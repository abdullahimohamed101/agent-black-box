"""The installation's server-only secret for deriving event ids (ADR-141 amendment 2).

Ids of server-written events are `HMAC-SHA256(secret, material)`, so an agent cannot compute
them and pre-claim them with events of its own. The secret is random, created once per
installation in the append-only `server_secrets` table (first use, under an advisory lock, in its
own short transaction so it is committed before anything uses it) and cached per process. It is
never logged, returned or placed in an event.
"""

import secrets
import weakref

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

NAME = "event-id"
_LOCK = "abb.server-secret.event-id"
_cache: weakref.WeakKeyDictionary[AsyncEngine, bytes] = weakref.WeakKeyDictionary()


async def event_id_key(engine: AsyncEngine) -> bytes:
    cached = _cache.get(engine)
    if cached is not None:
        return cached
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
    _cache[engine] = found
    return found


async def _newest(conn: AsyncConnection) -> bytes | None:
    row = (
        await conn.execute(
            text("SELECT secret FROM server_secrets WHERE name = :n ORDER BY id DESC LIMIT 1"),
            {"n": NAME},
        )
    ).first()
    return bytes(row.secret) if row else None

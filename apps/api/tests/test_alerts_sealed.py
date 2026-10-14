"""Destination secrets at rest (ADR-150): AES-256-GCM under a `server_secrets` key, bound to the
row, the workspace and the purpose; nothing readable in the database."""

import asyncio
import secrets
import uuid

import pytest
from abb_event_schema.ids import IdKind
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from abb_api.alerts import sealed
from abb_api.alerts.sealed import SealError
from abb_api.ids import new_uuid
from tests.alerts_helpers import SENTINEL_SIGNING, SENTINEL_URL, seed_destination
from tests.ingest_helpers import make_tenant

WS = uuid.uuid4()
DST = new_uuid(IdKind.DESTINATION)
SECRET = b"https://hooks.example.com/services/SENTINELPLAINTEXT"


async def seal(engine: AsyncEngine, purpose: sealed.Purpose = "config") -> bytes:
    return await sealed.seal(engine, SECRET, purpose=purpose, workspace_id=WS, destination_id=DST)


async def open_(
    engine: AsyncEngine,
    blob: bytes,
    *,
    purpose: sealed.Purpose = "config",
    workspace: uuid.UUID = WS,
    destination: uuid.UUID = DST,
) -> bytes:
    return await sealed.open_sealed(
        engine, blob, purpose=purpose, workspace_id=workspace, destination_id=destination
    )


async def test_a_secret_round_trips_and_the_ciphertext_hides_it(engine: AsyncEngine) -> None:
    blob = await seal(engine)
    assert SECRET not in blob and b"SENTINELPLAINTEXT" not in blob
    assert await open_(engine, blob) == SECRET
    assert await seal(engine) != blob  # a fresh nonce every time


async def test_a_ciphertext_cannot_be_moved_to_another_row_workspace_or_purpose(
    engine: AsyncEngine,
) -> None:
    blob = await seal(engine)
    for kwargs in (
        {"destination": new_uuid(IdKind.DESTINATION)},
        {"workspace": uuid.uuid4()},
        {"purpose": "signing"},
    ):
        with pytest.raises(SealError):
            await open_(engine, blob, **kwargs)


async def test_a_tampered_or_truncated_ciphertext_fails_with_a_fixed_message(
    engine: AsyncEngine,
) -> None:
    blob = bytearray(await seal(engine))
    blob[-1] ^= 1
    for bad in (bytes(blob), b"", b"\x01" + b"\x00" * 10, b"\x02" + bytes(blob)[1:]):
        with pytest.raises(SealError) as excinfo:
            await open_(engine, bad)
        assert str(excinfo.value) == "a destination secret could not be decrypted"
        assert "SENTINELPLAINTEXT" not in repr(excinfo.value)


async def test_the_key_is_created_once_even_when_many_processes_race(
    database_url: str, engine: AsyncEngine
) -> None:
    engines = [create_async_engine(database_url, poolclass=NullPool) for _ in range(6)]
    try:
        blobs = await asyncio.gather(*(seal(e) for e in engines))
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text("SELECT id FROM server_secrets WHERE name = :n"), {"n": sealed.NAME}
                )
            ).all()
        assert len(rows) == 1
        assert {sealed.key_id_of(b) for b in blobs} == {rows[0].id}
        assert await open_(engines[0], blobs[-1]) == SECRET  # any process opens any process's
    finally:
        for e in engines:
            await e.dispose()


async def test_a_rotated_key_seals_new_data_and_the_old_key_still_opens_old_data(
    database_url: str, engine: AsyncEngine
) -> None:
    old = await seal(engine)
    async with engine.begin() as conn:  # the operator's rotation: a newer row, then a restart
        await conn.execute(
            text("INSERT INTO server_secrets (name, secret) VALUES (:n, :s)"),
            {"n": sealed.NAME, "s": secrets.token_bytes(32)},
        )
    restarted = create_async_engine(database_url, poolclass=NullPool)
    try:
        new = await seal(restarted)
        assert sealed.key_id_of(new) != sealed.key_id_of(old)
        assert await open_(restarted, old) == SECRET
        assert await open_(restarted, new) == SECRET
        # An unrestarted process keeps sealing under the key it cached, and opens the new one too.
        assert sealed.key_id_of(await seal(engine)) == sealed.key_id_of(old)
        assert await open_(engine, new) == SECRET
    finally:
        await restarted.dispose()


async def test_a_ciphertext_naming_a_key_that_does_not_exist_cannot_be_opened(
    engine: AsyncEngine,
) -> None:
    blob = bytearray(await seal(engine))
    blob[1:9] = (10_000).to_bytes(8, "big")
    with pytest.raises(SealError):
        await open_(engine, bytes(blob))


async def test_another_key_name_is_never_used_to_open_alert_secrets(engine: AsyncEngine) -> None:
    blob = await seal(engine)
    async with engine.begin() as conn:
        other = await conn.execute(
            text("INSERT INTO server_secrets (name, secret) VALUES ('event-id', :s) RETURNING id"),
            {"s": secrets.token_bytes(32)},
        )
        other_id = other.scalar_one()
    tampered = bytearray(blob)
    tampered[1:9] = int(other_id).to_bytes(8, "big")
    fresh = create_async_engine(
        engine.url.render_as_string(hide_password=False), poolclass=NullPool
    )
    try:
        with pytest.raises(SealError):
            await open_(fresh, bytes(tampered))
    finally:
        await fresh.dispose()


async def test_no_plaintext_target_or_secret_is_in_the_database(engine: AsyncEngine) -> None:
    tenant = await make_tenant(engine, "acme")
    await seed_destination(engine, tenant.context)
    async with engine.connect() as conn:
        whole_row = (
            await conn.execute(text("SELECT t::text FROM alert_destinations t"))
        ).scalar_one()
        raw = (
            await conn.execute(
                text("SELECT config_encrypted, signing_secret_encrypted FROM alert_destinations")
            )
        ).one()
    haystack = whole_row.encode() + bytes(raw[0]) + bytes(raw[1])
    for needle in (SENTINEL_URL, "SENTINELSECRETTOKEN", "T000/B000", SENTINEL_SIGNING):
        assert needle.encode() not in haystack, needle
    # The only readable part is the mask: the host and four characters of a long final segment.
    assert "https://hooks.example.com/…/1234" in whole_row


async def test_the_signing_secret_is_random_and_url_safe() -> None:
    first, second = sealed.new_signing_secret(), sealed.new_signing_secret()
    assert first != second and first.startswith("whsec_") and len(first) >= 40
    assert all(c.isalnum() or c in "-_" for c in first.removeprefix("whsec_"))

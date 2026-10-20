"""Alert-key rotation (ADR-162): every destination is re-sealed under a new key, resumably,
idempotently and without plaintext in any output; old keys retire only when nothing references
them."""

import asyncio
import io
import logging
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from abb_api.alerts import sealed
from abb_api.alerts.sealed import SealError
from abb_api.rotate import cli as rotate_cli
from abb_api.rotate.alerts import RotationReport, rotate_alerts
from tests.alerts_helpers import SENTINEL_SIGNING, SENTINEL_URL, seed_destination
from tests.backup.helpers import base_environ
from tests.ingest_helpers import Tenant, make_tenant


@pytest.fixture
async def installation(engine: AsyncEngine) -> tuple[Tenant, Tenant, list[uuid.UUID]]:
    acme = await make_tenant(engine, "acme")
    globex = await make_tenant(engine, "globex")
    ids = [
        await seed_destination(engine, acme.context, name="pager"),
        await seed_destination(engine, acme.context, name="mail", dest_type="email"),
        await seed_destination(engine, globex.context, name="hook"),
    ]
    return acme, globex, ids


@pytest.fixture
async def fresh(database_url: str) -> AsyncIterator[AsyncEngine]:
    """An engine with an empty key cache: what a freshly restarted process sees."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    yield engine
    await engine.dispose()


async def rows(engine: AsyncEngine) -> dict[uuid.UUID, tuple[uuid.UUID, bytes, bytes | None, int]]:
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT workspace_id, id, config_encrypted, signing_secret_encrypted, key_id "
                "FROM alert_destinations"
            )
        )
        return {
            r.id: (
                r.workspace_id,
                bytes(r.config_encrypted),
                r.signing_secret_encrypted and bytes(r.signing_secret_encrypted),
                r.key_id,
            )
            for r in result
        }


async def plaintexts(engine: AsyncEngine) -> dict[uuid.UUID, tuple[bytes, bytes | None]]:
    out: dict[uuid.UUID, tuple[bytes, bytes | None]] = {}
    for did, (ws, config, signing, _) in (await rows(engine)).items():
        opened = await sealed.open_sealed(
            engine, config, purpose="config", workspace_id=ws, destination_id=did
        )
        sig = (
            await sealed.open_sealed(
                engine, signing, purpose="signing", workspace_id=ws, destination_id=did
            )
            if signing
            else None
        )
        out[did] = (opened, sig)
    return out


async def key_ids(engine: AsyncEngine) -> list[int]:
    async with engine.connect() as conn:
        return [
            r[0]
            for r in await conn.execute(
                text("SELECT id FROM server_secrets WHERE name = 'alerts-encryption' ORDER BY id")
            )
        ]


async def test_rotation_reseals_every_destination_and_the_old_key_can_retire(
    engine: AsyncEngine, database_url: str, installation: tuple[Tenant, Tenant, list[uuid.UUID]]
) -> None:
    _, _, ids = installation
    before_engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        before = await plaintexts(before_engine)
    finally:
        await before_engine.dispose()
    (old_key,) = await key_ids(engine)

    report = await rotate_alerts(database_url, dry_run=False, new_key=True)
    assert report.key_added and report.key_added != old_key
    assert (report.destinations, report.resealed, report.already_current) == (3, 3, 0)
    assert report.blobs_by_key == {report.key_added: 5}  # 3 configs + 2 webhook signing secrets
    assert report.older_blobs == 0 and report.can_retire_old_keys

    fresh_engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        assert await plaintexts(fresh_engine) == before  # the same secrets, sealed again
        current = await rows(fresh_engine)
        for _, config, signing, column in current.values():
            assert sealed.key_id_of(config) == column == report.key_added
            if signing:
                assert sealed.key_id_of(signing) == report.key_added
        # The old key now opens nothing: removing it (a deliberate owner action) breaks nothing.
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM server_secrets WHERE id = :i"), {"i": old_key})
        late = create_async_engine(database_url, poolclass=NullPool)
        try:
            assert await plaintexts(late) == before
        finally:
            await late.dispose()
    finally:
        await fresh_engine.dispose()
    assert len(ids) == 3 and len(await key_ids(engine)) == 1


async def test_the_binding_survives_a_rotation(
    engine: AsyncEngine, database_url: str, installation: tuple[Tenant, Tenant, list[uuid.UUID]]
) -> None:
    await rotate_alerts(database_url, dry_run=False, new_key=True)
    fresh_engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        current = await rows(fresh_engine)
        (a, (ws_a, config_a, signing_a, _)), (b, (ws_b, *_)) = (
            next((k, v) for k, v in current.items() if v[2]),
            next((k, v) for k, v in current.items() if v[0] != next(iter(current.values()))[0]),
        )
        assert signing_a is not None
        for kwargs in (
            {"destination_id": b, "workspace_id": ws_a, "purpose": "config"},  # another row
            {"destination_id": a, "workspace_id": ws_b, "purpose": "config"},  # another workspace
            {"destination_id": a, "workspace_id": ws_a, "purpose": "signing"},  # the other purpose
        ):
            with pytest.raises(SealError):
                await sealed.open_sealed(fresh_engine, config_a, **kwargs)  # type: ignore[arg-type]
    finally:
        await fresh_engine.dispose()


async def test_a_second_run_changes_nothing_and_a_dry_run_writes_nothing(
    engine: AsyncEngine, database_url: str, installation: tuple[Tenant, Tenant, list[uuid.UUID]]
) -> None:
    snapshot = await rows(engine)
    dry = await rotate_alerts(database_url, dry_run=True, new_key=True)
    assert dry.would_reseal == 0  # no newer key exists yet: nothing is stale
    assert dry.key_added is None and len(await key_ids(engine)) == 1
    assert await rows(engine) == snapshot and not dry.can_retire_old_keys

    first = await rotate_alerts(database_url, dry_run=False, new_key=True)
    assert first.resealed == 3
    after_first = await rows(engine)
    second = await rotate_alerts(database_url, dry_run=False, new_key=False)
    assert (second.resealed, second.already_current, second.key_added) == (0, 3, None)
    assert await rows(engine) == after_first  # not even a fresh nonce: untouched
    assert second.can_retire_old_keys
    dry_after = await rotate_alerts(database_url, dry_run=True, new_key=False)
    assert dry_after.would_reseal == 0 and len(await key_ids(engine)) == 2


async def test_a_dry_run_after_a_key_was_added_counts_what_a_real_run_would_do(
    engine: AsyncEngine, database_url: str, installation: tuple[Tenant, Tenant, list[uuid.UUID]]
) -> None:
    async with engine.begin() as conn:
        await sealed.add_key(conn)
    snapshot = await rows(engine)
    dry = await rotate_alerts(database_url, dry_run=True, new_key=False)
    assert (dry.destinations, dry.would_reseal, dry.resealed) == (3, 3, 0)
    assert dry.older_blobs == 5 and not dry.can_retire_old_keys
    assert await rows(engine) == snapshot


async def test_an_interrupted_run_resumes_and_a_failed_destination_blocks_retirement(
    engine: AsyncEngine, database_url: str, installation: tuple[Tenant, Tenant, list[uuid.UUID]]
) -> None:
    _, _, ids = installation
    before = await rows(engine)
    seen: list[uuid.UUID] = []

    async def fail_second(destination_id: uuid.UUID, phase: str) -> None:
        if phase == "before_write":
            seen.append(destination_id)
            if len(seen) == 2:
                raise RuntimeError("injected fault")

    report = await rotate_alerts(database_url, dry_run=False, new_key=True, hook=fail_second)
    assert report.resealed == 2 and report.failed == [seen[1]]
    assert not report.can_retire_old_keys and report.older_blobs > 0
    mid = await rows(engine)
    assert mid[seen[1]] == before[seen[1]]  # the failed one is exactly as it was (rolled back)

    done = await rotate_alerts(database_url, dry_run=False, new_key=False)
    assert (done.resealed, done.already_current) == (1, 2) and done.can_retire_old_keys
    assert len(await key_ids(engine)) == 2
    assert set(ids) == set(await rows(engine))


async def test_a_killed_run_leaves_a_consistent_state_and_resumes(
    engine: AsyncEngine, database_url: str, installation: tuple[Tenant, Tenant, list[uuid.UUID]]
) -> None:
    calls = 0

    async def die_on_second(destination_id: uuid.UUID, phase: str) -> None:
        nonlocal calls
        if phase == "before_commit":
            calls += 1
            if calls == 2:
                raise asyncio.CancelledError  # not an Exception: the whole run stops

    with pytest.raises(asyncio.CancelledError):
        await rotate_alerts(database_url, dry_run=False, new_key=True, hook=die_on_second)
    newest = (await key_ids(engine))[-1]
    keys = [sealed.key_id_of(c) for _, c, _, _ in (await rows(engine)).values()]
    assert keys.count(newest) == 1 and len(keys) == 3  # exactly the committed one
    fresh_engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        await plaintexts(fresh_engine)  # every row, old or new, still opens
    finally:
        await fresh_engine.dispose()
    resumed = await rotate_alerts(database_url, dry_run=False, new_key=False)
    assert resumed.resealed == 2 and resumed.can_retire_old_keys


async def test_delivery_during_rotation_keeps_decrypting(
    engine: AsyncEngine, database_url: str, installation: tuple[Tenant, Tenant, list[uuid.UUID]]
) -> None:
    sender = create_async_engine(database_url, poolclass=NullPool)  # a delivery worker's engine
    opened: list[bytes] = []

    async def deliver_meanwhile(destination_id: uuid.UUID, phase: str) -> None:
        if phase != "before_commit":
            return
        async with sender.connect() as conn:  # the row is locked by the rotation, not unreadable
            row = (
                await conn.execute(
                    text(
                        "SELECT workspace_id, config_encrypted FROM alert_destinations "
                        "WHERE id = :d"
                    ),
                    {"d": destination_id},
                )
            ).one()
        opened.append(
            await sealed.open_sealed(
                sender,
                bytes(row.config_encrypted),
                purpose="config",
                workspace_id=row.workspace_id,
                destination_id=destination_id,
            )
        )

    try:
        report = await rotate_alerts(
            database_url, dry_run=False, new_key=True, hook=deliver_meanwhile
        )
        assert report.resealed == 3 and len(opened) == 3
        # The sender's own cache knows only the old key: the new one is fetched by id on demand.
        after = await plaintexts(sender)
        assert all(sig is None or sig.startswith(b"whsec_") for _, sig in after.values())
    finally:
        await sender.dispose()


async def test_a_blob_that_does_not_open_is_reported_by_id_and_does_not_stop_the_rest(
    engine: AsyncEngine, database_url: str, installation: tuple[Tenant, Tenant, list[uuid.UUID]]
) -> None:
    _, _, ids = installation
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE alert_destinations SET config_encrypted = "
                "set_byte(config_encrypted, octet_length(config_encrypted) - 1, "
                "get_byte(config_encrypted, octet_length(config_encrypted) - 1) # 1) "
                "WHERE id = :d"
            ),
            {"d": ids[1]},
        )
    report = await rotate_alerts(database_url, dry_run=False, new_key=True)
    assert report.failed == [ids[1]] and report.resealed == 2
    assert not report.can_retire_old_keys


async def test_stragglers_sealed_by_a_process_that_was_not_restarted_are_found(
    engine: AsyncEngine, database_url: str, installation: tuple[Tenant, Tenant, list[uuid.UUID]]
) -> None:
    acme, _, _ = installation
    report = await rotate_alerts(database_url, dry_run=False, new_key=True)
    assert report.can_retire_old_keys
    # `engine` still caches the old current key: it is an API process that was not restarted.
    late = await seed_destination(engine, acme.context, name="late")
    check = await rotate_alerts(database_url, dry_run=True, new_key=False)
    assert check.older_blobs == 2 and not check.can_retire_old_keys
    final = await rotate_alerts(database_url, dry_run=False, new_key=False)
    assert final.resealed == 1 and final.can_retire_old_keys
    assert late in await rows(engine)


async def test_the_command_prints_counts_and_ids_never_secrets(
    engine: AsyncEngine,
    database_url: str,
    installation: tuple[Tenant, Tenant, list[uuid.UUID]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    out, err = io.StringIO(), io.StringIO()
    code = await rotate_cli.run(
        ["alerts", "--new-key"], base_environ(ABB_ROTATION_DATABASE_URL=database_url), out, err
    )
    assert code == 0, err.getvalue()
    assert "verdict=old_keys_can_be_retired" in out.getvalue() and "resealed=3" in out.getvalue()
    everything = out.getvalue() + err.getvalue() + caplog.text
    for secret in (
        SENTINEL_URL,
        SENTINEL_SIGNING,
        "SENTINELSECRETTOKEN",
        "abb_dev_password",
        "hooks.example.com",
    ):
        assert secret not in everything, secret

    out, err = io.StringIO(), io.StringIO()
    assert await rotate_cli.run(["alerts"], base_environ(), out, err) == 2
    assert (
        await rotate_cli.run(
            ["alerts", "--batch-size", "0"], base_environ(ABB_DATABASE_URL=database_url), out, err
        )
        == 2
    )
    broken = "postgresql+asyncpg://abb:SENTINELDBPASSWORD@127.0.0.1:1/nowhere"
    err = io.StringIO()
    assert (
        await rotate_cli.run(["alerts"], base_environ(ABB_DATABASE_URL=broken), io.StringIO(), err)
        == 5
    )
    assert "SENTINELDBPASSWORD" not in err.getvalue()


def test_the_report_type_is_what_the_cli_prints() -> None:
    report = RotationReport(dry_run=False, newest_key_id=2)
    report.blobs_by_key[2] = 4
    assert report.can_retire_old_keys
    report.blobs_by_key[1] = 1
    assert not report.can_retire_old_keys and report.older_blobs == 1

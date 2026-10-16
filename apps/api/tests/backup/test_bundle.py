"""Bundle rules: encryption, tampering, refusals, permissions, the artifact ordering guarantee,
snapshot consistency, schema upgrade on restore, error hygiene."""

import asyncio
import json
import os
import shutil
import stat
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from abb_api.backup import create as create_module
from abb_api.backup.restore import alembic_root, code_head
from tests.alerts_helpers import SENTINEL_SIGNING
from tests.backup.conftest import World
from tests.backup.helpers import (
    PASSPHRASE,
    base_environ,
    count,
    read_json,
    run_cli,
    scratch_database,
    write_passphrase,
)
from tests.ingest_helpers import make_tenant


def restore_env(target_url: str, artifact_dir: Path, **extra: str) -> dict[str, str]:
    return base_environ(
        ABB_RESTORE_DATABASE_URL=target_url, ABB_ARTIFACT_DIR=str(artifact_dir), **extra
    )


async def tables_in(url: str) -> int:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            return int(
                (
                    await conn.execute(
                        text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
                    )
                ).scalar_one()
            )
    finally:
        await engine.dispose()


def flip_a_byte(path: Path) -> None:
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0xFF
    path.write_bytes(bytes(data))


async def encrypted_bundle(world: World, tmp_path: Path) -> tuple[Path, Path]:
    passphrase_file = write_passphrase(tmp_path)
    bundle = tmp_path / "enc-bundle"
    code, _, err = await run_cli(
        "create", "--out", str(bundle), "--encrypt", "--passphrase-file", str(passphrase_file),
        environ=world.environ(),
    )  # fmt: skip
    assert code == 0, err
    return bundle, passphrase_file


# ---- encryption


async def test_an_encrypted_bundle_round_trips_and_hides_its_content(
    world: World, tmp_path: Path
) -> None:
    bundle, passphrase_file = await encrypted_bundle(world, tmp_path)
    names = {p.name for p in bundle.iterdir()}
    assert names == {"MANIFEST.json", "database.dump.enc", "artifacts.tar.enc"}
    dump = (bundle / "database.dump.enc").read_bytes()
    assert dump.startswith(b"Salted__") and b"PGDMP" not in dump[:64]
    assert b"x" * 100 not in (bundle / "artifacts.tar.enc").read_bytes()
    manifest = read_json(bundle / "MANIFEST.json")  # the manifest stays readable
    assert manifest["encryption"]["scheme"].startswith("openssl-enc-aes-256-cbc-pbkdf2")
    assert manifest["manifest_mac"] and manifest["schema_revision"]
    assert PASSPHRASE not in (bundle / "MANIFEST.json").read_text()

    # The passphrase may come from an environment variable NAME instead of a file.
    code, _, err = await run_cli(
        "verify", str(bundle), "--passphrase-env", "MY_BACKUP_PASS",
        environ=base_environ(MY_BACKUP_PASS=PASSPHRASE),
    )  # fmt: skip
    assert code == 0, err

    async with scratch_database() as target:
        restored = tmp_path / "restored"
        code, _, err = await run_cli(
            "restore", str(bundle), "--passphrase-file", str(passphrase_file),
            environ=restore_env(target, restored),
        )  # fmt: skip
        assert code == 0, err
        assert await count(target, "runs") == 3
        assert {
            str(p.relative_to(restored)): p.read_bytes() for p in restored.rglob("*") if p.is_file()
        } == world.artifact_keys


async def test_a_wrong_passphrase_fails_with_one_fixed_error_and_restores_nothing(
    world: World, tmp_path: Path
) -> None:
    bundle, _ = await encrypted_bundle(world, tmp_path)
    wrong = write_passphrase(
        tmp_path / ".." / "wrongdir" if False else tmp_path, "not the passphrase!!"
    )
    async with scratch_database() as target:
        restored = tmp_path / "restored"
        code, out, err = await run_cli(
            "restore", str(bundle), "--passphrase-file", str(wrong),
            environ=restore_env(target, restored),
        )  # fmt: skip
        assert code == 4
        assert "the passphrase is wrong or the bundle was modified" in err
        assert "not the passphrase" not in err + out
        assert await tables_in(target) == 0 and not restored.exists()
    code, _, err = await run_cli("verify", str(bundle), environ=base_environ())
    assert code == 2 and "encrypted" in err


# ---- tampering


@pytest.mark.parametrize("victim", ["database.dump", "artifacts.tar"])
async def test_a_changed_byte_fails_verify_and_restore_and_names_the_file(
    world: World, tmp_path: Path, victim: str
) -> None:
    bundle = tmp_path / "bundle"
    assert (await run_cli("create", "--out", str(bundle), environ=world.environ()))[0] == 0
    flip_a_byte(bundle / victim)
    code, _, err = await run_cli("verify", str(bundle), environ=base_environ())
    assert code == 4 and victim in err and "checksum mismatch" in err
    async with scratch_database() as target:
        restored = tmp_path / "restored"
        code, _, err = await run_cli("restore", str(bundle), environ=restore_env(target, restored))
        assert code == 4 and victim in err
        assert await tables_in(target) == 0 and not restored.exists()


async def test_tampering_with_an_encrypted_bundle_is_found_by_file_or_by_the_mac(
    world: World, tmp_path: Path
) -> None:
    bundle, passphrase_file = await encrypted_bundle(world, tmp_path)
    args = ("--passphrase-file", str(passphrase_file))
    flip_a_byte(bundle / "database.dump.enc")
    code, _, err = await run_cli("verify", str(bundle), *args, environ=base_environ())
    assert code == 4 and "database.dump.enc" in err

    other_bundle, _ = await encrypted_bundle(world, other_dir(tmp_path))
    manifest = read_json(other_bundle / "MANIFEST.json")
    manifest["tables"]["runs"] = 999
    (other_bundle / "MANIFEST.json").write_text(json.dumps(manifest))
    code, _, err = await run_cli("verify", str(other_bundle), *args, environ=base_environ())
    assert code == 4 and "passphrase is wrong or the bundle was modified" in err


def other_dir(tmp_path: Path) -> Path:
    path = tmp_path / "second"
    path.mkdir()
    return path


# ---- refusals and permissions


async def test_nothing_is_ever_overwritten_or_loaded_into_a_non_empty_target(
    world: World, tmp_path: Path
) -> None:
    taken = tmp_path / "taken"
    taken.mkdir()
    (taken / "keep.txt").write_text("mine")
    code, _, err = await run_cli("create", "--out", str(taken), environ=world.environ())
    assert code == 3 and (taken / "keep.txt").read_text() == "mine"

    bundle = tmp_path / "bundle"
    assert (await run_cli("create", "--out", str(bundle), environ=world.environ()))[0] == 0

    async with scratch_database(migrate_to="0001") as dirty:  # a database with a table in it
        code, _, err = await run_cli(
            "restore", str(bundle), environ=restore_env(dirty, tmp_path / "a")
        )
        assert code == 3 and "not empty" in err and not (tmp_path / "a").exists()

    async with scratch_database() as clean:
        full = tmp_path / "full"
        full.mkdir()
        (full / "x").write_text("data")
        code, _, err = await run_cli("restore", str(bundle), environ=restore_env(clean, full))
        assert code == 3 and "artifact directory" in err
        assert await tables_in(clean) == 0 and (full / "x").read_text() == "data"


async def test_bundle_permissions_and_passphrase_file_permissions(
    world: World, tmp_path: Path
) -> None:
    empty = tmp_path / "wide"
    empty.mkdir(mode=0o755)
    empty.chmod(0o755)
    assert (await run_cli("create", "--out", str(empty), environ=world.environ()))[0] == 0
    assert stat.S_IMODE(empty.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in empty.iterdir())

    loose = write_passphrase(tmp_path, mode=0o644)
    code, _, err = await run_cli(
        "create", "--out", str(tmp_path / "b2"), "--encrypt", "--passphrase-file", str(loose),
        environ=world.environ(),
    )  # fmt: skip
    assert code == 3 and "readable by group or others" in err and not (tmp_path / "b2").exists()


async def test_usage_errors_have_fixed_codes(world: World, tmp_path: Path) -> None:
    out = str(tmp_path / "b")
    short = write_passphrase(tmp_path, "tooshort", name="short")
    good = write_passphrase(tmp_path, name="good")
    cases = [
        (("create", "--out", out, "--encrypt"), world.environ()),
        (("create", "--out", out, "--passphrase-file", str(good)), world.environ()),
        (("create", "--out", out, "--encrypt", "--passphrase-file", str(short)), world.environ()),
        (("create", "--out", out), base_environ()),
        (("restore", str(tmp_path)), base_environ()),
    ]
    for argv, env in cases:
        code, _, err = await run_cli(*argv, environ=env)
        assert code == 2, (argv, err)
    assert not (tmp_path / "b").exists()
    code, _, err = await run_cli("verify", str(tmp_path), environ=base_environ())
    assert code == 3 and "not a backup bundle" in err


# ---- the artifact ordering guarantee and snapshot consistency


async def test_an_artifact_row_without_its_file_is_never_backed_up(
    world: World, tmp_path: Path
) -> None:
    victim = next(iter(world.artifact_keys))
    (world.artifact_dir / victim).unlink()
    bundle = tmp_path / "bundle"
    code, _, err = await run_cli("create", "--out", str(bundle), environ=world.environ())
    assert code == 7 and "no file" in err
    assert list(bundle.iterdir()) == []  # nothing half-written is left


async def test_a_file_deleted_between_the_dump_and_the_copy_fails_the_backup(
    world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_dump = create_module._dump
    victim = world.artifact_dir / next(iter(world.artifact_keys))

    def dump_then_delete(*args: Any, **kwargs: Any) -> None:
        real_dump(*args, **kwargs)
        victim.unlink()  # retention removing a file after the snapshot was dumped

    monkeypatch.setattr(create_module, "_dump", dump_then_delete)
    code, _, err = await run_cli(
        "create", "--out", str(tmp_path / "bundle"), environ=world.environ()
    )
    assert code == 7 and "no file" in err
    assert list((tmp_path / "bundle").iterdir()) == []


async def test_files_that_arrive_after_the_dump_are_harmless_extras(
    world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_dump = create_module._dump
    ws = next(iter(world.artifact_keys)).split("/")[0]
    extra = world.artifact_dir / ws / ("e" * 32 + "-" + "f" * 64)
    stray = world.artifact_dir / ws / ".tmp-abc"

    def dump_then_upload(*args: Any, **kwargs: Any) -> None:
        real_dump(*args, **kwargs)
        extra.write_bytes(b"late")  # an upload whose row the dump does not have
        stray.write_bytes(b"partial")

    monkeypatch.setattr(create_module, "_dump", dump_then_upload)
    bundle = tmp_path / "bundle"
    code, _, err = await run_cli("create", "--out", str(bundle), environ=world.environ())
    assert code == 0, err
    manifest = read_json(bundle / "MANIFEST.json")
    keys = {f["key"] for f in manifest["artifacts"]["files"]}
    assert f"{ws}/{extra.name}" in keys and not any(".tmp" in k for k in keys)
    assert manifest["tables"]["artifacts"] == 2  # the manifest still describes the snapshot
    async with scratch_database() as target:
        code, _, err = await run_cli(
            "restore", str(bundle), environ=restore_env(target, tmp_path / "restored")
        )
        assert code == 0, err  # counts match the snapshot, the extra file is just a file


async def test_writes_after_the_snapshot_are_in_neither_the_dump_nor_the_manifest(
    world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_dump = create_module._dump

    def write_then_dump(*args: Any, **kwargs: Any) -> None:
        async def late_write() -> None:
            await make_tenant(world.api.engine, "late-arrival")

        asyncio.run(late_write())  # committed after the snapshot was exported
        real_dump(*args, **kwargs)

    monkeypatch.setattr(create_module, "_dump", write_then_dump)
    bundle = tmp_path / "bundle"
    code, _, err = await run_cli("create", "--out", str(bundle), environ=world.environ())
    assert code == 0, err
    assert read_json(bundle / "MANIFEST.json")["tables"]["workspaces"] == 2
    async with scratch_database() as target:
        code, _, err = await run_cli(
            "restore", str(bundle), environ=restore_env(target, tmp_path / "restored")
        )
        assert code == 0, err
        assert await count(target, "workspaces") == 2
    assert await count(world.source_url, "workspaces") == 3


# ---- an older bundle is upgraded on restore; a newer one is refused


async def test_an_older_bundle_is_upgraded_to_the_code_head_on_restore(tmp_path: Path) -> None:
    from tests.test_migrations import revisions

    head = revisions()[-1]
    previous = revisions()[-2]
    assert head == code_head(alembic_root(os.environ))
    async with scratch_database(migrate_to=previous) as source:
        engine = create_async_engine(source, poolclass=NullPool)
        try:
            await make_tenant(engine, "oldtimer")
        finally:
            await engine.dispose()
        bundle = tmp_path / "old"
        artifacts = tmp_path / "src-artifacts"
        artifacts.mkdir()
        code, _, err = await run_cli(
            "create", "--out", str(bundle),
            environ=base_environ(ABB_BACKUP_DATABASE_URL=source, ABB_ARTIFACT_DIR=str(artifacts)),
        )  # fmt: skip
        assert code == 0, err
        assert read_json(bundle / "MANIFEST.json")["schema_revision"] == previous
        async with scratch_database() as target:
            code, out, err = await run_cli(
                "restore", str(bundle), environ=restore_env(target, tmp_path / "restored")
            )
            assert code == 0, err
            assert "upgraded=true" in out and f"revision_in_bundle={previous}" in out
            assert f"revision_now={head}" in out
            engine = create_async_engine(target, poolclass=NullPool)
            try:
                async with engine.connect() as conn:
                    version = (
                        await conn.execute(text("SELECT version_num FROM alembic_version"))
                    ).scalar_one()
                    column = (
                        await conn.execute(
                            text(
                                "SELECT count(*) FROM information_schema.columns "
                                "WHERE table_name = 'analytics_runs_daily' "
                                "AND column_name = 'expired_trace_runs'"
                            )
                        )
                    ).scalar_one()
            finally:
                await engine.dispose()
            assert version == head and column == 1


async def test_a_bundle_from_a_newer_schema_is_refused(world: World, tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    assert (await run_cli("create", "--out", str(bundle), environ=world.environ()))[0] == 0
    manifest = read_json(bundle / "MANIFEST.json")
    manifest["schema_revision"] = "9999"
    (bundle / "MANIFEST.json").write_text(json.dumps(manifest))
    async with scratch_database() as target:
        code, _, err = await run_cli(
            "restore", str(bundle), environ=restore_env(target, tmp_path / "restored")
        )
        assert code == 3 and "newer" in err and await tables_in(target) == 0


# ---- error hygiene


async def test_connection_failures_do_not_echo_credentials(world: World, tmp_path: Path) -> None:
    broken = "postgresql+asyncpg://abb:SENTINELDBPASSWORD@127.0.0.1:1/nowhere"
    code, out, err = await run_cli(
        "create", "--out", str(tmp_path / "b"),
        environ=base_environ(ABB_BACKUP_DATABASE_URL=broken, ABB_ARTIFACT_DIR=str(tmp_path)),
    )  # fmt: skip
    assert code != 0 and "SENTINELDBPASSWORD" not in out + err and "127.0.0.1" not in out + err
    assert not (tmp_path / "b").exists() or list((tmp_path / "b").iterdir()) == []


async def test_nothing_secret_is_in_a_bundle_manifest(world: World, tmp_path: Path) -> None:
    bundle, _ = await encrypted_bundle(world, tmp_path)
    manifest_text = (bundle / "MANIFEST.json").read_text()
    for needle in (PASSPHRASE, SENTINEL_SIGNING, "abb_dev_password", "postgresql", "SENTINEL"):
        assert needle not in manifest_text, needle
    # The unencrypted dump does contain server_secrets: that is why bundles are encrypted at rest.
    assert shutil.which("openssl")

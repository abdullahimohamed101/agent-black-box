"""The restore drill (Phase 19b D4/D6 gate 6): seed an installation, back it up, restore it into a
new database and a new artifact directory, and prove it is the same installation. Real PostgreSQL,
real pg_dump/pg_restore, real files. Only deterministic facts are asserted, never durations."""

import json
import logging
import os
import stat
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from abb_api.backup.manifest import KEY_TABLES
from abb_api.backup.restore import alembic_root, code_head
from abb_api.main import create_app
from tests.alerts_helpers import SENTINEL_SIGNING
from tests.backup.conftest import World, open_destination
from tests.backup.helpers import (
    PASSPHRASE,
    base_environ,
    privileges,
    read_json,
    run_cli,
    scratch_database,
    table_digest,
)
from tests.conftest import _client, make_settings, runtime_url

pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")


def restore_environ(
    world: World, target_url: str, artifact_dir: Path, **extra: str
) -> dict[str, str]:
    return base_environ(
        ABB_RESTORE_DATABASE_URL=target_url, ABB_ARTIFACT_DIR=str(artifact_dir), **extra
    )


async def make_bundle(world: World, out: Path, *flags: str, **extra: str) -> tuple[int, str, str]:
    return await run_cli("create", "--out", str(out), *flags, environ=world.environ(**extra))


def bundle_files(bundle: Path) -> dict[str, Path]:
    return {p.name: p for p in bundle.iterdir()}


async def test_a_backup_restores_into_the_same_installation(
    world: World, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    bundle = tmp_path / "bundle"
    code, out, err = await make_bundle(world, bundle)
    assert code == 0, err
    assert "result=ok" in out and "encrypted=false" in out

    # The bundle: private, complete, and its manifest describes the snapshot.
    assert stat.S_IMODE(bundle.stat().st_mode) == 0o700
    files = bundle_files(bundle)
    assert set(files) == {"MANIFEST.json", "database.dump", "artifacts.tar"}
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in files.values())
    manifest = read_json(bundle / "MANIFEST.json")
    assert manifest["format_version"] == 1
    assert manifest["schema_revision"] == code_head(alembic_root(os.environ))
    assert set(manifest["tables"]) == set(KEY_TABLES)
    assert manifest["tables"]["runs"] == 3 and manifest["tables"]["server_secrets"] == 1
    assert manifest["artifacts"]["count"] == 2 and manifest["artifacts"]["total_bytes"] == 2600
    assert manifest["encryption"] is None

    assert (await run_cli("verify", str(bundle), environ=base_environ()))[0] == 0

    source_secrets = await open_destination(
        world.api.engine, world.api.tenant.context.workspace_id, world.destination_id
    )
    assert source_secrets[1] == SENTINEL_SIGNING

    async with scratch_database() as target_url:
        target_artifacts = tmp_path / "restored-artifacts"
        code, out, err = await run_cli(
            "restore", str(bundle), environ=restore_environ(world, target_url, target_artifacts)
        )
        assert code == 0, err
        assert "result=ok" in out and "upgraded=false" in out

        restored = create_async_engine(target_url, poolclass=NullPool)
        try:
            # Counts and content of the key tables match the source.
            for table, order in (
                ("workspaces", "id"),
                ("runs", "workspace_id, id"),
                ("events", "workspace_id, event_id"),
                ("artifacts", "workspace_id, id"),
                ("api_keys", "key_id"),
                ("alert_destinations", "workspace_id, id"),
                ("server_secrets", "id"),
                ("retention_policies", "workspace_id"),
                ("audit_log", "id"),
            ):
                assert await table_digest(restored, table, order) == await table_digest(
                    world.api.engine, table, order
                ), table
            # Grants came back with the data (the runtime role still cannot touch events).
            assert await privileges(restored) == await privileges(world.api.engine)

            # Artifacts: the same files, byte for byte, and nothing else.
            found = {
                str(p.relative_to(target_artifacts)): p.read_bytes()
                for p in target_artifacts.rglob("*")
                if p.is_file()
            }
            assert found == world.artifact_keys

            # The alert destination still opens, with the restored server_secrets.
            assert (
                await open_destination(
                    restored, world.api.tenant.context.workspace_id, world.destination_id
                )
                == source_secrets
            )
            async with restored.connect() as conn:
                from sqlalchemy import text

                head = (
                    await conn.execute(text("SELECT version_num FROM alembic_version"))
                ).scalar_one()
            assert head == code_head(alembic_root(os.environ))
        finally:
            await restored.dispose()

        # The API serves the restored runs (as the least-privilege role, with a source key).
        app = create_app(make_settings(runtime_url(target_url)))
        async for client in _client(app):
            for run_id in world.run_ids:
                response = await client.get(
                    f"/v1/runs/{run_id}",
                    headers={"authorization": f"Bearer {world.api.tokens['reader']}"},
                )
                assert response.status_code == 200, response.text
                original = await world.api.get(f"/v1/runs/{run_id}")
                assert response.json() == original.json()
            listing = await client.get(
                "/v1/runs", headers={"authorization": f"Bearer {world.api.tokens['wide_reader']}"}
            )
            assert listing.status_code == 200
            assert len(listing.json()["items"]) == 3

    # Nothing secret was written to the manifest, the tool's output or the log records.
    secret_values = ("abb_dev_password", PASSPHRASE, SENTINEL_SIGNING, "SENTINELSECRETTOKEN")
    haystacks = [(bundle / "MANIFEST.json").read_text(), out, err, caplog.text]
    for value in secret_values:
        assert all(value not in h for h in haystacks), value
    json.loads((bundle / "MANIFEST.json").read_text())

"""The seeded installation every backup test starts from: a workspace with runs (through the real
ingestion path), artifact files and rows, a sealed alert destination, a retention policy."""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.alerts import sealed
from abb_api.artifacts.store import LocalFsArtifactStore
from abb_api.db import tables as t
from tests.alerts_helpers import SENTINEL_SIGNING, SENTINEL_URL, post_run, seed_destination
from tests.api_fixtures import Api
from tests.backup.helpers import base_environ
from tests.retention.helpers import NOW, seed_run


@dataclass
class World:
    api: Api
    source_url: str
    artifact_dir: Path
    run_ids: list[str]
    seeded_runs: list[uuid.UUID]
    artifact_keys: dict[str, bytes] = field(default_factory=dict)
    destination_id: uuid.UUID = field(default_factory=uuid.uuid4)

    def environ(self, **extra: str) -> dict[str, str]:
        return base_environ(
            ABB_BACKUP_DATABASE_URL=self.source_url,
            ABB_ARTIFACT_DIR=str(self.artifact_dir),
            **extra,
        )


@pytest.fixture
async def world(api: Api, database_url: str, tmp_path: Path) -> AsyncIterator[World]:
    artifact_dir = tmp_path / "artifacts"
    store = LocalFsArtifactStore(artifact_dir)
    run_ids = [await post_run(api, "SUCCESS"), await post_run(api, "FAILED", message="boom")]
    await api.drain()
    seeded = await seed_run(
        api.engine, api.tenant, ended=NOW, artifact_sizes=(100, 2500), store=store, full=True
    )
    world = World(api, database_url, artifact_dir, run_ids, [seeded.run_id])
    for key in seeded.artifact_keys:
        world.artifact_keys[key] = (artifact_dir / key).read_bytes()
    world.destination_id = await seed_destination(api.engine, api.tenant.context)
    async with api.engine.begin() as conn:
        await conn.execute(
            insert(t.retention_policies).values(
                workspace_id=api.tenant.context.workspace_id,
                artifacts_days=10,
                trace_days=40,
                updated_by="test:fixture",
            )
        )
    yield world


async def open_destination(
    engine: AsyncEngine, workspace_id: uuid.UUID, destination_id: uuid.UUID
) -> tuple[str, str]:
    """(target url, signing secret) of a destination, opened through the real sealed path."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT config_encrypted, signing_secret_encrypted FROM alert_destinations "
                    "WHERE workspace_id = :w AND id = :d"
                ),
                {"w": workspace_id, "d": destination_id},
            )
        ).one()
    config = await sealed.open_sealed(
        engine, bytes(row.config_encrypted), purpose="config",
        workspace_id=workspace_id, destination_id=destination_id,
    )  # fmt: skip
    signing = await sealed.open_sealed(
        engine, bytes(row.signing_secret_encrypted), purpose="signing",
        workspace_id=workspace_id, destination_id=destination_id,
    )  # fmt: skip
    return sealed.decode_config(config)["url"], signing.decode()


SENTINELS = (SENTINEL_URL, SENTINEL_SIGNING)

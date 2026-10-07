"""Migration cycle against a real database: empty -> head -> base -> head."""

import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import TEST_DATABASE_URL

API_DIR = Path(__file__).resolve().parents[1]


def alembic(*args: str) -> None:
    subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", "-x", f"url={TEST_DATABASE_URL}", *args],
        cwd=API_DIR,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("_", [0])
async def test_upgrade_downgrade_upgrade_is_repeatable(_: int) -> None:
    alembic("downgrade", "base")
    alembic("upgrade", "head")
    alembic("downgrade", "base")
    alembic("upgrade", "head")
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as conn:
        version = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
    await engine.dispose()
    assert version == "0001"

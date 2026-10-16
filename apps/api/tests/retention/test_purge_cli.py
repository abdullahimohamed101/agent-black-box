"""The commands as an operator runs them: `python -m abb_api.retention purge-project|...`.

Argument parsing, configuration, exit codes, output. The procedure itself is in test_purge.py."""

import asyncio
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.retention import purge, runner
from tests.ingest_helpers import make_tenant
from tests.retention.enforce_helpers import everything, make_store, scalar
from tests.retention.purge_helpers import seed_project_world


@pytest.fixture
def operator(retention_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ABB_RETENTION_DATABASE_URL", retention_database_url)
    monkeypatch.setenv("ABB_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("ABB_ENVIRONMENT", "test")
    monkeypatch.setenv("ABB_RETENTION_BATCH_SIZE", "2")
    return tmp_path / "artifacts"


async def cli(*argv: str) -> int:
    # `main` owns its event loop (it is the process entry point), so it runs in a thread.
    return await asyncio.to_thread(runner.main, list(argv))


async def test_the_default_is_a_dry_run_and_confirm_must_match_to_delete(
    engine: AsyncEngine,
    retention_engine: AsyncEngine,
    operator: Path,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    acme = await make_tenant(engine, "acme", projects=("p1", "p2"))
    await seed_project_world(engine, acme, "p1", store, runs=1)
    await seed_project_world(engine, acme, "p2", store, runs=1)
    before = await everything(engine)
    base = ("purge-project", "--workspace", "acme", "--project", "p1")

    assert await cli(*base) == purge.EXIT_OK
    out = capsys.readouterr().out
    assert "dry run: nothing was deleted" in out and "artifact store: files=2" in out
    assert await cli(*base, "--dry-run", "--confirm", "p1") == purge.EXIT_OK  # --dry-run wins
    assert await cli(*base, "--confirm", "p2") == purge.EXIT_USAGE
    assert (
        await cli("purge-workspace", "--workspace", "acme", "--confirm", "p1") == purge.EXIT_USAGE
    )
    assert await everything(engine) == before

    assert await cli(*base, "--confirm", "p1") == purge.EXIT_OK
    out = capsys.readouterr().out
    assert "purge-project: done purge_id=" in out
    assert await scalar(engine, "SELECT count(*) FROM projects WHERE slug = 'p1'") == 0
    assert await scalar(engine, "SELECT count(*) FROM projects WHERE slug = 'p2'") == 1
    # Run again: nothing by that name is left, which is a refusal (3), not a crash.
    assert await cli(*base, "--confirm", "p1") == purge.EXIT_REFUSED


async def test_a_purge_needs_the_artifact_directory_to_exist_but_a_dry_run_does_not(
    engine: AsyncEngine,
    retention_engine: AsyncEngine,
    operator: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Deleting a file that is not there succeeds, so a wrong directory would delete every record
    and keep every file: the same guard as enforcement."""
    await make_tenant(engine, "acme", projects=("p1",))
    monkeypatch.setenv("ABB_ARTIFACT_DIR", str(tmp_path / "wrong"))
    args = ("purge-project", "--workspace", "acme", "--project", "p1")
    assert await cli(*args) == purge.EXIT_OK
    assert await cli(*args, "--confirm", "p1") == 2
    assert "ABB_ARTIFACT_DIR" in capsys.readouterr().err
    assert await scalar(engine, "SELECT count(*) FROM projects WHERE slug = 'p1'") == 1
    assert await scalar(engine, "SELECT count(*) FROM purge_tombstones") == 0


def test_the_commands_exist_and_demand_their_scope() -> None:
    parser = runner.build_parser()
    ns = parser.parse_args(["purge-workspace", "--workspace", "w"])
    assert (ns.command, ns.workspace, ns.confirm, ns.dry_run) == (
        "purge-workspace",
        "w",
        None,
        False,
    )
    with pytest.raises(SystemExit):
        parser.parse_args(["purge-project", "--workspace", "w"])  # no --project
    with pytest.raises(SystemExit):
        parser.parse_args(["purge-workspace", "--project", "p", "--workspace", "w"])

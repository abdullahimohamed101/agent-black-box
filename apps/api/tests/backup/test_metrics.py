"""`--metrics-textfile` on the backup and rotation commands (Phase 19c step 2)."""

import io
import time
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.obs.textfile import BACKUP_METRICS, ROTATION_METRICS
from abb_api.rotate import cli as rotate_cli
from tests.alerts_helpers import seed_destination
from tests.backup.conftest import World
from tests.backup.helpers import base_environ, read_json, run_cli
from tests.ingest_helpers import make_tenant
from tests.obs.parser import parse, sample_value


async def test_a_backup_writes_its_success_time_duration_and_size_and_a_failure_keeps_them(
    world: World, tmp_path: Path
) -> None:
    target = tmp_path / "textfile" / "abb_backup.prom"
    target.parent.mkdir()
    bundle = tmp_path / "bundle"
    before = time.time()
    code, _, err = await run_cli(
        "create", "--out", str(bundle), "--metrics-textfile", str(target), environ=world.environ()
    )
    after = time.time()
    assert code == 0, err
    families = parse(target.read_text())
    assert set(families) == set(BACKUP_METRICS)
    stamp = sample_value(families, "backup_last_success_timestamp_seconds")
    assert stamp is not None and before <= stamp <= after
    assert sample_value(families, "backup_last_duration_seconds") >= 0  # type: ignore[operator]
    manifest = read_json(bundle / "MANIFEST.json")
    assert sample_value(families, "backup_bundle_bytes") == manifest["total_bytes"] > 0
    assert sample_value(families, "backup_runs_total", outcome="succeeded") == 1
    assert sample_value(families, "backup_runs_total", outcome="failed") in (None, 0)
    # Nothing that identifies the installation: no path, no URL, no host.
    text = target.read_text()
    for secret in (str(tmp_path), world.source_url, "localhost", "postgresql"):
        assert secret not in text

    # A failing run (refused: the output directory is not empty) counts and keeps the last success.
    code, _, _ = await run_cli(
        "create", "--out", str(bundle), "--metrics-textfile", str(target), environ=world.environ()
    )
    assert code != 0
    again = parse(target.read_text())
    assert sample_value(again, "backup_last_success_timestamp_seconds") == stamp
    assert sample_value(again, "backup_runs_total", outcome="failed") == 1
    assert sample_value(again, "backup_runs_total", outcome="succeeded") == 1


async def test_the_environment_variable_names_the_file_and_verify_writes_nothing(
    world: World, tmp_path: Path
) -> None:
    target = tmp_path / "from-env.prom"
    bundle = tmp_path / "bundle"
    env = world.environ(ABB_METRICS_TEXTFILE=str(target))
    code, _, err = await run_cli("create", "--out", str(bundle), environ=env)
    assert code == 0, err
    assert sample_value(parse(target.read_text()), "backup_runs_total", outcome="succeeded") == 1
    stamp = target.stat().st_mtime_ns
    code, _, err = await run_cli("verify", str(bundle), environ=env)
    assert code == 0, err
    assert target.stat().st_mtime_ns == stamp, "only `create` is a backup run"


async def test_an_unwritable_metrics_file_never_fails_the_backup(
    world: World, tmp_path: Path
) -> None:
    code, _, err = await run_cli(
        "create",
        "--out",
        str(tmp_path / "bundle"),
        "--metrics-textfile",
        str(tmp_path / "no-such-dir" / "x.prom"),
        environ=world.environ(),
    )
    assert code == 0, err


async def test_a_rotation_writes_its_success_time_and_counts_failures(
    engine: AsyncEngine, database_url: str, tmp_path: Path
) -> None:
    acme = await make_tenant(engine, "acme")
    await seed_destination(engine, acme.context, name="pager")
    target = tmp_path / "abb_rotation.prom"
    env = base_environ(ABB_ROTATION_DATABASE_URL=database_url)
    before = time.time()
    code = await rotate_cli.run(
        ["alerts", "--new-key", "--metrics-textfile", str(target)],
        env,
        io.StringIO(),
        io.StringIO(),
    )
    assert code == 0
    families = parse(target.read_text())
    assert set(families) == set(ROTATION_METRICS)
    stamp = sample_value(families, "rotation_last_success_timestamp_seconds")
    assert stamp is not None and before <= stamp <= time.time()
    assert sample_value(families, "rotation_runs_total", outcome="succeeded") == 1

    # A dry run changes nothing and is not a rotation run.
    done = await rotate_cli.run(
        ["alerts", "--dry-run", "--metrics-textfile", str(target)],
        env,
        io.StringIO(),
        io.StringIO(),
    )
    assert done == 0
    assert sample_value(parse(target.read_text()), "rotation_runs_total", outcome="succeeded") == 1

    # An unreachable database is a failed run; the last success stays.
    broken = base_environ(ABB_ROTATION_DATABASE_URL="postgresql+asyncpg://u:p@127.0.0.1:1/none")
    code = await rotate_cli.run(
        ["alerts", "--metrics-textfile", str(target)], broken, io.StringIO(), io.StringIO()
    )
    assert code == 5
    last = parse(target.read_text())
    assert sample_value(last, "rotation_runs_total", outcome="failed") == 1
    assert sample_value(last, "rotation_last_success_timestamp_seconds") == stamp

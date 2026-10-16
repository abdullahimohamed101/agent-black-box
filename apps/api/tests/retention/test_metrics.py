"""Retention metrics: counters from a finished tick, the textfile of the runner, and the
scrape-time heartbeat. The reports table is the oracle for every figure."""

import os
import subprocess
import sys
import uuid
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.db import create_engine
from abb_api.obs.catalogue import build_observability
from abb_api.obs.scrape import ScrapeSampler
from abb_api.obs.textfile import RETENTION_METRICS, TextfileMetrics
from abb_api.retention import runner
from abb_api.retention.metrics import record_crash, record_tick
from abb_api.retention.tick import TickResult
from tests.ingest_helpers import make_tenant
from tests.obs.helpers import total, value
from tests.obs.parser import parse, sample_value
from tests.retention.enforce_helpers import enforce, enforcing, make_store
from tests.retention.helpers import NOW, days_ago, seed_run, seed_world
from tests.retention.test_tick import settings, tick

SRC = Path(runner.__file__).resolve().parents[2]


async def report_sums(engine: AsyncEngine, mode: str) -> dict[str, tuple[int, int, int, int]]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT tier, sum(runs), sum(events), sum(row_count), sum(artifact_bytes) "
                "FROM retention_reports WHERE mode = :m GROUP BY tier"
            ),
            {"m": mode},
        )
        return {r[0]: (int(r[1]), int(r[2]), int(r[3]), int(r[4])) for r in rows}


async def test_a_dry_run_sets_the_would_delete_gauges_to_what_the_reports_say(
    engine: AsyncEngine, retention_engine: AsyncEngine
) -> None:
    acme, globex = await make_tenant(engine, "acme"), await make_tenant(engine, "globex")
    await seed_world(engine, acme, globex)
    result = await tick(retention_engine)
    obs = build_observability()
    record_tick(obs.catalogue, result)
    sums = await report_sums(engine, "dry_run")
    assert value(obs, "retention_passes_total", mode="dry_run") == 1
    assert value(obs, "retention_mode", mode="dry_run") == 1
    assert value(obs, "retention_mode", mode="enforce") == 0
    for tier in ("artifacts", "trace", "run"):
        assert value(obs, "retention_dry_run_candidates", tier=tier) == sums[tier][0] > 0
    for tier in ("audit", "operational", "platform"):
        assert value(obs, "retention_dry_run_candidates", tier=tier) == sums[tier][2]
    assert total(obs, "retention_deleted_total") == 0, "a dry run deletes nothing"
    assert total(obs, "retention_failures_total") == 0


async def test_enforce_counts_what_it_really_removed_per_tier_and_kind(
    engine: AsyncEngine, retention_engine: AsyncEngine, tmp_path: Path
) -> None:
    store = make_store(tmp_path)
    acme = await make_tenant(engine, "acme")
    kwargs = {"store": store, "full": True, "artifact_sizes": (100, 200), "events": 3, "spans": 2}
    await seed_run(engine, acme, ended=days_ago(400), **kwargs)  # type: ignore[arg-type]
    await seed_run(engine, acme, ended=days_ago(100), **kwargs)  # type: ignore[arg-type]
    await seed_run(engine, acme, ended=days_ago(40), **kwargs)  # type: ignore[arg-type]
    await seed_run(engine, acme, ended=days_ago(10), **kwargs)  # type: ignore[arg-type]
    result = await enforce(retention_engine, store, enforcing())
    obs = build_observability()
    record_tick(obs.catalogue, result)
    sums = await report_sums(engine, "enforce")
    assert value(obs, "retention_passes_total", mode="enforce") == 1
    assert value(obs, "retention_mode", mode="enforce") == 1
    for tier, (runs, events, rows, size) in sums.items():
        assert value(obs, "retention_deleted_total", tier=tier, kind="runs") == runs
        assert value(obs, "retention_deleted_total", tier=tier, kind="events") == events
        assert value(obs, "retention_deleted_total", tier=tier, kind="rows") == rows
        assert value(obs, "retention_deleted_total", tier=tier, kind="bytes") == size
    assert (
        value(obs, "retention_deleted_total", tier="run", kind="runs") == result.deleted_runs == 1
    )
    assert value(obs, "retention_deleted_total", tier="artifacts", kind="bytes") > 0
    assert total(obs, "retention_deleted_total") > 0
    assert total(obs, "retention_failures_total") == 0
    assert total(obs, "retention_dry_run_candidates") == 0


def test_failures_use_fixed_codes_and_a_locked_out_tick_is_not_a_pass() -> None:
    obs = build_observability()
    record_tick(
        obs.catalogue,
        TickResult(
            tick_id=uuid.uuid4(),
            mode="enforce",
            errors=2,
            failed=3,
            refused=True,
            budget_exhausted=True,
        ),
    )
    assert value(obs, "retention_failures_total", code="measure_error") == 2
    assert value(obs, "retention_failures_total", code="step_failed") == 3
    assert value(obs, "retention_failures_total", code="refused") == 1
    assert value(obs, "retention_failures_total", code="budget_exhausted") == 1
    record_crash(obs.catalogue)
    assert value(obs, "retention_failures_total", code="tick_crashed") == 1
    before = value(obs, "retention_passes_total", mode="enforce")
    record_tick(obs.catalogue, TickResult(tick_id=uuid.uuid4(), mode="enforce", locked_out=True))
    assert value(obs, "retention_passes_total", mode="enforce") == before


async def test_the_scrape_reports_the_effective_mode_and_the_age_of_the_last_pass(
    engine: AsyncEngine, retention_engine: AsyncEngine, runtime_database_url: str
) -> None:
    acme = await make_tenant(engine, "acme")
    await seed_run(engine, acme, ended=days_ago(40), events=2, spans=0, jobs=0)
    await tick(retention_engine, settings(retention_mode="dry_run"))
    obs = build_observability()
    runtime = create_engine(runtime_database_url)
    try:
        await ScrapeSampler(lambda: runtime, obs.catalogue).refresh()
    finally:
        await runtime.dispose()
    assert value(obs, "retention_mode", mode="dry_run") == 1
    assert value(obs, "retention_mode", mode="enforce") == 0
    assert value(obs, "retention_seconds_since_last_pass") >= 0
    # The tick's report time is real time (the database clock), so only its sign is asserted.


async def test_the_runner_writes_a_textfile_that_carries_over_between_runs(
    tmp_path: Path, engine: AsyncEngine, retention_database_url: str
) -> None:
    acme = await make_tenant(engine, "acme")
    await seed_run(engine, acme, ended=NOW.replace(year=2020), events=2, spans=0, jobs=0)
    target = tmp_path / "metrics" / "abb_retention.prom"
    target.parent.mkdir()
    cfg = runner.RetentionRunnerSettings(
        retention_mode="dry_run", retention_database_url=retention_database_url
    )
    for expected in (1, 2):
        textfile = TextfileMetrics(target, RETENTION_METRICS)
        await runner.run_once(cfg, textfile)
        families = parse(target.read_text())
        assert set(families) == set(RETENTION_METRICS)
        assert sample_value(families, "retention_passes_total", mode="dry_run") == expected
        assert sample_value(families, "retention_dry_run_candidates", tier="run") == 1
    assert oct(target.stat().st_mode & 0o777) == "0o644"
    assert [p.name for p in target.parent.iterdir()] == ["abb_retention.prom"]


def run_cli(cwd: Path, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - our own interpreter and arguments
        [sys.executable, "-m", "abb_api.cli", "retention", "run-once", *args],
        capture_output=True,
        text=True,
        cwd=cwd,  # not apps/api: no .env is read
        env=env,
        check=False,
    )


async def test_the_cli_flag_and_the_environment_variable_both_name_the_file(
    tmp_path: Path, engine: AsyncEngine, retention_database_url: str
) -> None:
    await make_tenant(engine, "acme")
    flag_file, env_file = tmp_path / "flag.prom", tmp_path / "env.prom"
    base = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(SRC),
        "ABB_ENVIRONMENT": "production",
        "ABB_RETENTION_DATABASE_URL": retention_database_url,
    }
    for extra_args, extra_env, expected in (
        (["--metrics-textfile", str(flag_file)], {}, flag_file),
        ([], {"ABB_METRICS_TEXTFILE": str(env_file)}, env_file),
    ):
        done = run_cli(tmp_path, extra_args, {**base, **extra_env})
        assert done.returncode == 0, done.stderr
        families = parse(expected.read_text())
        assert sample_value(families, "retention_passes_total", mode="dry_run") == 1
        assert retention_database_url not in expected.read_text()

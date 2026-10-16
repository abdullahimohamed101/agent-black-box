"""The textfile writer for short-lived tools: atomic, parseable, closed to the catalogue."""

import os
import stat
from pathlib import Path

import pytest

from abb_api.obs import textfile
from abb_api.obs.textfile import BACKUP_METRICS, TextfileMetrics, textfile_path
from tests.obs.parser import parse, sample_value


def test_the_file_is_atomic_world_readable_and_parses_with_the_scrape_parser(
    tmp_path: Path,
) -> None:
    target = tmp_path / "abb_backup.prom"
    metrics = TextfileMetrics(target, BACKUP_METRICS)
    metrics.catalogue.backup_last_success_timestamp_seconds.set(1_760_000_000)
    metrics.catalogue.backup_runs_total.inc("succeeded")
    assert metrics.write()
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert [p.name for p in tmp_path.iterdir()] == ["abb_backup.prom"], "no temp file is left"
    families = parse(target.read_text())
    assert set(families) == set(BACKUP_METRICS), "only the metrics the tool owns"
    assert sample_value(families, "backup_last_success_timestamp_seconds") == 1_760_000_000
    assert sample_value(families, "backup_runs_total", outcome="succeeded") == 1


def test_a_crash_while_writing_leaves_the_old_file_and_no_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "abb_backup.prom"
    target.write_text("previous\n")

    def boom(*_: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        textfile.write_atomic(target, "new\n")
    assert target.read_text() == "previous\n"
    assert [p.name for p in tmp_path.iterdir()] == ["abb_backup.prom"]


def test_a_write_failure_never_raises_into_the_tool(tmp_path: Path) -> None:
    metrics = TextfileMetrics(tmp_path / "missing-dir" / "x.prom", BACKUP_METRICS)
    assert metrics.write() is False


def test_values_carry_over_between_invocations_so_a_failure_keeps_the_last_success(
    tmp_path: Path,
) -> None:
    target = tmp_path / "abb_backup.prom"
    first = TextfileMetrics(target, BACKUP_METRICS)
    first.catalogue.backup_last_success_timestamp_seconds.set(1_760_000_000)
    first.catalogue.backup_bundle_bytes.set(4096)
    first.catalogue.backup_runs_total.inc("succeeded")
    first.write()
    second = TextfileMetrics(target, BACKUP_METRICS)  # a later run that fails
    second.catalogue.backup_runs_total.inc("failed")
    second.write()
    families = parse(target.read_text())
    assert sample_value(families, "backup_last_success_timestamp_seconds") == 1_760_000_000
    assert sample_value(families, "backup_bundle_bytes") == 4096
    assert sample_value(families, "backup_runs_total", outcome="succeeded") == 1
    assert sample_value(families, "backup_runs_total", outcome="failed") == 1


def test_a_hostile_or_damaged_file_is_ignored_when_seeding(tmp_path: Path) -> None:
    target = tmp_path / "x.prom"
    target.write_text(
        'garbage\nbackup_runs_total{outcome="nonsense"} 5\nbackup_runs_total{evil="1"} 9\n'
        'backup_bundle_bytes not-a-number\nbackup_runs_total{outcome="failed"} -3\n'
        'http_requests_total{method="GET",route="/",status="200"} 4\n'
        "backup_last_duration_seconds +Inf\n"
    )
    metrics = TextfileMetrics(target, BACKUP_METRICS)
    cat = metrics.catalogue
    assert cat.backup_runs_total.value("failed") == 0
    assert cat.backup_runs_total.value("succeeded") == 0
    assert cat.backup_runs_total.value("other") == 5  # a closed label folds an unknown value
    assert cat.backup_bundle_bytes.value() == 0
    assert cat.backup_last_duration_seconds.value() == 0
    assert cat.http_requests_total.value("GET", "/", "200") == 0, "not the tool's metric"


def test_absurd_and_non_finite_seed_values_are_refused(tmp_path: Path) -> None:
    # A writable textfile directory is a trust boundary: 1e308 would make a counter useless
    # forever (every later increment is lost to float precision), inf/nan would poison rate().
    target = tmp_path / "x.prom"
    target.write_text(
        'backup_runs_total{outcome="succeeded"} 1e308\n'
        'backup_runs_total{outcome="failed"} +Inf\n'
        'backup_runs_total{outcome="other"} NaN\n'
        "backup_bundle_bytes 1e308\n"
        "backup_last_duration_seconds NaN\n"
        "backup_last_success_timestamp_seconds 1700000000\n"
    )
    cat = TextfileMetrics(target, BACKUP_METRICS).catalogue
    assert cat.backup_runs_total.value("succeeded") == 0
    assert cat.backup_runs_total.value("failed") == 0
    assert cat.backup_runs_total.value("other") == 0
    assert cat.backup_bundle_bytes.value() == 0
    assert cat.backup_last_duration_seconds.value() == 0
    assert cat.backup_last_success_timestamp_seconds.value() == 1_700_000_000  # sane: kept


def test_path_comes_from_the_flag_first_then_the_environment() -> None:
    assert textfile_path("/a/b.prom", {"ABB_METRICS_TEXTFILE": "/c.prom"}) == Path("/a/b.prom")
    assert textfile_path(None, {"ABB_METRICS_TEXTFILE": "/c.prom"}) == Path("/c.prom")
    assert textfile_path(None, {"ABB_METRICS_TEXTFILE": "  "}) is None
    assert textfile_path(None, {}) is None


def test_disabled_is_a_no_op() -> None:
    metrics = TextfileMetrics(None, BACKUP_METRICS)
    assert not metrics.enabled and metrics.write()

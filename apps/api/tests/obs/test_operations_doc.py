"""The metrics table in docs/OPERATIONS.md lists exactly the catalogue (no drift in either way)."""

import re
from pathlib import Path
from typing import Any

import yaml

from abb_api.obs.catalogue import build_observability

OPERATIONS = Path(__file__).resolve().parents[4] / "docs" / "OPERATIONS.md"
ROW = re.compile(r"^\| `([a-z][a-z0-9_]*)` \| (counter|gauge|histogram) \| ([^|]*) \|", re.M)


def test_every_catalogue_metric_is_documented_with_its_type_and_labels() -> None:
    documented = {
        name: (kind, labels.strip()) for name, kind, labels in ROW.findall(OPERATIONS.read_text())
    }
    declared = build_observability().registry.metrics
    assert set(documented) == set(declared), (
        f"missing from the doc: {sorted(set(declared) - set(documented))}; "
        f"not in the catalogue: {sorted(set(documented) - set(declared))}"
    )
    for name, metric in declared.items():
        kind, labels = documented[name]
        assert kind == metric.type, name
        assert labels == (", ".join(metric.label_names) or "-"), name


RULES = Path(__file__).resolve().parents[4] / "deploy/observability/prometheus/rules/ops.yml"
RUNBOOKS = Path(__file__).resolve().parents[4] / "docs" / "runbooks"


def _expr(alert: str) -> str:
    groups: list[dict[str, Any]] = yaml.safe_load(RULES.read_text())["groups"]
    for group in groups:
        for rule in group["rules"]:
            if rule.get("alert") == alert:
                return str(rule["expr"])
    raise AssertionError(alert)


def test_backup_age_thresholds_in_the_docs_are_the_ones_the_rules_ship() -> None:
    too_old = int(re.search(r"> (\d+) \* 3600", _expr("AbbBackupTooOld")).group(1))  # type: ignore[union-attr]
    very_old = int(re.search(r"> (\d+) \* 3600", _expr("AbbBackupVeryOld")).group(1))  # type: ignore[union-attr]
    operations = OPERATIONS.read_text()
    runbook = (RUNBOOKS / "backup-restore.md").read_text()
    assert f"`AbbBackupTooOld` ({too_old} hours, ticket)" in runbook
    assert f"`AbbBackupVeryOld` ({very_old} hours, page)" in runbook
    assert f"time() - backup_last_success_timestamp_seconds > {too_old} * 3600" in operations
    # No stale hard-coded second counts for the same alert survive in the prose.
    assert "> 100000" not in operations


def test_worker_lag_and_stream_thresholds_in_the_runbooks_are_the_rules() -> None:
    lag_high = re.search(r"> (\d+)\n", _expr("AbbWorkerLagHigh") + "\n")
    lag_severe = re.search(r"> (\d+)\n", _expr("AbbWorkerLagSevere") + "\n")
    streams = re.search(r">= (\d+)\n", _expr("AbbStreamConnectionsNearLimit") + "\n")
    assert lag_high and lag_severe and streams
    worker = (RUNBOOKS / "worker-lag.md").read_text()
    stream = (RUNBOOKS / "stream-issues.md").read_text()
    assert f"oldest due job over {lag_high.group(1)} s" in worker
    assert f"(over {lag_severe.group(1)} s, page)" in worker
    assert f"{streams.group(1)} or more open streams" in stream


def test_quota_refusals_alert_is_a_sustained_rate_not_any_single_refusal() -> None:
    groups: list[dict[str, Any]] = yaml.safe_load(RULES.read_text())["groups"]
    rule = next(r for g in groups for r in g["rules"] if r.get("alert") == "AbbQuotaRefusals")
    expr = " ".join(str(rule["expr"]).split())
    assert expr.startswith("sum by (quota) (rate(quota_refusals_total[")
    assert "increase(" not in expr
    threshold = float(expr.rsplit(">", 1)[1])
    assert threshold > 0  # "> 0" would be any single refusal
    assert str(rule["for"]) not in {"0m", "0s", "0"}
    assert rule["labels"]["severity"] == "ticket"
    runbook = (RUNBOOKS / "hardening.md").read_text()
    assert f"over {threshold} a second" in runbook

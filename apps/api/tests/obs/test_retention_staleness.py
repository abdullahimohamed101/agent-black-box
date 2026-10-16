"""AbbRetentionStale must not fire when retention is intentionally off (review finding P3-4).

`retention_mode` is the mode of the newest report, and an `off` retention process writes none, so
the old rule (`unless retention_mode{mode="off"}`) fired three hours after retention was switched
off. The rule now reads `retention_configured_mode`, set from the settings of the API and worker.
"""

import math
from pathlib import Path
from typing import Any

import yaml

from abb_api.obs.catalogue import RETENTION_MODES, build_observability
from abb_api.obs.scrape import ScrapeSampler
from tests.obs.helpers import client_for, make_app, scrape
from tests.obs.parser import sample_value

RULES = Path(__file__).resolve().parents[4] / "deploy/observability/prometheus/rules/ops.yml"
STALE_AFTER_SECONDS = 10800


def _stale_expr() -> str:
    groups: list[dict[str, Any]] = yaml.safe_load(RULES.read_text())["groups"]
    for group in groups:
        for rule in group["rules"]:
            if rule.get("alert") == "AbbRetentionStale":
                return str(rule["expr"])
    raise AssertionError("AbbRetentionStale is gone")


def fires(*, age: float, configured: str | None) -> bool:
    """The documented decision table of the rule, in plain Python.

    NaN age (no report ever written) never fires; an age over 3 hours fires unless the process is
    configured with `off`; a process that exports no configured mode (a series absent) does not
    suppress the alert.
    """
    if math.isnan(age) or age <= STALE_AFTER_SECONDS:
        return False
    return configured != "off"


def test_the_decision_table() -> None:
    assert not fires(age=math.nan, configured="enforce")
    assert not fires(age=3600, configured="dry_run")
    assert fires(age=STALE_AFTER_SECONDS + 1, configured="dry_run")
    assert fires(age=STALE_AFTER_SECONDS + 1, configured="enforce")
    assert fires(age=STALE_AFTER_SECONDS + 1, configured=None)
    assert not fires(age=10**7, configured="off")  # switched off long ago: silent


def test_the_rule_implements_that_table_with_the_configured_mode() -> None:
    expr = " ".join(_stale_expr().split())
    assert f"max(retention_seconds_since_last_pass) > {STALE_AFTER_SECONDS}" in expr
    assert 'unless on () (max(retention_configured_mode{mode="off"}) == 1)' in expr
    assert "retention_mode{" not in expr  # the newest report's mode says nothing about config


def test_the_sampler_exports_the_configured_mode() -> None:
    for mode in RETENTION_MODES:
        cat = build_observability().catalogue
        ScrapeSampler(lambda: None, cat, retention_mode=mode)
        for other in RETENTION_MODES:
            assert cat.retention_configured_mode.value(other) == (1.0 if other == mode else 0.0)
    unset = build_observability().catalogue
    ScrapeSampler(lambda: None, unset)
    assert list(unset.retention_configured_mode.collect()) == []


async def test_the_api_exports_its_configured_retention_mode() -> None:
    app = make_app(retention_mode="off")
    async with client_for(app) as client:
        families = await scrape(client)
    for mode, expected in (("off", 1.0), ("dry_run", 0.0), ("enforce", 0.0)):
        assert sample_value(families, "retention_configured_mode", mode=mode) == expected

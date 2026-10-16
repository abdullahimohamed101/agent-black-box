"""The catalogue is internally consistent and its exposition parses back."""

import re

from abb_api.limits.scopes import Scope
from abb_api.obs import catalogue as c
from abb_api.obs.catalogue import build_observability
from abb_api.retention.model import ReportTier
from tests.obs.parser import parse

FORBIDDEN_LABELS = {
    "workspace_id",
    "workspace",
    "project_id",
    "project",
    "user_id",
    "user",
    "key_id",
    "key",
    "path",
    "url",
    "address",
    "ip",
    "client",
    "email",
    "run_id",
    "agent",
    "session_id",
    "token",
}
SNAKE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
BASE_UNIT_WORDS = {"milliseconds", "minutes", "hours", "days", "kilobytes", "megabytes", "ms"}


def test_names_and_labels_follow_the_conventions() -> None:
    obs = build_observability()
    assert obs.registry.metrics, "empty catalogue"
    for name, metric in obs.registry.metrics.items():
        assert SNAKE.match(name), name
        assert not (set(name.split("_")) & BASE_UNIT_WORDS), f"{name}: use seconds or bytes"
        if metric.type == "counter":
            assert name.endswith("_total"), f"{name}: counters end in _total"
        else:
            assert not name.endswith("_total"), f"{name}: only counters end in _total"
        if metric.type == "histogram":
            assert name.endswith(("_seconds", "_bytes")), name
        assert metric.help.strip() and metric.help.endswith("."), f"{name}: help text"
        for label in metric.labels:
            assert label.name not in FORBIDDEN_LABELS, f"{name}: forbidden label {label.name}"
            assert SNAKE.match(label.name)


def test_every_label_is_closed_or_capped_with_a_sane_bound() -> None:
    for metric in build_observability().registry.metrics.values():
        for label in metric.labels:
            if label.values is not None:
                assert len(label.values) <= 600
            else:
                assert 0 < label.max_values <= 400, f"{metric.name}.{label.name}"


def test_catalogue_object_and_registry_agree() -> None:
    obs = build_observability()
    declared = set(obs.registry.metrics)
    fields = {f for f in vars(obs.catalogue)}
    assert declared == fields, "every catalogue field is a registry metric and vice versa"


def test_the_literal_closed_sets_match_their_source_enums() -> None:
    assert set(c.LIMITER_SCOPES) == {s.value for s in Scope}
    assert set(c.RETENTION_TIERS) == {t.value for t in ReportTier}


def test_exposition_parses_back_and_declares_every_metric() -> None:
    obs = build_observability()
    obs.catalogue.http_requests_total.inc("GET", "/healthz", "200")
    obs.catalogue.http_request_duration_seconds.observe(0.02, "GET", "/healthz")
    obs.catalogue.worker_jobs_pending.set(3, "summarize_run")
    families = parse(obs.registry.render())
    assert set(families) == set(obs.registry.metrics)
    for name, family in families.items():
        assert family.type == obs.registry.metrics[name].type
        assert family.help
    assert families["process_start_time_seconds"].samples[0][2] > 1.7e9
    assert families["process_uptime_seconds"].samples[0][2] >= 0
    assert families["abb_build_info"].samples[0][1].keys() == {"version"}


def test_two_registries_do_not_share_state() -> None:
    a, b = build_observability(), build_observability()
    a.catalogue.invitation_only_refusals_total.inc()
    assert b.catalogue.invitation_only_refusals_total.value() == 0

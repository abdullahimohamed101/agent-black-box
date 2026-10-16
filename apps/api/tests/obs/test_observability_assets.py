"""The shipped observability assets agree with the metric catalogue, the runbooks and each other.

Phase 19c D6/D7 (ADR-171). No network, no docker, no promtool needed (it is used when present).
The rules, the dashboard, the compose profile and the runbooks are plain files, and the catalogue
(`abb_api/obs/catalogue.py`) is the single source of truth for every metric name and label.
"""

import json
import math
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from abb_api.obs.catalogue import build_observability

ROOT = Path(__file__).resolve().parents[4]
OBS = ROOT / "deploy" / "observability"
RULES = OBS / "prometheus" / "rules"
DASHBOARD = OBS / "grafana" / "dashboards" / "agent-black-box.json"
RUNBOOKS = ROOT / "docs" / "runbooks"
COMPOSE = ROOT / "docker-compose.yml"

CATALOGUE = dict(build_observability().registry.metrics)
# Labels Prometheus itself adds to every scraped series.
SCRAPE_LABELS = {"job", "instance"}
KEYWORDS = {
    "and", "or", "unless", "by", "without", "on", "ignoring", "group_left", "group_right",
    "bool", "offset", "inf", "nan",
}  # fmt: skip
SELECTOR = re.compile(r"([A-Za-z_:][A-Za-z0-9_:]*)?\s*\{([^{}]*)\}")
MATCHER = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*(?:=~|!~|!=|=)\s*"(?:[^"\\]|\\.)*"')
GROUPING = re.compile(r"\b(?:by|without|on|ignoring|group_left|group_right)\s*\(([^()]*)\)")
TOKEN = re.compile(r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|([A-Za-z_:][A-Za-z0-9_:]*)(\s*\()?")
TEMPLATE_LABEL = re.compile(r"\$labels\.([A-Za-z_][A-Za-z0-9_]*)")
LEGEND_LABEL = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def _load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text())


def _rule_files() -> list[Path]:
    return sorted(RULES.glob("*.yml"))


def _rules() -> Iterator[tuple[str, dict[str, Any]]]:
    for path in _rule_files():
        for group in _load_yaml(path)["groups"]:
            for rule in group["rules"]:
                yield path.name, rule


def _recorded_names() -> set[str]:
    return {r["record"] for f, r in _rules() if f == "slo.yml" and "record" in r}


def _alerts() -> list[dict[str, Any]]:
    return [r for _, r in _rules() if "alert" in r]


class Analysis:
    """The identifiers of one PromQL expression, split into metric names and label names."""

    def __init__(self, expr: str) -> None:
        self.metrics: set[str] = set()
        self.selector_labels: list[tuple[str | None, str]] = []
        self.group_labels: set[str] = set()

        def selector(m: re.Match[str]) -> str:
            name = m.group(1)
            if name:
                self.metrics.add(name)
            for matcher in MATCHER.finditer(m.group(2)):
                self.selector_labels.append((name, matcher.group(1)))
            return " "

        def grouping(m: re.Match[str]) -> str:
            self.group_labels.update(x.strip() for x in m.group(1).split(",") if x.strip())
            return " "

        text = SELECTOR.sub(selector, expr)
        text = GROUPING.sub(grouping, text)
        text = re.sub(r"\[[^\]]*\]", " ", text)
        text = re.sub(r'"(?:[^"\\]|\\.)*"', " ", text)
        for token in TOKEN.finditer(text):
            name, call = token.group(1), token.group(2)
            if name and not call and name not in KEYWORDS:
                self.metrics.add(name)


def _labels_of(name: str) -> set[str] | None:
    """The label names a series called `name` can have, or None if the name is unknown."""
    if name in CATALOGUE:
        return set(CATALOGUE[name].label_names) | SCRAPE_LABELS
    if name == "up":
        return set(SCRAPE_LABELS)
    if name == "ALERTS":
        return {"alertname", "alertstate", "severity"} | SCRAPE_LABELS
    if name in _recorded_names():
        return set(SCRAPE_LABELS)
    for suffix in ("_bucket", "_sum", "_count"):
        base = name.removesuffix(suffix)
        if name.endswith(suffix) and base in CATALOGUE and CATALOGUE[base].type == "histogram":
            extra = {"le"} if suffix == "_bucket" else set()
            return set(CATALOGUE[base].label_names) | SCRAPE_LABELS | extra
    return None


def _check_expr(expr: str, where: str) -> set[str]:
    """Fail on an unknown metric or label; return every label name the expression may carry."""
    analysis = Analysis(expr)
    allowed: set[str] = set(analysis.group_labels)
    for name in sorted(analysis.metrics):
        labels = _labels_of(name)
        assert labels is not None, f"{where}: unknown metric {name!r} in {expr!r}"
        allowed |= labels
    for selected, label in analysis.selector_labels:
        assert selected is not None, f"{where}: selector without a metric name in {expr!r}"
        declared = _labels_of(selected)
        assert declared is not None and label in declared, (
            f"{where}: {selected!r} has no label {label!r} (declared: {sorted(declared or [])})"
        )
    for label in analysis.group_labels:
        union = set().union(*(_labels_of(n) or set() for n in analysis.metrics)) | {"le"}
        assert label in union, f"{where}: grouping label {label!r} is on none of its metrics"
    return allowed


def _dashboard() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(DASHBOARD.read_text())
    return data


def _dashboard_panels() -> list[dict[str, Any]]:
    panels: list[dict[str, Any]] = _dashboard()["panels"]
    return panels


# ---- (a) files parse -------------------------------------------------------------------------


def test_yaml_and_json_assets_parse() -> None:
    files = [*OBS.rglob("*.yml"), *OBS.rglob("*.yaml")]
    assert len(files) >= 5
    for path in files:
        assert _load_yaml(path) is not None, path
    assert isinstance(_dashboard(), dict)
    assert _rule_files(), "no rule files"


# ---- (b)(c) metrics and labels in rules and dashboard -----------------------------------------


def test_recorded_names_are_namespaced_and_unique() -> None:
    names = [r["record"] for f, r in _rules() if "record" in r]
    assert len(names) == len(set(names))
    assert all(re.fullmatch(r"abb:[a-z0-9_]+(:[a-z0-9_]+)?", n) for n in names), names
    assert not any("record" in r for f, r in _rules() if f != "slo.yml"), (
        "recording rules live in slo.yml only (the allow-list of derived names is read from it)"
    )


def test_rule_expressions_use_only_catalogue_metrics_and_labels() -> None:
    seen = 0
    for file, rule in _rules():
        name = rule.get("alert") or rule["record"]
        _check_expr(rule["expr"], f"{file}:{name}")
        seen += 1
    assert seen >= 30


def test_dashboard_expressions_use_only_catalogue_metrics_and_labels() -> None:
    seen = 0
    for panel in _dashboard_panels():
        for target in panel.get("targets", []):
            allowed = _check_expr(target["expr"], f"dashboard:{panel['title']}")
            for label in LEGEND_LABEL.findall(target.get("legendFormat", "")):
                assert label in allowed, f"{panel['title']}: legend uses {label!r}"
            seen += 1
    assert seen >= 40


def test_tokenizer_catches_a_typo() -> None:
    with pytest.raises(AssertionError, match="unknown metric"):
        _check_expr("sum(rate(ingest_acks_totl[5m]))", "t")
    with pytest.raises(AssertionError, match="no label"):
        _check_expr('sum(rate(ingest_acks_total{outcom="error"}[5m]))', "t")
    with pytest.raises(AssertionError, match="grouping label"):
        _check_expr("sum by (workspace_id) (rate(ingest_acks_total[5m]))", "t")
    assert _check_expr(
        "histogram_quantile(0.9, sum by (le) (rate(sse_delivery_lag_seconds_bucket[1m])))", "t"
    )


def test_no_rule_or_panel_groups_by_a_tenant_identifier() -> None:
    forbidden = re.compile(r"workspace|project|tenant|user|actor|key_id|address", re.I)
    exprs = [r["expr"] for _, r in _rules()]
    exprs += [t["expr"] for p in _dashboard_panels() for t in p.get("targets", [])]
    for expr in exprs:
        assert not forbidden.search("".join(Analysis(expr).group_labels)), expr
        assert not any(forbidden.search(lbl) for _, lbl in Analysis(expr).selector_labels), expr


# ---- (d) alerts ------------------------------------------------------------------------------


def _slug(heading: str) -> str:
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading).replace("`", "").strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    in_code = False
    for line in path.read_text().splitlines():
        if line.startswith("```"):
            in_code = not in_code
        match = None if in_code else re.match(r"#{1,6}\s+(.*?)\s*#*\s*$", line)
        if match:
            slug = _slug(match.group(1))
            n = counts.get(slug, 0)
            counts[slug] = n + 1
            anchors.add(slug if n == 0 else f"{slug}-{n}")
    return anchors


def test_every_alert_has_severity_annotations_and_a_resolving_runbook() -> None:
    alerts = _alerts()
    names = [a["alert"] for a in alerts]
    assert len(names) == len(set(names)) and len(names) >= 25
    for alert in alerts:
        name = alert["alert"]
        assert re.fullmatch(r"Abb[A-Za-z0-9]+", name), name
        assert alert["labels"]["severity"] in {"page", "ticket"}, name
        assert "for" in alert, f"{name}: state `for:` explicitly (0m is allowed)"
        ann = alert["annotations"]
        for key in ("summary", "description", "runbook_url"):
            assert str(ann.get(key, "")).strip(), f"{name}: missing {key}"
        assert len(ann["description"]) > 80, f"{name}: say why the threshold is what it is"
        match = re.fullmatch(
            r"docs/runbooks/([A-Za-z0-9-]+\.md)(?:#([a-z0-9_-]+))?", ann["runbook_url"]
        )
        assert match, f"{name}: runbook_url {ann['runbook_url']!r}"
        target = RUNBOOKS / match.group(1)
        assert target.is_file(), f"{name}: {target} does not exist"
        if match.group(2):
            assert match.group(2) in _anchors(target), (
                f"{name}: no heading for #{match.group(2)} in {target.name}"
            )


def test_template_labels_in_annotations_exist_on_the_alert_series() -> None:
    for alert in _alerts():
        allowed = _check_expr(alert["expr"], alert["alert"])
        text = " ".join(str(v) for v in alert["annotations"].values())
        for label in TEMPLATE_LABEL.findall(text):
            assert label in allowed, f"{alert['alert']}: $labels.{label} is not on the series"


def test_pages_are_fewer_than_tickets_and_every_alert_is_in_the_runbook_index() -> None:
    index = (RUNBOOKS / "README.md").read_text()
    alerts = _alerts()
    for alert in alerts:
        assert f"`{alert['alert']}`" in index, (
            f"{alert['alert']} is missing from runbooks/README.md"
        )
    pages = [a for a in alerts if a["labels"]["severity"] == "page"]
    assert 0 < len(pages) < len(alerts) / 2, "paging should stay rare"


# ---- (e) runbooks ----------------------------------------------------------------------------

# Words that start like a catalogue metric but are tables, settings or reasons, not metrics.
NOT_METRICS = {
    # tables and columns
    "alert_notifications",
    "retention_run_progress",
    "retention_reports",
    "alert_id",
    # database roles and migration names
    "abb_runtime",
    "abb_retention",
    "abb_p7",
    # label values (reasons and codes), which are not metric names
    "worker_lag",
    "session_start",
    "http_3xx",
    "http_4xx",
    "http_5xx",
    "process_full",
    "quota_exceeded",
    "artifact_store",
}
LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)\)")
BACKTICK = re.compile(r"`([^`\n]+)`")


def _prefixes() -> set[str]:
    return {name.split("_")[0] for name in CATALOGUE}


def _runbook_files() -> list[Path]:
    return sorted(RUNBOOKS.glob("*.md"))


def test_runbook_links_resolve_and_anchors_exist() -> None:
    checked = 0
    for path in _runbook_files():
        in_code = False
        for line in path.read_text().splitlines():
            if line.startswith("```"):
                in_code = not in_code
            if in_code:
                continue
            for target in LINK.findall(line):
                if re.match(r"[a-z]+:", target):
                    continue
                file_part, _, anchor = target.partition("#")
                dest = path if not file_part else (path.parent / file_part).resolve()
                assert dest.exists(), f"{path.name}: link target {target!r} does not exist"
                if anchor and dest.suffix == ".md":
                    assert anchor in _anchors(dest), f"{path.name}: no heading {target!r}"
                checked += 1
    assert checked > 40


def test_metric_and_alert_names_in_runbooks_exist() -> None:
    alert_names = {a["alert"] for a in _alerts()}
    prefixes = _prefixes()
    problems: list[str] = []
    for path in _runbook_files():
        in_code = False
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if line.startswith("```"):
                in_code = not in_code
            if in_code:
                continue
            for token in BACKTICK.findall(line):
                match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_:]*)(\{[^`]*\})?", token)
                if not match:
                    continue
                ident = match.group(1)
                if ident.startswith("Abb") and ident[3:4].isupper():
                    if ident not in alert_names:
                        problems.append(f"{path.name}:{number} alert {ident}")
                elif ident.split("_")[0] in prefixes and "_" in ident and ident == ident.lower():
                    if ident not in NOT_METRICS and _labels_of(ident) is None:
                        problems.append(f"{path.name}:{number} metric {ident}")
                    elif match.group(2) and _labels_of(ident) is not None:
                        _check_expr(token, f"{path.name}:{number}")
    assert not problems, "\n".join(problems)


def test_runbook_index_links_every_runbook() -> None:
    index = (RUNBOOKS / "README.md").read_text()
    for path in _runbook_files():
        if path.name != "README.md":
            assert f"({path.name}" in index, f"{path.name} is not linked from runbooks/README.md"


# ---- (f) dashboard ---------------------------------------------------------------------------


def test_dashboard_structure() -> None:
    dash = _dashboard()
    assert dash["uid"] == "agent-black-box"
    assert dash["schemaVersion"] >= 39
    variables = {v["name"]: v for v in dash["templating"]["list"]}
    assert variables["datasource"]["type"] == "datasource"
    assert variables["datasource"]["query"] == "prometheus"
    panels = dash["panels"]
    ids = [p["id"] for p in panels]
    assert len(ids) == len(set(ids)), "panel ids must be unique"
    rows = [p["title"] for p in panels if p["type"] == "row"]
    assert rows == [
        "Overview and SLO",
        "Ingestion",
        "Worker and jobs",
        "Database and dependencies",
        "Controls: limiter, throttles, quotas, body cap",
        "Retention and backups",
        "Alerts and streams",
    ]
    for panel in panels:
        if panel["type"] == "row":
            continue
        assert panel["title"].strip() and panel["description"].strip(), panel["id"]
        assert panel["datasource"]["uid"] == "${datasource}", panel["id"]
        for target in panel["targets"]:
            assert target["datasource"]["uid"] == "${datasource}"
            assert target["expr"].strip()
    for panel in panels:
        grid = panel["gridPos"]
        assert grid["x"] + grid["w"] <= 24


def test_dashboard_has_no_tenant_identifiers() -> None:
    text = DASHBOARD.read_text()
    assert not re.search(r"\b(ws|prj|run|key|usr)_[A-Za-z0-9]{6,}", text)
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)
    assert "workspace_id" not in text and "project_id" not in text


def test_dashboard_references_every_alert_signal_row() -> None:
    """The runbook index names dashboard rows: they must be real rows."""
    rows = {p["title"] for p in _dashboard_panels() if p["type"] == "row"}
    index = (RUNBOOKS / "README.md").read_text()
    named = {
        cell.strip().strip("|").strip()
        for line in index.splitlines()
        if line.startswith("| ") and line.count("|") >= 5
        for cell in [line.rstrip().rstrip("|").rsplit("|", 1)[-1]]
    }
    named -= {"Dashboard row", "---"}
    short_rows = {r.split(":")[0] for r in rows}
    assert named, "no dashboard rows found in the index"
    assert named <= short_rows, f"index names rows that do not exist: {named - short_rows}"


# ---- (g) compose profile and Prometheus config -----------------------------------------------


def test_compose_observability_profile() -> None:
    compose = _load_yaml(COMPOSE)
    services = {
        name: svc
        for name, svc in compose["services"].items()
        if "observability" in svc.get("profiles", [])
    }
    assert set(services) == {"prometheus", "grafana"}
    for name, svc in services.items():
        image = svc["image"]
        assert ":" in image and not image.endswith(":latest"), f"{name}: pin the image tag"
        assert svc["profiles"] == ["observability"]
        for port in svc["ports"]:
            assert str(port).startswith("127.0.0.1:"), f"{name}: {port} is not loopback only"
        for volume in svc.get("volumes", []):
            if isinstance(volume, str) and volume.startswith("./"):
                source = volume.split(":")[0]
                assert (ROOT / source).exists() or source == "./.local/metrics-token", volume
                assert volume.endswith(":ro"), f"{name}: {volume} should be read-only"
    mounts = services["prometheus"]["volumes"]
    assert "./.local/metrics-token:/etc/prometheus/abb-metrics-token:ro" in mounts
    # Prometheus runs as the invoking (non-root) user, so its in-memory data directory must be
    # world-writable: a recent Docker mounts a bare tmpfs 0775 root:root and Prometheus panics on
    # /prometheus/queries.active (found by scripts/obs-e2e.sh).
    assert services["prometheus"]["tmpfs"] == ["/prometheus:mode=1777"]
    env = services["grafana"]["environment"]
    assert env["GF_AUTH_ANONYMOUS_ENABLED"] == "false"
    assert env["GF_USERS_ALLOW_SIGN_UP"] == "false"
    # No other service gained a profile-less dependency on the observability stack.
    for name, svc in compose["services"].items():
        if name not in services:
            assert "observability" not in svc.get("profiles", [])


def test_prometheus_config_reads_the_token_from_a_file_and_finds_the_rules() -> None:
    config = _load_yaml(OBS / "prometheus" / "prometheus.yml")
    jobs = {job["job_name"]: job for job in config["scrape_configs"]}
    assert {"abb-api", "abb-worker"} <= set(jobs)
    for job in jobs.values():
        auth = job["authorization"]
        assert auth["credentials_file"] == "/etc/prometheus/abb-metrics-token"
        assert "credentials" not in auth
        assert "bearer_token" not in job
    text = (OBS / "prometheus" / "prometheus.yml").read_text()
    assert not re.search(r"^\s*(credentials|bearer_token|password):", text, re.M)
    assert config["rule_files"] == ["/etc/prometheus/rules/*.yml"]
    compose = _load_yaml(COMPOSE)
    rules_mount = "./deploy/observability/prometheus/rules:/etc/prometheus/rules:ro"
    assert rules_mount in compose["services"]["prometheus"]["volumes"]
    provisioned = compose["services"]["grafana"]["volumes"]
    assert "./deploy/observability/grafana/provisioning:/etc/grafana/provisioning:ro" in provisioned
    assert "./deploy/observability/grafana/dashboards:/var/lib/grafana/dashboards:ro" in provisioned
    provider = _load_yaml(OBS / "grafana" / "provisioning" / "dashboards" / "abb.yml")
    assert provider["providers"][0]["options"]["path"] == "/var/lib/grafana/dashboards"


def test_make_targets_and_gitignore_for_the_token() -> None:
    makefile = (ROOT / "Makefile").read_text()
    for target in ("observability-token", "observability-up", "observability-down"):
        assert re.search(rf"^{target}:", makefile, re.M), target
    assert "openssl rand -hex 24" in makefile
    assert re.search(r"umask 077", makefile) and "chmod 600" in makefile
    assert ".local/" in (ROOT / ".gitignore").read_text().splitlines()


# ---- (h) burn-rate arithmetic ----------------------------------------------------------------

DURATION = {"5m": 300, "30m": 1800, "1h": 3600, "2h": 7200, "6h": 21600, "1d": 86400, "3d": 259200}
BURN_ALERTS = {
    # name: (burn rate, long window, short window, severity)
    "AbbIngestErrorBudgetBurnFast": (14.4, "1h", "5m", "page"),
    "AbbIngestErrorBudgetBurnSustained": (6.0, "6h", "30m", "page"),
    "AbbIngestErrorBudgetBurnSlow": (3.0, "1d", "2h", "ticket"),
    "AbbIngestErrorBudgetBurnBaseline": (1.0, "3d", "6h", "ticket"),
}


def test_burn_rate_thresholds_follow_from_the_slo_target() -> None:
    slo = next(r for f, r in _rules() if r.get("record") == "abb:ingest_slo:target_ratio")
    target = float(re.fullmatch(r"vector\(([0-9.]+)\)", slo["expr"].strip()).group(1))  # type: ignore[union-attr]
    assert target == 0.9995, "spec 117: 99.95% over 30 days"
    budget = round(1 - target, 6)
    assert budget == 0.0005
    by_name = {a["alert"]: a for a in _alerts()}
    month = 30 * 86400
    for name, (burn, long_w, short_w, severity) in BURN_ALERTS.items():
        alert = by_name[name]
        assert alert["labels"]["severity"] == severity
        assert float(alert["labels"]["burn_rate"]) == burn
        expected = round(burn * budget, 6)
        thresholds = [float(x) for x in re.findall(r">\s*([0-9.]+)", alert["expr"])]
        assert thresholds == [expected, expected], (name, thresholds, expected)
        windows = re.findall(r"abb:ingest_error_ratio:rate(\w+)", alert["expr"])
        assert windows == [long_w, short_w], (name, windows)
        # The short window is a twelfth of the long one, so the alert resets within ~1/12 of it.
        assert DURATION[long_w] == 12 * DURATION[short_w], name
        # Budget share consumed in the long window at this burn rate: 2%, 5%, 10%, 10%.
        spent = burn * DURATION[long_w] / month
        assert math.isclose(spent, {"1h": 0.02, "6h": 0.05, "1d": 0.1, "3d": 0.1}[long_w])
    assert [BURN_ALERTS[n][0] * budget for n in BURN_ALERTS] == pytest.approx(
        [0.0072, 0.003, 0.0015, 0.0005]
    )


def test_every_burn_window_has_a_recording_rule() -> None:
    recorded = _recorded_names()
    for _, long_w, short_w, _ in BURN_ALERTS.values():
        for window in (long_w, short_w):
            assert f"abb:ingest_error_ratio:rate{window}" in recorded, window


def test_sli_excludes_client_refusals() -> None:
    """The ratio's denominator is committed + error; `rejected` (4xx) never appears in it."""
    for _file, rule in _rules():
        if str(rule.get("record", "")).startswith("abb:ingest_error_ratio:"):
            expr = " ".join(rule["expr"].split())
            assert 'outcome=~"committed|error"' in expr, rule["record"]
            assert "rejected" not in expr, rule["record"]


# ---- promtool, when installed ----------------------------------------------------------------


@pytest.mark.skipif(shutil.which("promtool") is None, reason="promtool is not installed")
def test_promtool_accepts_the_rules() -> None:
    result = subprocess.run(  # noqa: S603
        ["promtool", "check", "rules", *map(str, _rule_files())],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

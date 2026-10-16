"""Gate 5 of scripts/obs-e2e.sh (D9 gates 1 and 6, against REAL software): the shipped Prometheus configuration and rules, and the Grafana
provisioning and dashboard, are exercised by the pinned images, not by our own parsers.

  1. a disk guard (the container VM must have 3 GB free) BEFORE any pull; only the two pinned images are ever pulled; nothing is pruned
  2. `promtool check config` and `promtool check rules` on the COMMITTED files
  3. a private compose project (`abb-p19c-obs`) built from the committed `docker-compose.yml` observability profile plus an override
     file written here (ports, a test copy of the scrape config and rules with short intervals, the token file, host.docker.internal):
     the committed files are never modified
  4. bounded polling: both targets UP, every rule group loaded, every rule healthy with no evaluation error, every recording rule
     produces a series after traffic, EVERY expression of the rules and of the dashboard executes (status success), Grafana is
     healthy, the datasource and the dashboard are provisioned (fetched by uid; rows and panels equal the file), every dashboard
     expression also runs through Grafana's datasource proxy, and AbbTargetDown really FIRES when the worker target is stopped
  5. teardown of what this gate created (containers, network, volume); the pulled images stay cached for the next run
"""

import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

import obs_e2e_lib as lib
from obs_e2e_lib import check, die, gate, info

OBS = lib.ROOT / "deploy" / "observability"
PROJECT = "abb-p19c-obs"
PROM_PORT = int(os.environ.get("OBS_E2E_PROM_PORT", "9290"))
GRAFANA_PORT = int(os.environ.get("OBS_E2E_GRAFANA_PORT", "3291"))
MIN_FREE_KIB = 3 * 1024 * 1024
SHORT = "2s"
FIRE_AFTER = "6s"  # the committed rule says 3m; the copy shortens only this `for` so the test finishes (the expression is compared equal)


def _images() -> dict[str, str]:
    compose = yaml.safe_load((lib.ROOT / "docker-compose.yml").read_text())
    return {name: str(compose["services"][name]["image"]) for name in ("prometheus", "grafana")}


def _present(image: str) -> bool:
    return lib.docker("image", "inspect", image).returncode == 0


def _vm_free_kib(candidates: list[str]) -> int | None:
    """Free space of the container VM's root filesystem, as a container sees it (`df` of /), via an image that is already local."""
    for image in candidates:
        if not _present(image):
            continue
        done = lib.docker("run", "--rm", "--entrypoint", "df", image, "-Pk", "/")
        if done.returncode == 0:
            rows = done.stdout.strip().splitlines()
            if len(rows) >= 2:
                columns = rows[-1].split()
                try:
                    return int(columns[3])
                except (IndexError, ValueError):
                    continue
    return None


def _disk_guard(images: dict[str, str]) -> list[str]:
    """Fails the gate (never prunes, never deletes) unless pulling is safe. Returns the images that will be pulled."""
    to_pull = [image for image in images.values() if not _present(image)]
    free = _vm_free_kib([*images.values(), "postgres:16-alpine", "alpine", "busybox"])
    if to_pull:
        if free is None:
            die(f"cannot measure the container VM's free disk (no local image with `df`); refusing to pull {to_pull} blind. Free space or pull them yourself.")
        if free < MIN_FREE_KIB:
            die(f"the container VM has only {free / 1048576:.1f} GB free; pulling {to_pull} needs {MIN_FREE_KIB / 1048576:.0f} GB. Nothing was pulled or removed. "
                "Free space yourself (this script never prunes images or volumes).")
    check(True, "disk guard: the container VM has room before any pull" if free is not None else "disk guard: nothing to pull (both images are local)",
          free_gb=None if free is None else round(free / 1048576, 1), minimum_gb=MIN_FREE_KIB // 1048576, will_pull=to_pull)
    return to_pull


def _uid() -> list[str]:
    return ["--user", f"{os.getuid()}:{os.getgid()}"] if hasattr(os, "getuid") else []


def _promtool(image: str, workdir: Path) -> None:
    mounts = ["-v", f"{OBS / 'prometheus' / 'prometheus.yml'}:/etc/prometheus/prometheus.yml:ro", "-v", f"{OBS / 'prometheus' / 'rules'}:/etc/prometheus/rules:ro",
              "-v", f"{lib.TOKEN_FILE}:/etc/prometheus/abb-metrics-token:ro"]
    base = ["run", "--rm", *_uid(), "--entrypoint", "promtool", *mounts, image]
    config = lib.docker(*base, "check", "config", "/etc/prometheus/prometheus.yml")
    check(config.returncode == 0 and "SUCCESS" in config.stdout + config.stderr, "promtool check config: the committed prometheus.yml (with its rule files and token file) is valid",
          output=(config.stdout + config.stderr).strip().replace("\n", " | ")[:300])
    rule_files = sorted((OBS / "prometheus" / "rules").glob("*.yml"))
    rules = lib.docker(*base, "check", "rules", *[f"/etc/prometheus/rules/{p.name}" for p in rule_files])
    check(rules.returncode == 0 and (rules.stdout + rules.stderr).count("SUCCESS") == len(rule_files), f"promtool check rules: all {len(rule_files)} committed rule files are valid",
          output=(rules.stdout + rules.stderr).strip().replace("\n", " | ")[:400])
    # The negative control: promtool must be able to fail, or the two checks above prove nothing.
    broken = workdir / "broken-rules.yml"
    broken.write_text("groups:\n  - name: broken\n    rules:\n      - alert: Broken\n        expr: sum(rate(http_requests_total[5m]) by (route)\n")
    control = lib.docker("run", "--rm", *_uid(), "--entrypoint", "promtool", "-v", f"{broken}:/x/broken.yml:ro", image, "check", "rules", "/x/broken.yml")
    check(control.returncode != 0, "promtool negative control: a rule file with a syntax error is rejected", exit_code=control.returncode)


def _rule_files() -> dict[str, dict[str, Any]]:
    return {p.name: yaml.safe_load(p.read_text()) for p in sorted((OBS / "prometheus" / "rules").glob("*.yml"))}


def _test_copy(workdir: Path, api_port: int, worker_port: int) -> None:
    """The scrape config and rules the test runs: the committed ones with short intervals, targets on the host, and a short `for` on
    AbbTargetDown. Everything else is compared equal to the committed text below."""
    config = yaml.safe_load((OBS / "prometheus" / "prometheus.yml").read_text())
    config["global"].update({"scrape_interval": SHORT, "scrape_timeout": SHORT, "evaluation_interval": SHORT})
    targets = {"abb-api": f"host.docker.internal:{api_port}", "abb-worker": f"host.docker.internal:{worker_port}"}
    for job in config["scrape_configs"]:
        job["static_configs"] = [{"targets": [targets[job["job_name"]]]}]
    (workdir / "prometheus.yml").write_text(yaml.safe_dump(config))
    rules_dir = workdir / "rules"
    rules_dir.mkdir(exist_ok=True)
    for name, document in _rule_files().items():
        changed = json.loads(json.dumps(document))
        for group in changed["groups"]:
            group["interval"] = SHORT
            for rule in group["rules"]:
                if rule.get("alert") == "AbbTargetDown":
                    rule["for"] = FIRE_AFTER
        (rules_dir / name).write_text(yaml.safe_dump(changed, width=10_000))
    committed = {n: d for n, d in _rule_files().items()}
    reread = {p.name: yaml.safe_load(p.read_text()) for p in sorted(rules_dir.glob("*.yml"))}
    different = []
    for name, document in committed.items():
        for group, copy in zip(document["groups"], reread[name]["groups"], strict=True):
            for rule, rule_copy in zip(group["rules"], copy["rules"], strict=True):
                a, b = dict(rule), dict(rule_copy)
                a.pop("for", None)
                b.pop("for", None)
                if a != b:
                    different.append(rule.get("alert") or rule.get("record"))
    check(not different, "the test copy of the rules differs from the committed rules only in group intervals and the `for` of AbbTargetDown (every expression, label and annotation is identical)",
          differing=different)


def _override(workdir: Path) -> Path:
    path = workdir / "compose.override.yml"
    path.write_text(f"""# Written by scripts/obs-e2e.sh for ONE run; nothing here is committed.
services:
  prometheus:
    ports: !override
      - "127.0.0.1:{PROM_PORT}:9090"
    volumes: !override
      - {workdir / 'prometheus.yml'}:/etc/prometheus/prometheus.yml:ro
      - {workdir / 'rules'}:/etc/prometheus/rules:ro
      - {lib.TOKEN_FILE}:/etc/prometheus/abb-metrics-token:ro
    extra_hosts:
      - "host.docker.internal:host-gateway"
    restart: "no"
  grafana:
    ports: !override
      - "127.0.0.1:{GRAFANA_PORT}:3000"
    restart: "no"
""")
    return path


def _compose(workdir: Path, *args: str, env: dict[str, str] | None = None) -> Any:
    import subprocess  # noqa: PLC0415

    merged = {**os.environ, "ABB_OBS_UID": str(os.getuid()), "ABB_OBS_GID": str(os.getgid()), **(env or {})}
    return subprocess.run(  # noqa: S603
        ["docker", "compose", "-p", PROJECT, "-f", str(lib.ROOT / "docker-compose.yml"), "-f", str(workdir / "compose.override.yml"), "--profile", "observability", *args],  # noqa: S607
        capture_output=True, text=True, check=False, timeout=900, env=merged, cwd=lib.ROOT)


def _q(path: str, **params: Any) -> Any:
    return httpx.get(f"http://127.0.0.1:{PROM_PORT}{path}", params=params, timeout=30)


def _targets() -> dict[str, str]:
    data = _q("/api/v1/targets").json()["data"]["activeTargets"]
    return {t["labels"]["job"]: t["health"] for t in data}


def _rules_state() -> list[dict[str, Any]]:
    return list(_q("/api/v1/rules").json()["data"]["groups"])


def _walk_exprs(panel: dict[str, Any]) -> list[str]:
    out = [t["expr"] for t in panel.get("targets", []) if t.get("expr")]
    for child in panel.get("panels", []):
        out += _walk_exprs(child)
    return out


def _dashboard_file() -> dict[str, Any]:
    return dict(json.loads((OBS / "grafana" / "dashboards" / "agent-black-box.json").read_text()))


def _titles(panels: list[dict[str, Any]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for panel in panels:
        out.append((panel.get("type", ""), panel.get("title", "")))
        out += _titles(panel.get("panels", []))
    return out


def gate5() -> None:
    gate("gate5-prometheus")
    images = _images()
    info(images=images)
    workdir = lib.WORK / "prom"
    workdir.mkdir(exist_ok=True)
    pulled_before = {name: _present(image) for name, image in images.items()}
    _disk_guard(images)
    _promtool(images["prometheus"], workdir)

    api_port, worker_port = lib.API_PORT + 10, lib.API_PORT + 11
    # The containers reach the host, so these two processes listen on every interface for the minutes of this gate (token-protected
    # /metrics, a throwaway database, development environment; nothing is published beyond the host's own network).
    api = lib.start_server("scraped", api_port, {"ABB_WORKER_METRICS_PORT": None}, host="0.0.0.0")  # noqa: S104
    worker = lib.start_worker("scraped", metrics_port=worker_port, env={"ABB_WORKER_METRICS_BIND": "0.0.0.0"})
    lib.poll("the scraped worker serves metrics", lambda: lib.scrape_raw(f"http://127.0.0.1:{worker_port}").status_code == 200, timeout=60)
    _test_copy(workdir, api_port, worker_port)
    _override(workdir)
    (lib.WORK / "compose.project").write_text(PROJECT)
    grafana_password = secrets.token_hex(12)
    lib.remember_secret("grafana-admin", grafana_password)

    project = lib.new_project("obs-prom")
    key = lib.new_key(project, ["events:write"], "obs-prom")
    lib.remember_secret("api-key-prom", key)
    stop = threading.Event()
    sent = {"batches": 0, "failed": 0}

    def traffic() -> None:
        while not stop.is_set():
            run = lib.run_ids()
            try:
                reply = lib.ingest(f"http://127.0.0.1:{api_port}", key, [lib.event(run, n + 1) for n in range(3)])
                sent["batches"] += 1
                sent["failed"] += 0 if reply.status_code == 202 else 1
            except Exception:  # noqa: BLE001 - the load generator must not end the gate
                sent["failed"] += 1
            stop.wait(0.4)

    try:
        started = time.monotonic()
        up = _compose(workdir, "up", "-d", "prometheus", "grafana", env={"ABB_GRAFANA_ADMIN_PASSWORD": grafana_password})
        if up.returncode != 0:
            die(f"docker compose up failed: {(up.stderr or up.stdout)[-400:]}")
        for name, image in images.items():
            check(True, f"image {name}: {image} " + ("was already local" if pulled_before[name] else "was pulled by this run"), pulled=not pulled_before[name])
        threading.Thread(target=traffic, daemon=True).start()
        lib.timing(compose_up_seconds=round(time.monotonic() - started, 1))

        # ---------------------------------------------------------------- Prometheus
        lib.poll("Prometheus is ready", lambda: _q("/-/ready").status_code == 200, timeout=120)
        lib.poll("both targets (abb-api, abb-worker) are UP", lambda: _targets() == {"abb-api": "up", "abb-worker": "up"}, timeout=180)
        check(_targets() == {"abb-api": "up", "abb-worker": "up"}, "Prometheus scrapes both targets with the bearer token read from a file: abb-api UP, abb-worker UP")
        committed = _rule_files()
        expected_groups = [g["name"] for d in committed.values() for g in d["groups"]]
        expected_rules = sum(len(g["rules"]) for d in committed.values() for g in d["groups"])

        def all_evaluated() -> bool:
            groups = _rules_state()
            if sorted(g["name"] for g in groups) != sorted(expected_groups):
                return False
            rules = [r for g in groups for r in g["rules"]]
            return len(rules) == expected_rules and all(not r["lastEvaluation"].startswith("0001-") for r in rules)

        lib.poll("every rule group is loaded and every rule has been evaluated at least once", all_evaluated, timeout=180)
        groups = _rules_state()
        rules = [r for g in groups for r in g["rules"]]
        unhealthy = [(r["name"], r.get("lastError", "")[:80]) for r in rules if r["health"] != "ok" or r.get("lastError")]
        check(not unhealthy, f"Prometheus loaded {len(groups)} rule groups and {len(rules)} rules; all have health ok and no evaluation error", unhealthy=unhealthy[:5])
        check(sorted(g["name"] for g in groups) == sorted(expected_groups), "the loaded group names equal the committed files'", groups=len(groups))

        # recording rules produce series once there is traffic (rate() needs two scrapes that differ)
        records = [r["record"] for d in committed.values() for g in d["groups"] for r in g["rules"] if "record" in r]

        def produced() -> list[str]:
            return [name for name in records if not _q("/api/v1/query", query=name).json()["data"]["result"]]

        missing = lib.poll("every recording rule has produced a series", lambda: produced() == [] or None, timeout=240, every=3.0)
        check(bool(missing) and produced() == [], f"all {len(records)} recording rules produced a series after traffic ({sent['batches']} batches, {sent['failed']} not 202)",
              silent=produced())
        check(sent["failed"] == 0, "the traffic this gate drove was all accepted (no 5xx, no refusal)", batches=sent["batches"])
        ratio = _q("/api/v1/query", query="abb:ingest_error_ratio:rate5m").json()["data"]["result"]
        check(ratio and float(ratio[0]["value"][1]) == 0.0, "the SLI error ratio recorded by Prometheus is exactly 0 for traffic with no errors", value=ratio[0]["value"][1] if ratio else None)

        # every expression of the rules and of the dashboard executes
        rule_exprs = [(r.get("alert") or r.get("record"), r["expr"]) for d in committed.values() for g in d["groups"] for r in g["rules"]]
        dashboard = _dashboard_file()
        panel_exprs = [(f"panel:{p.get('title')}", e) for p in dashboard["panels"] for e in _walk_exprs(p)]
        failures = []
        for label, expr in [*rule_exprs, *panel_exprs]:
            reply = _q("/api/v1/query", query=expr)
            body = reply.json()
            if reply.status_code != 200 or body.get("status") != "success":
                failures.append((label, body.get("error", reply.status_code)))
        check(not failures, f"Prometheus executes all {len(rule_exprs)} rule expressions and all {len(panel_exprs)} dashboard expressions (status success; an empty result is fine)",
              failures=failures[:3])
        check(len(panel_exprs) > 50, "the dashboard has the expected volume of expressions (> 50)", expressions=len(panel_exprs))

        # ---------------------------------------------------------------- Grafana
        base_url = f"http://127.0.0.1:{GRAFANA_PORT}"
        auth = ("admin", grafana_password)
        lib.poll("Grafana reports a healthy database", lambda: httpx.get(f"{base_url}/api/health", timeout=10).json().get("database") == "ok", timeout=180)
        health = httpx.get(f"{base_url}/api/health", timeout=10).json()
        check(health.get("database") == "ok", "Grafana /api/health: database ok", version=health.get("version"))
        sources = httpx.get(f"{base_url}/api/datasources", auth=auth, timeout=15).json()
        provisioned = [s for s in sources if s.get("uid") == "abb-prometheus"]
        check(len(provisioned) == 1 and provisioned[0]["type"] == "prometheus" and provisioned[0]["isDefault"] and provisioned[0]["readOnly"],
              "Grafana: the Prometheus datasource is provisioned (uid abb-prometheus, default, read-only)", sources=[s.get("name") for s in sources])
        ds_health = httpx.get(f"{base_url}/api/datasources/uid/abb-prometheus/health", auth=auth, timeout=30)
        check(ds_health.status_code == 200 and ds_health.json().get("status") == "OK", "Grafana: the datasource health check reaches Prometheus", status=ds_health.status_code)
        got = lib.poll("the dashboard is provisioned", lambda: httpx.get(f"{base_url}/api/dashboards/uid/agent-black-box", auth=auth, timeout=15).status_code == 200 or None, timeout=120)
        reply = httpx.get(f"{base_url}/api/dashboards/uid/agent-black-box", auth=auth, timeout=15)
        served = reply.json()
        check(bool(got) and served["meta"]["provisioned"] is True and served["dashboard"]["title"] == dashboard["title"],
              "Grafana: the dashboard `agent-black-box` is provisioned and fetched by uid", title=served["dashboard"]["title"])
        served_titles, file_titles = _titles(served["dashboard"]["panels"]), _titles(dashboard["panels"])
        rows = [t for kind, t in file_titles if kind == "row"]
        check(served_titles == file_titles and len(rows) == 7, f"Grafana serves the same {len(file_titles)} panels and the same {len(rows)} rows as the file", rows=rows)
        via_proxy = []
        for label, expr in panel_exprs:
            r = httpx.get(f"{base_url}/api/datasources/proxy/uid/abb-prometheus/api/v1/query", params={"query": expr}, auth=auth, timeout=30)
            if r.status_code != 200 or r.json().get("status") != "success":
                via_proxy.append(label)
        check(not via_proxy, f"all {len(panel_exprs)} dashboard expressions also run through Grafana's datasource proxy", failures=via_proxy[:3])
        check(httpx.get(f"{base_url}/api/dashboards/uid/agent-black-box").status_code in {401, 403}, "Grafana: anonymous access is off (the dashboard needs a login)")

        # ---------------------------------------------------------------- the alert path
        def alerts() -> list[dict[str, Any]]:
            return [a for a in _q("/api/v1/alerts").json()["data"]["alerts"] if a["labels"].get("alertname") == "AbbTargetDown"]

        check(alerts() == [], "AbbTargetDown is not active while both targets are up")
        worker.stop()  # the worker target disappears
        lib.poll("the worker target goes DOWN", lambda: _targets().get("abb-worker") == "down", timeout=120)
        firing = lib.poll("AbbTargetDown FIRES for abb-worker", lambda: [a for a in alerts() if a["state"] == "firing"] or None, timeout=240, every=2.0)
        alert = firing[0]
        check(alert["labels"]["job"] == "abb-worker" and alert["labels"]["severity"] == "page" and alert["state"] == "firing",
              "AbbTargetDown FIRES (state firing, job abb-worker, severity page) once the worker target is stopped", labels={k: v for k, v in alert["labels"].items() if k != "instance"})
        check(all(a["labels"]["job"] != "abb-api" for a in alerts()) and _targets().get("abb-api") == "up", "only the stopped target alerts; abb-api is still UP and silent")
        check(alert["annotations"].get("runbook_url", "").startswith("docs/runbooks/"), "the firing alert carries its runbook_url", runbook=alert["annotations"].get("runbook_url"))
        stop.set()
    except BaseException:
        stop.set()
        # What the containers said (Prometheus and Grafana log no secrets: the token is read from a file, the password is not echoed).
        for service in ("prometheus", "grafana"):
            logs = _compose(workdir, "logs", "--no-color", "--tail", "25", service)
            print(f"  --- {service} container log (last lines) ---\n" + "\n".join((logs.stdout or logs.stderr).splitlines()[-25:]), flush=True)
        print(_compose(workdir, "ps", "-a").stdout, flush=True)
        raise
    finally:
        stop.set()
        down = _compose(workdir, "down", "--volumes", "--remove-orphans")
        lingering = lib.docker("ps", "-a", "--filter", f"label=com.docker.compose.project={PROJECT}", "--format", "{{.Names}}")
        check(down.returncode == 0 and not lingering.stdout.strip(), "teardown: the compose project's containers, network and volume are removed (images stay cached)", lingering=lingering.stdout.split())
        (lib.WORK / "compose.project").unlink(missing_ok=True)
        api.stop()
        worker.stop()
    lib.finish()


_ = re

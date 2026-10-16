"""Gate 6 of scripts/obs-e2e.sh (D9 gate 5, against a real exporter): optional tracing with the `otel` extra, in a SEPARATE virtualenv (the
default environment stays free of OpenTelemetry), exporting OTLP/HTTP to a local stdlib receiver (scripts/obs_e2e_otlp.py).

  * spans arrive for an ingestion request (`http.request` with its child `ingest.persist`) and for the processing jobs (`job.run`)
  * each job span is a ROOT whose link carries the trace id of the request that enqueued it (all three requests are linked)
  * an inbound `traceparent` header is ignored (no span has the fabricated trace id)
  * every attribute key is in the allow-list of abb_api.obs.tracing, every span name is in its closed set, no span event, no status text
  * the sentinels planted in paths, queries, headers, payloads, names and attributes appear in NO exported byte (raw bodies scanned)
  * a collector that is down never breaks ingestion: 202s continue, `otel_export_failures_total` increments, the process stays ready
"""

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

import obs_e2e_lib as lib
from obs_e2e_lib import check, die, gate, info

SPAN_NAMES_OK = {"http.request", "ingest.persist", "job.run", "db.checkout", "alerts.attempt", "retention.tick"}
# service.instance.id is a random per-process UUID the SDK adds to the resource (not derived from anything of ours or the client's).
RESOURCE_KEYS_OK = {"service.name", "service.instance.id", "telemetry.sdk.language", "telemetry.sdk.name", "telemetry.sdk.version"}
FABRICATED_TRACE = "0af7651916cd43dd8448eb211c80319c"


def _allowed_attribute_names() -> set[str]:
    from abb_api.obs.tracing import ATTRIBUTES  # noqa: PLC0415

    return {exported for exported, _ in ATTRIBUTES.values()}


def _span_names() -> set[str]:
    from abb_api.obs.tracing import SPAN_NAMES  # noqa: PLC0415

    return set(SPAN_NAMES)


def _start_receiver(out: Path, port: int) -> subprocess.Popen[bytes]:
    log = lib.WORK / "otlp-receiver.out"
    popen = subprocess.Popen([lib.OTEL_PYTHON, str(Path(__file__).parent / "obs_e2e_otlp.py"), "--port", str(port), "--out", str(out)],  # noqa: S603
                             stdout=log.open("wb"), stderr=subprocess.STDOUT, start_new_session=True)
    lib.poll("the OTLP receiver listens", lambda: popen.poll() is None and "listening" in log.read_text(), timeout=30, every=0.2)
    return popen


def _spans(out: Path) -> list[dict[str, Any]]:
    path = out / "spans.jsonl"
    if not path.exists():
        return []
    spans: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        record = json.loads(line)
        if "error" in record:
            die(f"the receiver could not decode request {record['n']}: {record['error']}")
        spans += record["spans"]
    return spans


def _stop_main_worker() -> None:
    """The main worker would take the jobs of this gate: stop it (only this one; its pid is in the work directory)."""
    pid = int((lib.WORK / "worker.pid").read_text())
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.2)
    die("the main worker did not stop")


def gate6() -> None:
    gate("gate6-tracing")
    if not lib.OTEL_PYTHON or not Path(lib.OTEL_PYTHON).exists():
        die("the otel environment was not created (stage 6 needs it)")
    out = lib.WORK / "otlp"
    out.mkdir(exist_ok=True)
    port = lib.free_port()
    receiver = _start_receiver(out, port)
    _stop_main_worker()
    exporter_env = {"ABB_OTEL_ENABLED": "1", "ABB_OTEL_SAMPLE_RATIO": "1.0", "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{port}", "OTEL_EXPORTER_OTLP_TIMEOUT": "5"}
    api = lib.start_server("traced", lib.API_PORT + 12, exporter_env, python=lib.OTEL_PYTHON)
    worker = lib.start_worker("traced", python=lib.OTEL_PYTHON, env=exporter_env, metrics_port=lib.API_PORT + 13)
    try:
        marks = {kind: lib.sentinel(f"trace-{kind}") for kind in ("project", "agent", "path", "query", "header", "attr", "ua", "cookie")}
        project = lib.new_project(marks["project"].lower())
        key = lib.new_key(project, ["events:write"], "obs-trace")
        lib.remember_secret("api-key-trace", key)
        runs = [lib.run_ids() for _ in range(3)]
        for index, run in enumerate(runs):
            events = [lib.event(run, n + 1, agent=marks["agent"].lower()) for n in range(4)]
            events[1]["attributes"] = {"tool.name": "shell", "note": marks["attr"]}
            reply = lib.ingest(api.url, key, events, extra_headers={"User-Agent": marks["ua"], "X-Request-ID": f"{marks['header']} not:a:valid id", "X-Trace-Note": marks["header"],
                                                                   "traceparent": f"00-{FABRICATED_TRACE}-00f067aa0ba902b7-01", "Cookie": f"abb_other={marks['cookie']}"})
            check(reply.status_code == 202, f"traced API: ingest batch {index + 1} is accepted", status=reply.status_code)
        scanner = httpx.get(f"{api.url}/v1/{marks['path']}/x", params={"q": marks["query"]}, headers={"X-Probe": marks["header"]}, timeout=15)
        check(scanner.status_code == 404, "traced API: an unmatched request with sentinels in the path, query and header is 404", status=scanner.status_code)

        request_route = "/v1/events/batch"

        def ingest_spans() -> list[dict[str, Any]]:
            return [s for s in _spans(out) if s["name"] == "http.request" and s["attributes"].get("http.route") == request_route]

        def job_spans() -> list[dict[str, Any]]:
            return [s for s in _spans(out) if s["name"] == "job.run" and s["resource"].get("service.name") == "abb-worker"]

        lib.poll("spans for the 3 ingestion requests reach the collector", lambda: len(ingest_spans()) >= 3, timeout=120, every=1.0)
        requests = ingest_spans()
        check(len(requests) == 3 and all(s["attributes"].get("http.response.status_code") == 202 and s["attributes"].get("http.request.method") == "POST" for s in requests),
              "spans: exactly 3 `http.request` spans for the ingest route, each POST with status 202", spans=len(requests))
        request_traces = {s["trace_id"] for s in requests}

        def linked() -> set[str]:
            return {link["trace_id"] for job in job_spans() for link in job["links"]} & request_traces

        lib.poll("the worker's job spans arrive and link back to every request", lambda: linked() == request_traces, timeout=180, every=2.0)
        all_spans = _spans(out)
        jobs = job_spans()
        by_trace = {s["trace_id"]: s for s in requests}
        persists = [s for s in all_spans if s["name"] == "ingest.persist"]
        children = [p for p in persists if p["trace_id"] in by_trace and p["parent_span_id"] == by_trace[p["trace_id"]]["span_id"]]
        check(len(children) == 3 and all(p["attributes"].get("abb.events") == 4 and p["attributes"].get("abb.accepted") == 4 for p in children),
              "spans: each request has an `ingest.persist` child (same trace, parent = the request span) with abb.events = 4 and abb.accepted = 4", children=len(children))
        check(linked() == request_traces, "spans: the job spans carry a link whose trace id equals the trace id of each of the 3 requests", linked=len(linked()))
        check(all(j["parent_span_id"] == "" and j["trace_id"] not in request_traces for j in jobs), "spans: a job span is a root of its own trace (linked, not parented, to the request)", jobs=len(jobs))
        check(all(j["attributes"].get("abb.job.type") and j["attributes"].get("abb.outcome") in {"succeeded", "retried", "dead_letter"} for j in jobs), "spans: job spans carry the job type and an outcome from the closed set")
        check(all(s["trace_id"] != FABRICATED_TRACE and all(link["trace_id"] != FABRICATED_TRACE for link in s["links"]) for s in all_spans),
              "an inbound traceparent header is ignored: no span or link uses the fabricated trace id", spans=len(all_spans))

        allowed, names = _allowed_attribute_names(), _span_names()
        seen_keys = {k for s in all_spans for k in s["attributes"]}
        check(seen_keys <= allowed, "every exported attribute key is in the allow-list of abb_api.obs.tracing", keys=sorted(seen_keys), outside=sorted(seen_keys - allowed))
        check({s["name"] for s in all_spans} <= names and names >= SPAN_NAMES_OK, "every exported span name is in the closed set", names=sorted({s["name"] for s in all_spans}))
        resource_keys = {k for s in all_spans for k in s["resource"]}
        check(resource_keys <= RESOURCE_KEYS_OK and {s["resource"]["service.name"] for s in all_spans} == {"abb-api", "abb-worker"}, "resource attributes are the service name, a random instance id and the SDK's own three",
              keys=sorted(resource_keys), services=sorted({s["resource"]["service.name"] for s in all_spans}))
        check(all(s["events"] == [] and s["status_message"] == "" and s["scope"] == "abb_api" for s in all_spans), "no span carries events, a status message or another instrumentation scope")
        check(all(len(str(v)) <= 64 for s in all_spans for v in s["attributes"].values()), "every string attribute is at most 64 characters")
        route_values = {s["attributes"].get("http.route") for s in all_spans if s["name"] == "http.request"}
        check(all(r is None or (isinstance(r, str) and r.startswith("/") and "SENTINEL" not in r.upper()) for r in route_values) and not any(m.lower() in str(route_values).lower() for m in marks.values()),
              "http.route is a route template (or absent for an unmatched path), never the raw path of the scanner request", routes=sorted(str(r) for r in route_values))

        # nothing a client controls is in a single exported byte
        needles = {**marks, "key": key, "key-id": key.split(".", 1)[0], "metrics-token": lib.metrics_token(), "workspace": lib.base.SLUG, "project-id": project,
                   **{f"run-{i}": run["run_id"] for i, run in enumerate(runs)}, **{f"event-trace-{i}": run["trace_id"] for i, run in enumerate(runs)},
                   "owner-email": lib.base.email("owner")}
        bodies = sorted(out.glob("body-*.bin"))
        leaked = [label for label, needle in needles.items() for body in bodies if needle.encode() in body.read_bytes() or needle.lower().encode() in body.read_bytes().lower()]
        check(bodies and not leaked, f"no sentinel, key, token, id or e-mail in any of the {len(bodies)} raw OTLP bodies exported ({len(needles)} needles)", leaked=sorted(set(leaked)))
        info(spans=len(all_spans), job_spans=len(jobs), exported_bodies=len(bodies))

        # a collector that is down never breaks ingestion
        before = lib.scrape(api.url)
        receiver.terminate()
        receiver.wait(timeout=20)
        statuses = []
        for _ in range(6):
            reply = lib.ingest(api.url, key, [lib.event(lib.run_ids(), n + 1) for n in range(3)])
            statuses.append(reply.status_code)
        check(statuses == [202] * 6, "collector down: 6 further ingest batches are all 202", statuses=statuses)
        lib.poll("the failed exports are counted (otel_export_failures_total increments)", lambda: lib.total(lib.scrape(api.url), "otel_export_failures_total") > lib.total(before, "otel_export_failures_total"), timeout=180, every=2.0)
        families = lib.scrape(api.url)
        check(lib.total(families, "otel_export_failures_total") >= 1 and lib.total(families, "otel_export_failures_total") > lib.total(before, "otel_export_failures_total"),
              "collector down: otel_export_failures_total increments", before=lib.total(before, "otel_export_failures_total"), after=lib.total(families, "otel_export_failures_total"))
        after = lib.ingest(api.url, key, [lib.event(lib.run_ids(), 1)])
        ready = httpx.get(f"{api.url}/readyz", timeout=15)
        check(after.status_code == 202 and ready.status_code == 200 and api.alive() and worker.alive(),
              "collector down: ingestion is still 202, /readyz is 200 and both traced processes are alive", ingest=after.status_code, ready=ready.status_code)
        committed = lib.total(families, "ingest_acks_total", outcome="committed") - lib.total(before, "ingest_acks_total", outcome="committed")
        check(committed == 6 and lib.total(families, "ingest_acks_total", outcome="error") == 0, "collector down: all 6 batches were committed and none counted as an error", committed=committed)
    finally:
        if receiver.poll() is None:
            receiver.terminate()
        api.stop()
        worker.stop()
    lib.finish()

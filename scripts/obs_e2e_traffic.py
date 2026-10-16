"""Gate 2 of scripts/obs-e2e.sh (D9 gate 1, second half): every catalogue family moves by EXACTLY the traffic driven.

Each scenario scrapes before and after and compares the difference with a count the script itself knows (requests it sent, answers it
saw, rows it counted in the database). Nothing is compared with a time. Refusal scenarios run against a second API process with tight
limits (its own registry, so no other traffic can move its counters); the happy path, the worker, sessions, alerts, retention and
backups run against the main stack.
"""

import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any

import httpx

import obs_e2e_lib as lib
from obs_e2e_lib import check, gate, info

TIGHT = {
    "ABB_INGEST_MAX_BATCH_EVENTS": "10", "ABB_INGEST_MAX_BODY_BYTES": "20480", "ABB_ARTIFACT_MAX_BYTES": "20480",
    "ABB_RATE_LIMIT_EVENTS_PER_SECOND": "0.5", "ABB_RATE_LIMIT_BURST_EVENTS": "10",
    "ABB_RATE_LIMIT_BYTES_PER_SECOND": "2048", "ABB_RATE_LIMIT_BURST_BYTES": "20480",
    "ABB_AUTH_FAIL_PER_CREDENTIAL": "5", "ABB_AUTH_FAIL_WINDOW_SECONDS": "60",
}


def _reason(code: str) -> str:
    from abb_api.ingestion.observe import reason_of  # noqa: PLC0415

    return reason_of(code)


def _count(sql: str, *args: Any) -> int:
    return int(lib.base.db_one(sql, *args) or 0)


# ------------------------------------------------------------------------------------------------ ingestion (main stack)


def _derivation_idle() -> bool:
    """No derivation job waits. The alert timers (sweep, deliver, prune) re-arm themselves for ever once a rule exists, so they are not 'work'."""
    return _count("select count(*) from outbox_jobs where status in ('pending', 'running') and job_type not in ('alert_sweep', 'deliver_alerts', 'alert_prune')") == 0


def _ingest_happy_path() -> tuple[str, str]:
    """Valid batches: every number the API counts equals what was sent. Returns (project id, key)."""
    api = lib.API_URL
    project = lib.new_project("obs-happy")
    key = lib.new_key(project, ["events:write"], "obs-happy")
    lib.remember_secret("api-key-happy", key)
    batches, per_batch = 7, 6
    done_before = _count("select count(*) from outbox_jobs where status = 'done'")
    delta = lib.Delta(api)
    worker = lib.Delta(lib.WORKER_URL)
    sent_bytes = 0
    runs = []
    for i in range(batches):
        run = lib.run_ids()
        runs.append(run["run_id"])
        events = [lib.event(run, n + 1) for n in range(per_batch)]
        body = json.dumps(lib.batch_of(events)).encode()
        reply = httpx.post(f"{api}/v1/events/batch", content=body, headers={**lib.bearer(key), "Content-Type": "application/json"}, timeout=30)
        if reply.status_code != 202:
            lib.die(f"ingest -> {reply.status_code} {reply.text[:120]}")
        sent_bytes += len(body)
    delta.refresh()
    check(delta.d("telemetry_events_received_total") == batches * per_batch, "ingest: events_received moved by exactly the events accepted",
          expected=batches * per_batch, observed=delta.d("telemetry_events_received_total"))
    check(delta.d("telemetry_bytes_received_total") == sent_bytes, "ingest: bytes_received moved by exactly the request body bytes sent",
          expected=sent_bytes, observed=delta.d("telemetry_bytes_received_total"))
    check(delta.d("ingest_acks_total", outcome="committed") == batches and delta.d("ingest_acks_total", outcome="rejected") == 0
          and delta.d("ingest_acks_total", outcome="error") == 0, "ingest: ingest_acks{committed} +1 per batch, none rejected, none errored", committed=batches)
    check(delta.d("ingest_ack_duration_seconds", "ingest_ack_duration_seconds_count") == batches, "ingest: the ack duration histogram counted one observation per request")
    check(delta.d("http_requests_total", route="/v1/events/batch", method="POST", status="202") == batches,
          "ingest: http_requests_total{route=/v1/events/batch, POST, 202} +1 per batch")
    check(delta.d("telemetry_events_rejected_total") == 0, "ingest: nothing was counted as rejected")
    # The worker derives the runs. The oracle is the queue table: one outbox row completes per successful execution.
    lib.poll("the worker finished every job of this traffic", _derivation_idle, timeout=90)
    done = _count("select count(*) from outbox_jobs where status = 'done'") - done_before
    worker.refresh()
    check(done >= batches and worker.d("worker_jobs_total", outcome="succeeded") == done,
          "worker: worker_jobs{outcome=succeeded} moved by exactly the jobs completed in the queue table", completed_rows=done,
          observed=worker.d("worker_jobs_total", outcome="succeeded"))
    check(worker.d("worker_job_duration_seconds", "worker_job_duration_seconds_count") == done and worker.d("processor_failures_total") == 0,
          "worker: one duration observation per job and no processor failure", failures=worker.d("processor_failures_total"))
    after = worker.now
    check(lib.value(after, "worker_jobs_pending") == 0 or not after["worker_jobs_pending"].samples or lib.total(after, "worker_jobs_pending") == 0,
          "worker: the scrape-time depth gauge shows no waiting job once the queue is empty")
    return project, key


# ------------------------------------------------------------------------------------------------ refusals (tight process)


def _refusals() -> None:
    controls = lib.start_server("controls", lib.API_PORT + 2, TIGHT)
    try:
        base_url = controls.url
        owner = lib.person("owner")
        project = lib.new_project("obs-refusals")
        key = lib.new_key(project, ["events:write"], "obs-refusals")
        lib.remember_secret("api-key-refusals", key)
        headers = {**lib.bearer(key), "Content-Type": "application/json"}
        delta = lib.Delta(base_url)
        reached_handler_4xx = 0

        # --- invalid: a body that is not a batch
        bad = httpx.post(f"{base_url}/v1/events/batch", content=b'{"events": "not a list"', headers=headers, timeout=30)
        reached_handler_4xx += 1
        check(400 <= bad.status_code < 500, "refusal: a malformed body is refused with a 4xx", status=bad.status_code, code=bad.json()["error"]["code"])
        invalid_request = _reason(bad.json()["error"]["code"])

        # --- invalid events inside an accepted batch: counted per event, by the reason of each event's own error code
        run = lib.run_ids()
        good = [lib.event(run, n + 1) for n in range(3)]
        broken = [{**lib.event(run, 10), "event_type": "not.a.real.type"}, {k: v for k, v in lib.event(run, 11).items() if k != "event_id"}]
        mixed = httpx.post(f"{base_url}/v1/events/batch", json=lib.batch_of(good + broken), headers=lib.bearer(key), timeout=30)
        body = mixed.json()
        check(mixed.status_code == 202 and body["rejected"] >= 1 and body["accepted"] + body["rejected"] == 5 and all(e["code"] for e in body["errors"]),
              "refusal: a batch with an invalid event is a 202 that accepts the rest and reports each refusal with its code", status=mixed.status_code,
              accepted=body.get("accepted"), rejected=body.get("rejected"), codes=sorted({e["code"] for e in body["errors"]}))
        mixed_accepted = int(body["accepted"])
        per_event: dict[str, int] = {}
        for error in body["errors"]:
            per_event[_reason(error["code"])] = per_event.get(_reason(error["code"]), 0) + 1

        # --- too large
        big = lib.batch_of([lib.event(lib.run_ids(), 1, pad=30000)])
        huge = httpx.post(f"{base_url}/v1/events/batch", content=json.dumps(big).encode(), headers=headers, timeout=30)
        reached_handler_4xx += 1 if huge.json().get("error", {}).get("code") in {"PAYLOAD_TOO_LARGE", "EVENT_TOO_LARGE"} else 0
        check(huge.status_code == 413, "refusal: a body over the cap is 413", status=huge.status_code, code=huge.json()["error"]["code"])

        # --- quota: the 4th..6th event of a project limited to 3 events a day
        quota_project = lib.new_project("obs-quota")
        quota_key = lib.new_key(quota_project, ["events:write"], "obs-quota")
        lib.remember_secret("api-key-quota", quota_key)
        set_quota = owner.put(f"/v1/projects/{quota_project}/quotas", json={"events_per_day": 3})
        check(set_quota.status_code == 200, "quota: the owner sets events_per_day = 3 through the real API", status=set_quota.status_code)
        qrun = lib.run_ids()
        over = httpx.post(f"{base_url}/v1/events/batch", json=lib.batch_of([lib.event(qrun, n + 1) for n in range(6)]), headers=lib.bearer(quota_key), timeout=30)
        qbody = over.json()
        check(over.status_code == 202 and qbody["accepted"] == 3 and qbody["rejected"] == 3 and all(e["code"] == "QUOTA_EXCEEDED" for e in qbody["errors"]),
              "quota: 6 events into a 3-event quota: 3 accepted, 3 refused QUOTA_EXCEEDED", accepted=qbody["accepted"], rejected=qbody["rejected"])

        # --- rate limited: a 10-event burst, then the same again at once (the bucket refills at 0.5 events a second)
        rate_project = lib.new_project("obs-rate")
        rate_key = lib.new_key(rate_project, ["events:write"], "obs-rate")
        lib.remember_secret("api-key-rate", rate_key)
        first = httpx.post(f"{base_url}/v1/events/batch", json=lib.batch_of([lib.event(lib.run_ids(), 1) for _ in range(10)]), headers=lib.bearer(rate_key), timeout=30)
        second = httpx.post(f"{base_url}/v1/events/batch", json=lib.batch_of([lib.event(lib.run_ids(), 1) for _ in range(10)]), headers=lib.bearer(rate_key), timeout=30)
        reached_handler_4xx += 1 if second.status_code == 429 else 0
        check(first.status_code == 202 and second.status_code == 429 and second.headers.get("retry-after", "").isdigit(),
              "rate limit: a 10-event burst is accepted, the next at once is 429 with Retry-After", statuses=[first.status_code, second.status_code],
              retry_after=second.headers.get("retry-after"))

        # --- failed key authentication, throttled: a real key id with the wrong secret
        wrong = lib.base.bad_token_for(key)
        answers = [httpx.get(f"{base_url}/v1/runs", headers=lib.bearer(wrong), timeout=30) for _ in range(12)]
        unauthorised = sum(1 for a in answers if a.status_code == 401)
        throttled = sum(1 for a in answers if a.status_code == 429)
        check(unauthorised + throttled == 12 and unauthorised >= 1 and throttled >= 1, "throttle: 12 wrong-secret requests are 401 until the budget (5) is spent, then 429",
              unauthorised=unauthorised, throttled=throttled)

        delta.refresh()
        d = delta.d
        # --- the deltas, against what the script knows it did
        expected_events_accepted = mixed_accepted + 3 + 10  # the mixed batch, the quota batch (3 of 6), the rate-limit burst
        check(d("telemetry_events_received_total") == expected_events_accepted, f"refusals: events_received counts only accepted events ({mixed_accepted} + 3 + 10)",
              expected=expected_events_accepted, observed=d("telemetry_events_received_total"))
        invalid_expected = per_event.get("invalid", 0) + (1 if invalid_request == "invalid" else 0)
        check(per_event.get("invalid", 0) >= 1 and d("telemetry_events_rejected_total", reason="invalid") == invalid_expected,
              "refusals: rejected{invalid} = the invalid events reported inside the 202 plus the malformed request (counted once, under its own reason)",
              request_reason=invalid_request, per_event=per_event, observed=d("telemetry_events_rejected_total", reason="invalid"))
        check(d("telemetry_events_rejected_total", reason="quota_exceeded") == 3 and d("quota_refusals_total", quota="events_per_day") == 3,
              "refusals: rejected{quota_exceeded} and quota_refusals{events_per_day} moved by exactly the 3 refused events",
              rejected=d("telemetry_events_rejected_total", reason="quota_exceeded"), refusals=d("quota_refusals_total", quota="events_per_day"))
        check(d("telemetry_events_rejected_total", reason="rate_limited") == 1, "refusals: rejected{rate_limited} +1 for the one 429 batch", observed=d("telemetry_events_rejected_total", reason="rate_limited"))
        check(d("telemetry_events_rejected_total", reason="too_large") + d("body_cap_refusals_total") >= 1, "refusals: the oversize body is counted (rejected{too_large} or the body cap)",
              too_large=d("telemetry_events_rejected_total", reason="too_large"), body_cap=d("body_cap_refusals_total"))
        # Acks: committed = the 202s (mixed, quota, burst); rejected = the 4xx answers that reached the handler; error = none.
        committed = 3
        check(d("ingest_acks_total", outcome="committed") == committed and d("ingest_acks_total", outcome="error") == 0
              and d("ingest_acks_total", outcome="rejected") == reached_handler_4xx,
              "acks: committed = the 202 batches, rejected = the 4xx answers of the handler, error = 0", committed=d("ingest_acks_total", outcome="committed"),
              rejected=d("ingest_acks_total", outcome="rejected"), expected_rejected=reached_handler_4xx)
        ratio_num, ratio_den = d("ingest_acks_total", outcome="committed"), d("ingest_acks_total", outcome="committed") + d("ingest_acks_total", outcome="error")
        naive = ratio_num / (ratio_den + d("ingest_acks_total", outcome="rejected"))
        check(ratio_den > 0 and ratio_num / ratio_den == 1.0 and naive < 1.0,
              "SLI: committed/(committed+error) is exactly 1.0 for this traffic; the 4xx refusals are not in the ratio (counting them would give less)",
              sli=ratio_num / ratio_den, with_rejected_counted=round(naive, 3))
        check(d("auth_throttle_blocks_total") == throttled, "throttle: auth_throttle_blocks moved by exactly the 429s answered", observed=d("auth_throttle_blocks_total"), answered=throttled)
        check(d("http_requests_total", route="/v1/runs", method="GET", status="401") == unauthorised
              and d("http_requests_total", route="/v1/runs", method="GET", status="429") == throttled,
              "throttle: http_requests_total{route=/v1/runs} 401 and 429 equal the answers seen")
        check(d("limiter_fail_open_total") == 0, "limiter: no fail-open while the database is up")
    finally:
        controls.stop()


# ------------------------------------------------------------------------------------------------ the worker: a dead-lettered job


def _dead_letter() -> None:
    worker = lib.Delta(lib.WORKER_URL)
    workspace = lib.base.db_one("select id from workspaces where slug = $1", lib.base.SLUG)
    job_type = "obs_e2e_unhandled"
    lib.base.db("insert into outbox_jobs (id, job_type, workspace_id, dedupe_key, payload) values ($1, $2, $3, $4, '{}'::jsonb)",
                uuid.uuid4(), job_type, workspace, f"obs-e2e-{uuid.uuid4()}")
    lib.poll("the worker dead-letters the job that has no handler", lambda: worker.refresh().d("worker_jobs_total", type=job_type, outcome="dead_letter") >= 1, timeout=60)
    check(worker.d("worker_jobs_total", type=job_type, outcome="dead_letter") == 1 and worker.d("processor_failures_total", type=job_type) == 1,
          "worker: a job with no handler moves worker_jobs{dead_letter} and processor_failures by exactly 1", type=job_type)
    row = lib.base.db_one("select status from outbox_jobs where job_type = $1", job_type)
    check(row == "dead_letter", "worker: the queue table agrees (status dead_letter)", status=row)
    check(lib.total(lib.scrape(lib.WORKER_URL), "worker_jobs_total", type=job_type, outcome="succeeded") == 0, "worker: the same job type shows no success")


# ------------------------------------------------------------------------------------------------ alerts


def _alert_delivery_failure(key: str, project: str) -> None:
    owner = lib.person("owner")
    recipient = f"{lib.sentinel('mail').lower()}@obs.test"
    destination = owner.post("/v1/alerts/destinations", json={"name": "obs-e2e-mail", "type": "email", "target": {"recipients": [recipient]}})
    if destination.status_code != 201:
        lib.die(f"create destination -> {destination.status_code} {destination.text[:160]}")
    rule = owner.post("/v1/alerts/rules", json={"name": "obs-e2e-run-failed", "trigger": "run_failed", "destination_ids": [destination.json()["id"]], "filters": {},
                                                 "cooldown_seconds": 0, "enabled": True, "project_id": project})
    if rule.status_code != 201:
        lib.die(f"create rule -> {rule.status_code} {rule.text[:160]}")
    worker = lib.Delta(lib.WORKER_URL)
    run = lib.run_ids()
    events = [
        {**lib.event(run, 1), "event_type": "run.started", "attributes": {}},
        {**lib.event(run, 2), "event_type": "run.failed", "status": "error"},
    ]
    sent = lib.ingest(lib.API_URL, key, events)
    check(sent.status_code == 202, "alerts: a run that failed is ingested", status=sent.status_code)
    lib.poll("the alert notification dead-letters (the relay is a closed port)", lambda: worker.refresh().d("alert_deliveries_total", status="dead") >= 1, timeout=120)
    by_status = {s: worker.d("alert_deliveries_total", status=s) for s in ("pending", "delivered", "failed", "dead", "skipped")}
    check(by_status["dead"] == 1 and by_status["delivered"] == 0 and by_status["failed"] + by_status["dead"] == 2 and worker.d("alert_deliveries_total", reason="smtp_failed") == 2,
          "alerts: alert_deliveries moved by exactly the attempts (1 failed + 1 dead, reason smtp_failed; ABB_ALERTS_MAX_ATTEMPTS=2), none delivered", by_status=by_status)
    lib.poll("the dead-letter gauge shows the dead notification", lambda: lib.total(lib.scrape(lib.WORKER_URL), "alert_dead_letters") == 1 or None, timeout=60)
    rows = lib.base.db("select status, count(*) as n from alert_notifications group by status")
    check({r["status"]: int(r["n"]) for r in rows} == {"dead": 1}, "alerts: the notification table agrees (exactly one dead notification)", rows={r["status"]: int(r["n"]) for r in rows})
    states = {labels["state"]: v for _, labels, v in lib.scrape(lib.API_URL)["alert_destinations"].samples}
    check(states.get("active", 0) + states.get("failing", 0) + states.get("paused", 0) == 1, "alerts: alert_destinations counts the one destination, never its name", states=states)


# ------------------------------------------------------------------------------------------------ sessions


def _sessions() -> None:
    api = lib.API_URL
    developer = lib.base.email("developer")
    extra = [lib.base.session_token(developer) for _ in range(3)]
    for index, token in enumerate(extra):
        lib.remember_secret(f"session-dev-extra-{index}", token)
    headers = {"Origin": lib.base.ORIGIN}
    me = httpx.get(f"{api}/v1/me", cookies={"abb_session": extra[0]}, headers=headers, timeout=15)
    check(me.status_code == 200, "sessions: an extra session of the developer works", status=me.status_code)
    listing = httpx.get(f"{api}/v1/auth/sessions", cookies={"abb_session": extra[0]}, headers=headers, timeout=15).json()["items"]
    delta = lib.Delta(api)
    others = [s for s in listing if not s["current"]]
    one = httpx.delete(f"{api}/v1/auth/sessions/{others[0]['id']}", cookies={"abb_session": extra[0]}, headers=headers, timeout=15)
    check(one.status_code == 204, "sessions: revoking one other session is 204", status=one.status_code)
    remaining = len(others) - 1
    revoked = httpx.post(f"{api}/v1/auth/sessions/revoke-others", cookies={"abb_session": extra[0]}, headers=headers, timeout=15)
    count = revoked.json()["revoked"]
    check(revoked.status_code == 200 and count == remaining, "sessions: revoke-others returns the number of sessions ended", revoked=count, expected=remaining)
    out = httpx.post(f"{api}/v1/auth/logout", cookies={"abb_session": extra[0]}, headers=headers, timeout=15)
    check(out.status_code == 200, "sessions: logout is 200", status=out.status_code)
    delta.refresh()
    check(delta.d("session_revocations_total", reason="revoke_one") == 1 and delta.d("session_revocations_total", reason="revoke_others") == count
          and delta.d("session_revocations_total", reason="logout") == 1,
          "sessions: session_revocations moved by exactly 1 revoke_one, the count revoked by revoke_others, and 1 logout", revoke_others=count)


# ------------------------------------------------------------------------------------------------ retention and backup textfiles


def _textfile_values(path: Path) -> dict[str, Any]:
    text = path.read_text()
    return lib.parse(text)


def _tool(module: str, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = dict(os.environ)
    for name in ("ABB_METRICS_TOKEN",):
        merged.pop(name, None)
    merged["ABB_DATABASE_URL"] = os.environ["E2E_OWNER_DB_URL"]  # owner: the tools read and delete everything
    merged.update(env or {})
    return subprocess.run([str(lib.base.API_PYTHON), "-m", module, *args], cwd=lib.API_DIR, env=merged, capture_output=True, text=True, check=False, timeout=300)  # noqa: S603


def _retention(project: str, key: str) -> None:
    old_runs = []
    for _ in range(2):
        run = lib.run_ids()
        old_runs.append(run["run_id"])
        reply = lib.ingest(lib.API_URL, key, [lib.event(run, n + 1) for n in range(4)])
        if reply.status_code != 202:
            lib.die(f"ingest -> {reply.status_code}")
    lib.poll("the runs are derived", lambda: _count("select count(*) from runs where project_id = $1", lib.base.uuid_of(project)) >= 2, timeout=60)
    lib.poll("the derivation queue is idle", _derivation_idle, timeout=90)
    ids = [lib.base.uuid_of(r) for r in old_runs]
    lib.base.db("update runs set created_at = now() - interval '800 days', updated_at = now() - interval '799 days', "
                "started_at = now() - interval '800 days', completed_at = now() - interval '799 days' where id = any($1::uuid[])", ids)
    aged = _count("select count(*) from runs where id = any($1::uuid[])", ids)
    check(aged == 2, "retention: two runs are aged 800 days in the throwaway database", aged=aged)
    textfile = lib.WORK / "retention.prom"
    run_env = {"ABB_RETENTION_MODE": "dry_run", "ABB_ARTIFACT_DIR": os.environ["ABB_ARTIFACT_DIR"]}
    dry = _tool("abb_api.retention", "run-once", "--metrics-textfile", str(textfile), env=run_env)
    check(dry.returncode == 0, "retention: a dry-run tick through the CLI exits 0", exit_code=dry.returncode, stderr=dry.stderr[-160:])
    families = _textfile_values(textfile)
    check(lib.value(families, "retention_passes_total", mode="dry_run") == 1 and lib.value(families, "retention_mode", mode="dry_run") == 1,
          "retention: the textfile holds retention_passes{dry_run} = 1 and retention_mode{dry_run} = 1")
    candidates = {labels["tier"]: v for _, labels, v in families["retention_dry_run_candidates"].samples}
    check(candidates.get("run") == 2, "retention: retention_dry_run_candidates{tier=run} = exactly the 2 aged runs", candidates=candidates)
    check(_count("select count(*) from runs where id = any($1::uuid[])", ids) == 2 and lib.total(families, "retention_deleted_total") == 0,
          "retention: a dry run deleted nothing (rows still there, no deleted counter)")
    owned = {"retention_passes_total", "retention_deleted_total", "retention_failures_total", "retention_dry_run_candidates", "retention_mode"}
    check(set(families) <= owned, "retention: the textfile carries only the retention families", extra=sorted(set(families) - owned))
    enforce = _tool("abb_api.retention", "run-once", "--metrics-textfile", str(textfile), env={**run_env, "ABB_RETENTION_MODE": "enforce"})
    check(enforce.returncode == 0, "retention: an enforce tick through the CLI exits 0", exit_code=enforce.returncode, stderr=enforce.stderr[-160:])
    families = _textfile_values(textfile)
    left = _count("select count(*) from runs where id = any($1::uuid[])", ids)
    check(left == 0 and lib.value(families, "retention_deleted_total", tier="run", kind="runs") == 2, "retention: enforce deleted the 2 runs and the textfile says retention_deleted{run, runs} = 2",
          rows_left=left, deleted=lib.value(families, "retention_deleted_total", tier="run", kind="runs"))
    check(lib.value(families, "retention_passes_total", mode="dry_run") == 1 and lib.value(families, "retention_passes_total", mode="enforce") == 1
          and lib.value(families, "retention_mode", mode="enforce") == 1 and lib.value(families, "retention_mode", mode="dry_run") == 0,
          "retention: the file accumulated across invocations (dry_run pass kept, enforce pass added, the effective mode is enforce)")
    mode = lib.poll("the API reports the newest retention report's mode", lambda: lib.value(lib.scrape(lib.API_URL), "retention_mode", mode="enforce") == 1.0 or None, timeout=60)
    since = lib.value(lib.scrape(lib.API_URL), "retention_seconds_since_last_pass")
    check(bool(mode) and since == since and since >= 0, "retention: the scrape-time gauges now show mode=enforce and a finite 'seconds since last pass'", seconds=round(since, 1))
    mode_worker = lib.value(lib.scrape(lib.WORKER_URL), "retention_mode", mode="enforce")
    check(mode_worker == 1.0, "retention: the worker's exposition agrees", mode_enforce=mode_worker)


def _backup() -> None:
    out = lib.WORK / "backup-bundle"
    textfile = lib.WORK / "backup.prom"
    pg = os.environ.get("PATH", "")
    if not any((Path(p) / "pg_dump").exists() for p in pg.split(os.pathsep)):
        for candidate in sorted(Path("/usr/lib/postgresql").glob("*/bin"), reverse=True):
            pg = f"{candidate}{os.pathsep}{pg}"
            break
    env = {"ABB_ARTIFACT_DIR": os.environ["ABB_ARTIFACT_DIR"], "ABB_BACKUP_DATABASE_URL": os.environ["E2E_OWNER_DB_URL"], "PATH": pg}
    done = _tool("abb_api.backup", "create", "--out", str(out), "--metrics-textfile", str(textfile), env=env)
    check(done.returncode == 0, "backup: `backup create` exits 0", exit_code=done.returncode, stderr=done.stderr[-200:])
    families = _textfile_values(textfile)
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file() and p.name != "MANIFEST.json")  # the manifest describes the data, it is not counted in it
    check(lib.value(families, "backup_runs_total", outcome="succeeded") == 1 and lib.value(families, "backup_runs_total", outcome="failed") == 0,
          "backup: backup_runs{succeeded} = 1, failed = 0")
    stamp = lib.value(families, "backup_last_success_timestamp_seconds")
    import time  # noqa: PLC0415

    check(0 < time.time() - stamp < 3600, "backup: backup_last_success_timestamp is a real Unix time of this run (within the hour)", age_seconds=round(time.time() - stamp))
    check(lib.value(families, "backup_bundle_bytes") == size, "backup: backup_bundle_bytes equals the bytes of the data files of the bundle on disk (the manifest excluded)", reported=lib.value(families, "backup_bundle_bytes"), on_disk=size)
    check(lib.value(families, "backup_last_duration_seconds") > 0, "backup: a duration was recorded")
    owned = {"backup_last_success_timestamp_seconds", "backup_last_duration_seconds", "backup_bundle_bytes", "backup_runs_total"}
    check(set(families) <= owned, "backup: the textfile carries only the backup families", extra=sorted(set(families) - owned))
    text = textfile.read_text()
    check(str(out) not in text and lib.base.SLUG not in text and os.environ["E2E_OWNER_DB_URL"].split("@")[0] not in text, "backup: the textfile has no path, name or credential")
    bad = _tool("abb_api.backup", "create", "--out", str(out), "--metrics-textfile", str(textfile), env=env)  # the output directory is no longer empty
    families = _textfile_values(textfile)
    check(bad.returncode != 0 and lib.value(families, "backup_runs_total", outcome="failed") == 1 and lib.value(families, "backup_runs_total", outcome="succeeded") == 1
          and lib.value(families, "backup_last_success_timestamp_seconds") == stamp,
          "backup: a refused second run counts as failed and keeps the last success timestamp", exit_code=bad.returncode)
    mode = textfile.stat().st_mode & 0o777
    check(mode == 0o644, "backup: the textfile is written with mode 0644 (the collector reads it as another user)", mode=oct(mode))


def gate2() -> None:
    gate("gate2-traffic")
    project, key = _ingest_happy_path()
    _refusals()
    _dead_letter()
    _alert_delivery_failure(key, project)
    _sessions()
    _retention(project, key)
    _backup()
    lib.finish()

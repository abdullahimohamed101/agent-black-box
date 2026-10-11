"""`GET /v1/runs/{run_id}/findings` end to end: events in through the API, findings out of it."""

import json
import re
from typing import Any

import pytest
from abb_event_schema.ids import to_uuid
from sqlalchemy import text

from abb_api.findings import job as findings_job
from abb_api.findings.detectors import DETECTORS_VERSION, run_all
from abb_api.findings.model import Detector, DetectorContext, Finding
from abb_api.jobs.handlers import HANDLERS
from abb_api.jobs.outbox import OutboxRepository
from abb_api.jobs.worker import Worker
from tests.api_fixtures import Api, person
from tests.conftest import make_settings
from tests.detector_helpers import load_runs

CANARIES = (
    "CMDCANARY-curl-H-Bearer-s3cr3t",
    "/home/PATHCANARY/.ssh/id_rsa",
    "CUSTOMCANARY-rm-rf",
    "CWDCANARY",
    "RETRYREASONCANARY",
    "POLICYCANARY",
    "SUITECANARY",
    "RUNNAMECANARY",
    "TAGCANARY",
    "PAYLOADCANARY",
    "URLCANARY",
)
VERSION = re.compile(r"^[a-z][a-z-]*@\d+\.\d+\.\d+$")


async def post_wires(api: Api, wires: list[dict[str, Any]], token: str = "writer") -> None:
    for start in range(0, len(wires), 200):
        response = await api.post_batch(wires[start : start + 200], token=token)
        assert response.status_code == 202, response.text


def fixture_wires(*names: str) -> list[dict[str, Any]]:
    return [w for n in names for run in load_runs(n).values() for w in run]


def canary_wires(name: str) -> list[dict[str, Any]]:
    """A fixture run with captured content (every canary kind) on every event."""
    wires = [json.loads(json.dumps(w)) for w in fixture_wires(name)]
    for w in wires:
        w["attributes"] = {
            **w.get("attributes", {}),
            "shell.command": CANARIES[0],
            "shell.cwd": "/srv/CWDCANARY",
            "file.path": CANARIES[1],
            "thing.command": CANARIES[2],
            "retry.reason": CANARIES[4],
            "policy.reason": CANARIES[5],
            "test.suite": CANARIES[6],
            "http.url": "https://URLCANARY.example/x",
        }
        w["tags"] = [CANARIES[8]]
        w["payload"] = {"prompt": CANARIES[9]}
        if w["event_type"] == "run.started":
            w["attributes"]["run.name"] = CANARIES[7]
    return wires


async def findings(api: Api, run_id: str, token: str = "reader") -> dict[str, Any]:
    response = await api.get(f"/v1/runs/{run_id}/findings", token=token)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def run_id_of(name: str) -> str:
    return list(load_runs(name))[-1]


# ------------------------------------------------------------------ the finding contract (§81.2)


async def test_every_finding_honours_the_contract_and_its_evidence_resolves(api: Api) -> None:
    names = ("looping", "failing", "retry_storm", "cascade", "context_growth", "identical")
    await post_wires(api, fixture_wires(*names))
    await api.drain()
    seen_types: set[str] = set()
    for name in names:
        run_id = run_id_of(name)
        body = await findings(api, run_id)
        assert body["run_id"] == run_id and body["state"]["status"] == "current"
        assert body["state"]["detectors_version"] == DETECTORS_VERSION
        assert body["items"], name
        for item in body["items"]:
            seen_types.add(item["finding_type"])
            assert VERSION.match(item["detector_version"]), item["detector_version"]
            assert item["id"].startswith("fnd_") and item["run_id"] == run_id
            assert item["kind"] in ("deterministic", "probable")
            assert item["severity"] in ("info", "warning", "critical")
            assert 0 <= item["confidence"] <= 1
            assert item["title"].startswith("Probable:") == (item["kind"] == "probable")
            assert item["message"] and len(item["fingerprint"]) == 64
            cited = {i for i in (item["start_event_id"], item["end_event_id"]) if i}
            cited |= set(item["evidence_event_ids"])
            assert cited
            for event_id in cited:
                got = await api.get(f"/v1/runs/{run_id}/events/{event_id}")
                assert got.status_code == 200, (name, item["finding_type"], event_id)
            if item["finding_type"] in ("possible_loop", "retry_storm", "context_growth"):
                assert "thresholds" in item["evidence"]
    assert {"possible_loop", "failure_chain", "retry_storm", "tool_failure_cascade"} <= seen_types


async def test_a_healthy_run_has_no_findings_and_a_readable_health(api: Api) -> None:
    await post_wires(api, fixture_wires("healthy"))
    await api.drain()
    body = await findings(api, run_id_of("healthy"))
    assert body["items"] == [] and body["state"]["status"] == "current"
    names = {d["name"]: d for d in body["health"]["dimensions"]}
    assert set(names) == {"reliability", "tool_reliability", "cost_efficiency", "loops", "context"}
    assert names["loops"]["status"] == "ok" and names["context"]["status"] == "ok"
    assert all(d["rule"] and isinstance(d["inputs"], dict) for d in names.values())
    assert "score" not in json.dumps(body).lower()


async def test_a_failed_run_reads_bad_from_its_summary(api: Api) -> None:
    await post_wires(api, fixture_wires("failing"))
    await api.drain()
    health = (await findings(api, run_id_of("failing")))["health"]
    reliability = next(d for d in health["dimensions"] if d["name"] == "reliability")
    assert reliability["status"] == "bad" and health["overall"] == "bad"


# ------------------------------------------------------------------ state


async def test_state_walks_none_processing_current(api: Api) -> None:
    await post_wires(api, fixture_wires("looping"))
    run_id = run_id_of("looping")
    before = await findings(api, run_id)
    assert before["state"]["status"] == "none" and before["items"] == []
    assert before["state"]["computed_at"] is None
    loops = {d["name"]: d["status"] for d in before["health"]["dimensions"]}
    assert loops["loops"] == "no_data" and loops["context"] == "no_data"

    assert await api.worker.run_once() >= 1  # the summarizer runs and queues the analysis
    middle = await findings(api, run_id)
    assert middle["state"]["status"] == "processing"

    await api.drain()
    after = await findings(api, run_id)
    assert after["state"]["status"] == "current" and after["items"]
    assert after["state"]["event_count_seen"] == len(load_runs("looping")[run_id])
    assert after["state"]["truncated"] is False


async def test_stored_findings_from_older_rules_read_outdated_never_current(api: Api) -> None:
    await post_wires(api, fixture_wires("looping"))
    await api.drain()
    run_id = run_id_of("looping")
    async with api.engine.begin() as conn:
        await conn.execute(text("UPDATE run_findings SET detectors_version = :v"), {"v": 0})
    body = await findings(api, run_id)
    assert body["state"]["status"] == "outdated" and body["items"]
    dims = {d["name"]: d for d in body["health"]["dimensions"]}
    assert dims["loops"]["status"] == "no_data" and "outdated" in dims["loops"]["rule"]
    badge = (await api.get(f"/v1/runs/{run_id}")).json()["findings"]
    assert badge["state"] == "outdated"


class Raising(Detector):
    name = "raising"
    version = "1.0.0"

    def detect(self, ctx: DetectorContext) -> list[Finding]:
        raise ValueError(CANARIES[0])


async def test_after_a_dead_letter_the_previous_analysis_is_still_returned_as_failed(
    api: Api, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await post_wires(api, fixture_wires("looping"))
    await api.drain()
    run_id = run_id_of("looping")
    good = await findings(api, run_id)
    assert good["items"] and good["state"]["status"] == "current"

    monkeypatch.setattr(findings_job, "run_all", lambda ctx: run_all(ctx, (Raising(),)))
    worker = Worker(
        api.worker._engine,
        HANDLERS,
        make_settings(
            database_url, worker_backoff_base_seconds=0.0, worker_backoff_max_seconds=0.0
        ),
        owner="dead-letter",
    )
    async with api.engine.begin() as conn:
        await OutboxRepository(conn, api.tenant.context).enqueue_detect([to_uuid(run_id)])
    for _ in range(12):
        await worker.run_once()

    failed = await findings(api, run_id)
    assert failed["state"]["status"] == "failed"
    assert failed["items"] == good["items"]  # the last successful analysis, not an empty list
    assert failed["state"]["computed_at"] == good["state"]["computed_at"]
    assert failed["state"]["event_count_seen"] == good["state"]["event_count_seen"]
    dims = {d["name"]: d for d in failed["health"]["dimensions"]}
    assert dims["loops"]["status"] == "no_data" and "failed" in dims["loops"]["rule"]
    assert (await api.get(f"/v1/runs/{run_id}")).json()["summary_state"] == "current"
    assert CANARIES[0] not in json.dumps(failed)


async def test_ids_and_first_seen_survive_a_rewrite(api: Api) -> None:
    await post_wires(api, fixture_wires("looping"))
    await api.drain()
    run_id = run_id_of("looping")
    first = await findings(api, run_id)
    async with api.engine.begin() as conn:
        await OutboxRepository(conn, api.tenant.context).enqueue_detect([to_uuid(run_id)])
    await api.drain()
    second = await findings(api, run_id)
    assert [(i["id"], i["created_at"], i["fingerprint"]) for i in second["items"]] == [
        (i["id"], i["created_at"], i["fingerprint"]) for i in first["items"]
    ]
    assert second["state"]["computed_at"] > first["state"]["computed_at"]


# ------------------------------------------------------------------ the badge on the runs list


async def test_the_runs_list_carries_a_badge_from_run_findings_only(api: Api) -> None:
    await post_wires(api, fixture_wires("looping", "healthy"))
    run_id = run_id_of("looping")
    listed = (await api.get("/v1/runs", limit=50)).json()["items"]
    assert {r["findings"]["state"] for r in listed} == {"none"}  # nothing analysed yet
    assert all(r["findings"]["count"] == 0 for r in listed)
    await api.drain()
    listed = {r["id"]: r for r in (await api.get("/v1/runs", limit=50)).json()["items"]}
    full = await findings(api, run_id)
    detail = full["items"]
    looping = listed[run_id]["findings"]
    assert looping["count"] == len(detail) > 0 and looping["state"] == "current"
    worst = max(detail, key=lambda i: ("info", "warning", "critical").index(i["severity"]))
    assert looping["max_severity"] == worst["severity"]
    healthy = listed[run_id_of("healthy")]["findings"]
    assert healthy == {"count": 0, "max_severity": None, "state": "current"}
    assert (await api.get(f"/v1/runs/{run_id}")).json()["findings"] == looping


async def test_the_badge_is_one_query_for_a_whole_page(api: Api) -> None:
    from sqlalchemy import event

    await post_wires(api, fixture_wires("looping", "healthy", "failing", "cascade"))
    await api.drain()
    statements: list[str] = []
    app_engine = api.app.state.engine.sync_engine

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    event.listen(app_engine, "before_cursor_execute", record)
    try:
        assert (await api.get("/v1/runs", limit=50)).status_code == 200
    finally:
        event.remove(app_engine, "before_cursor_execute", record)
    assert sum("run_findings" in s for s in statements) == 1
    assert not any("findings" in s and "outbox_jobs" in s for s in statements)


# ------------------------------------------------------------------ nothing is content


async def test_findings_carry_no_captured_content_and_every_reader_gets_the_same_body(
    web: Api,
) -> None:
    await post_wires(web, canary_wires("failing") + canary_wires("looping"))
    await web.drain()
    bodies: dict[str, dict[str, Any]] = {}
    people = {
        actor: await person(web, f"{actor}@acme.test", role)
        for actor, role in (("owner", "OWNER"), ("viewer", "VIEWER"), ("billing", "BILLING"))
    }
    for name in ("failing", "looping"):
        run_id = run_id_of(name)
        for actor, headers in people.items():
            response = await web.client.get(f"/v1/runs/{run_id}/findings", headers=headers)
            if actor == "billing":  # BILLING holds no `run.read`
                assert response.status_code == 403
                continue
            assert response.status_code == 200, response.text
            bodies[f"{name}:{actor}"] = response.json()
        for token in ("reader", "wide_reader"):
            bodies[f"{name}:{token}"] = await findings(web, run_id, token)
        assert bodies[f"{name}:owner"]["items"], name
        shapes = {
            key: json.dumps({k: body[k] for k in ("items", "health")}, sort_keys=True)
            for key, body in bodies.items()
            if key.startswith(name)
        }
        assert len(set(shapes.values())) == 1, shapes  # byte-identical for every actor
    everything = json.dumps(bodies)
    for canary in CANARIES:
        assert canary not in everything, canary
    # no payload digest anywhere: the only long hex strings are the fingerprints
    unexplained = re.sub(r'"fingerprint": "[0-9a-f]{64}"', "", everything)
    assert not re.search(r"[0-9a-f]{12,}", unexplained)


# ------------------------------------------------------------------ not found / denied


async def test_unknown_foreign_and_malformed_run_ids_are_identical_404s(api: Api) -> None:
    await post_wires(api, fixture_wires("looping"), token="other")  # a globex run
    await api.drain()
    foreign = run_id_of("looping")
    bodies = []
    for run_id in (foreign, "run_01M4QNE6M04CKP6JH7TAZ62Q99", "not-an-id"):
        response = await api.get(f"/v1/runs/{run_id}/findings")
        assert response.status_code == 404
        error = response.json()["error"]
        error.pop("request_id", None)
        bodies.append(error)
    assert bodies[0] == bodies[1] == bodies[2]


async def test_a_key_without_runs_read_is_refused(api: Api) -> None:
    response = await api.get(
        "/v1/runs/run_01M4QNE6M04CKP6JH7TAZ62Q99/findings", token="ingest_only"
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INSUFFICIENT_SCOPE"


async def test_a_project_bound_key_cannot_read_findings_of_another_project(api: Api) -> None:
    """A run of project alpha, read with a key bound to beta: the same 404 as a random id, an
    empty runs list, and the workspace-wide key (which may see both) still reads it."""
    await post_wires(api, fixture_wires("looping"))  # the writer is bound to alpha
    await api.drain()
    run_id = run_id_of("looping")
    assert (await api.get(f"/v1/runs/{run_id}/findings", token="wide_reader")).status_code == 200

    unknown = await api.get("/v1/runs/run_01M4QNE6M04CKP6JH7TAZ62Q99/findings", token="beta")
    hidden = await api.get(f"/v1/runs/{run_id}/findings", token="beta")
    assert hidden.status_code == unknown.status_code == 404
    bodies = []
    for response in (hidden, unknown):
        error = response.json()["error"]
        error.pop("request_id", None)
        bodies.append(error)
    assert bodies[0] == bodies[1]
    assert (await api.get("/v1/runs", token="beta")).json()["items"] == []


async def test_the_badge_and_findings_of_one_workspace_never_reach_another(api: Api) -> None:
    await post_wires(api, fixture_wires("looping"))
    await api.drain()
    run_id = run_id_of("looping")
    assert (await api.get(f"/v1/runs/{run_id}", token="other")).status_code == 404
    assert (await api.get(f"/v1/runs/{run_id}/findings", token="other")).status_code == 404
    listed = (await api.get("/v1/runs", token="other", limit=50)).json()["items"]
    assert run_id not in {r["id"] for r in listed}

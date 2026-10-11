"""`GET /v1/runs/{run_id}/compare` over HTTP: roles, projections, bounds, scope (AC-5, AC-6)."""

import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from abb_event_schema.event import finalize
from abb_event_schema.ids import IdKind, from_uuid, to_uuid
from abb_event_schema.parse import parse_event_in
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.api_fixtures import WEB_ORIGIN, Api, build_api, person
from tests.conftest import make_settings
from tests.evaluation_helpers import add_evaluator, trigger
from tests.ingest_helpers import RECEIVED, make_run_ids, wire_event
from tests.test_summarizer import ingest

FIXTURE = Path(__file__).parent / "fixtures" / "compare_pair"
COMMAND = "CMDCANARY-curl-H-Bearer-s3cr3t"
OTHER_COMMAND = "OTHERCMD-rm-rf-everything"
PATH = "/home/PATHCANARY/.ssh/id_rsa"
OTHER_PATH = "/home/ELSEPATH/.aws/credentials"


async def load(api: Api, wires: list[dict[str, Any]], project: str = "alpha") -> str:
    events = [
        finalize(
            parse_event_in(w),
            workspace_id=api.tenant.workspace_id,
            project_id=api.tenant.projects[project],
            received_at=RECEIVED,
        )
        for w in wires
    ]
    await ingest(api.engine, api.tenant, events)
    await api.drain()
    return str(wires[0]["run_id"])


def fixture_wires(name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (FIXTURE / name).read_text().splitlines()]


async def demo_pair(api: Api) -> tuple[str, str, dict[str, Any]]:
    manifest = json.loads((FIXTURE / "manifest.json").read_text())
    baseline = await load(api, fixture_wires("baseline.ndjson"))
    wrong = await load(api, fixture_wires("wrong_fix.ndjson"))
    return baseline, wrong, manifest


async def compare(api: Api, headers: dict[str, str], run: str, other: str) -> Any:
    return await api.client.get(
        f"/v1/runs/{run}/compare", params={"with_run_id": other}, headers=headers
    )


def variant_run(command: str, path: str) -> list[dict[str, Any]]:
    ids = make_run_ids()
    specs: list[tuple[str, dict[str, Any], str | None]] = [
        ("run.started", {}, None),
        ("shell.command.completed", {"shell.command": command, "shell.exit_code": 0}, "success"),
        ("file.modified", {"file.path": path, "file.language": "python"}, None),
        ("tool.call.completed", {"tool.name": "edit_file"}, "success"),
        ("run.completed", {}, "success"),
    ]
    return [
        wire_event(
            ids,
            n,
            event_type=kind,
            attributes=attrs,
            status=status,
            **({"span_id": ...} if kind.startswith("run.") else {}),
        )
        for n, (kind, attrs, status) in enumerate(specs, start=1)
    ]


async def test_the_demo_pair_diverges_at_the_second_fix_for_an_owner(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    baseline, wrong, manifest = await demo_pair(web)
    response = await compare(web, owner, baseline, wrong)
    assert response.status_code == 200, response.text[:300]
    body = response.json()
    assert body["alignment"] == "content" and body["alignment_quality"] == "full"
    assert (body["left"]["run_id"], body["right"]["run_id"]) == (baseline, wrong)
    assert (body["left"]["status"], body["right"]["status"]) == ("SUCCESS", "FAILED")
    div = body["divergence"]
    assert div["relation"] == "changed"
    first = body["steps"][div["index"]]
    assert first["left"]["event_id"] == manifest["baseline"]["second_edit_read"]
    assert first["right"]["event_id"] == manifest["wrong_fix"]["second_edit_read"]
    assert first["left"]["label"] == "app/session.py" and first["right"]["label"] == "app/oauth.py"
    outcome = body["steps"][body["first_outcome_difference"]]
    assert outcome["left"]["event_id"] == manifest["baseline"]["second_test_run"]
    assert (outcome["left"]["status"], outcome["right"]["status"]) == ("success", "error")
    metrics = {m["metric"]: m for m in body["summary"]}
    assert (
        metrics["tool_calls"]["delta"]
        == metrics["tool_calls"]["right"] - metrics["tool_calls"]["left"]
    )
    assert metrics["tool_calls"]["delta"] < 0
    assert body["files"]["paths_withheld"] is False
    assert body["files"]["only_left"] == [] or "app/session.py" in body["files"]["both"]


async def test_a_viewer_diverges_at_the_second_test_run_and_gets_counts_only(web: Api) -> None:
    viewer = await person(web, "v@acme.test", "VIEWER")
    baseline, wrong, manifest = await demo_pair(web)
    body = (await compare(web, viewer, baseline, wrong)).json()
    assert body["alignment"] == "metadata"
    index = body["divergence"]["index"]
    assert index == body["first_outcome_difference"]
    row = body["steps"][index]
    assert row["left"]["event_id"] == manifest["baseline"]["second_test_run"]
    assert row["right"]["event_id"] == manifest["wrong_fix"]["second_test_run"]
    assert body["files"]["paths_withheld"] is True
    assert body["files"]["only_left"] == body["files"]["only_right"] == body["files"]["both"] == []
    text = json.dumps(body)
    for needle in ("app/session.py", "app/oauth.py", "unittest discover", "tail -n", "abb-a1b2"):
        assert needle not in text, needle


async def test_runs_that_differ_only_in_commands_and_paths_compare_all_equal_to_a_viewer(
    web: Api,
) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    viewer = await person(web, "v@acme.test", "VIEWER")
    a = await load(web, variant_run(COMMAND, PATH))
    b = await load(web, variant_run(OTHER_COMMAND, OTHER_PATH))
    seen = (await compare(web, viewer, a, b)).json()
    assert {r["relation"] for r in seen["steps"]} == {"equal"} and seen["divergence"] is None
    assert seen["first_outcome_difference"] is None and seen["alignment"] == "metadata"
    assert seen["files"] == {
        "paths_withheld": True,
        "left_count": 1,
        "right_count": 1,
        "only_left": [],
        "only_right": [],
        "both": [],
        "truncated": False,
    }
    text = json.dumps(seen)
    for needle in (COMMAND, OTHER_COMMAND, PATH, OTHER_PATH, "PATHCANARY", "ELSEPATH"):
        assert needle not in text
    full = (await compare(web, owner, a, b)).json()
    assert full["divergence"] is not None  # the owner does see the difference
    assert full["files"]["only_left"] and full["files"]["only_right"]


async def test_comparing_a_run_with_itself_is_all_equal_and_either_order_works(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    baseline, wrong, _ = await demo_pair(web)
    same = (await compare(web, owner, baseline, baseline)).json()
    assert {r["relation"] for r in same["steps"]} == {"equal"} and same["divergence"] is None
    forward = (await compare(web, owner, baseline, wrong)).json()
    backward = (await compare(web, owner, wrong, baseline)).json()
    assert forward["divergence"]["index"] == backward["divergence"]["index"]
    assert [(r["left"], r["right"]) for r in forward["steps"]] == [
        (r["right"], r["left"]) for r in backward["steps"]
    ]
    assert (await compare(web, owner, baseline, wrong)).json() == forward  # deterministic


async def test_two_requests_return_the_same_bytes(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    baseline, wrong, _ = await demo_pair(web)
    one, two = (
        await compare(web, owner, baseline, wrong),
        await compare(web, owner, baseline, wrong),
    )
    assert one.content == two.content


async def test_evaluation_scores_cost_and_summary_are_diffed_without_explanations(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    viewer = await person(web, "v@acme.test", "VIEWER")
    baseline, wrong, _ = await demo_pair(web)
    evaluator = await add_evaluator(web.engine, web.tenant, "run_succeeded", name="ok")
    for run in (baseline, wrong):
        await trigger(web.engine, web.tenant, to_uuid(run), evaluator)
    await web.drain()
    for headers in (owner, viewer):
        body = (await compare(web, headers, baseline, wrong)).json()
        (diff,) = body["evaluations"]
        assert diff["evaluator_name"] == "ok" and diff["ground_truth"] is True
        assert diff["left"] == {"score": 1.0, "label": "pass"}
        assert diff["right"] == {"score": 0.0, "label": "fail"} and diff["delta"] == -1.0
        assert "explanation" not in json.dumps(body)
    assert all(m["metric"] != "status" for m in body["summary"])
    models = {m["model"] for m in body["cost_by_model"]}
    assert models <= {"scripted-agent-1", "unknown"}


async def test_foreign_missing_and_project_scoped_runs_are_one_404(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    baseline, wrong, _ = await demo_pair(web)
    foreign_run = await load_foreign(web)
    random_run = from_uuid(IdKind.RUN, uuid.uuid4())
    bodies = []
    for left, right in ((baseline, foreign_run), (foreign_run, baseline), (baseline, random_run)):
        response = await compare(web, owner, left, right)
        assert response.status_code == 404 and response.json()["error"]["code"] == "RUN_NOT_FOUND"
        bodies.append({**response.json()["error"], "request_id": None})
    assert bodies[0] == bodies[1] == bodies[2]
    # A key bound to project beta cannot compare alpha's runs, and says what an unknown id says.
    beta = web.headers("beta")
    del beta["content-type"]
    response = await web.client.get(
        f"/v1/runs/{baseline}/compare", params={"with_run_id": wrong}, headers=beta
    )
    assert response.status_code == 404 and response.json()["error"]["code"] == "RUN_NOT_FOUND"
    reader = web.headers("reader")
    del reader["content-type"]
    ok = await web.client.get(
        f"/v1/runs/{baseline}/compare", params={"with_run_id": wrong}, headers=reader
    )
    assert ok.status_code == 200  # alpha's own key sees alpha's runs (and payload.read)


async def load_foreign(api: Api) -> str:
    wires = variant_run("ls", "x.py")
    events = [
        finalize(
            parse_event_in(w),
            workspace_id=api.other.workspace_id,
            project_id=api.other.projects["p"],
            received_at=RECEIVED,
        )
        for w in wires
    ]
    await ingest(api.engine, api.other, events)
    await api.drain()
    return str(wires[0]["run_id"])


async def test_a_malformed_second_run_id_is_a_422_and_the_caller_needs_run_read(web: Api) -> None:
    owner = await person(web, "o@acme.test", "OWNER")
    billing = await person(web, "b@acme.test", "BILLING")
    baseline, wrong, _ = await demo_pair(web)
    bad = await compare(web, owner, baseline, "not-a-run")
    assert bad.status_code == 422
    missing = await web.client.get(f"/v1/runs/{baseline}/compare", headers=owner)
    assert missing.status_code == 422
    denied = await compare(web, billing, baseline, wrong)
    assert denied.status_code == 403


@pytest.fixture
async def tight(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> AsyncIterator[Api]:
    settings = make_settings(
        database_url,
        web_origin=WEB_ORIGIN,
        compare_max_events=100,
        compare_max_steps=30,
        compare_max_edits=1,
    )
    async for instance in build_api(database_url, engine, settings=settings):
        yield instance


async def test_long_runs_are_cut_at_the_caps_and_a_hard_middle_answers_partial(tight: Api) -> None:
    owner = await person(tight, "o@acme.test", "OWNER")
    ids_a, ids_b = make_run_ids(), make_run_ids()

    def long_run(ids: dict[str, str], names: list[str]) -> list[dict[str, Any]]:
        wires = [wire_event(ids, 1, event_type="run.started", attributes={}, span_id=...)]
        for n, name in enumerate(names, start=2):
            wires.append(
                wire_event(
                    ids,
                    n,
                    event_type="tool.call.completed",
                    status="success",
                    attributes={"tool.name": name},
                )
            )
        wires.append(
            wire_event(
                ids,
                len(names) + 2,
                event_type="run.completed",
                status="success",
                attributes={},
                span_id=...,
            )
        )
        return wires

    a = await load(tight, long_run(ids_a, [f"t{n % 3}" for n in range(200)]))
    b = await load(tight, long_run(ids_b, [f"u{(n * 7) % 5}" for n in range(200)]))
    body = (await compare(tight, owner, a, b)).json()
    assert body["left"]["truncated"] is True and body["right"]["truncated"] is True
    assert body["left"]["steps"] <= 30 and body["left"]["truncated_at_event"]
    assert body["alignment_quality"] == "partial"
    index = body["divergence"]["index"]
    assert body["steps"][index]["relation"] in ("left_only", "right_only", "changed")
    assert len(body["steps"]) <= 60

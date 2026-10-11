"""Findings are metadata only (ADR-080): every actor that may read a run gets the same findings.

The world's runs are too small to produce findings, so this file ingests the pathological
fixtures with captured content (every canary kind) into acme, and a looping run into globex whose
agent and tool names carry the globex canary. Then: no content canary in any body for any actor,
byte-identical `items` and `health` across roles and keys, and nothing of globex in acme's reads.
"""

import json
from typing import Any

from abb_api.findings.detectors import DETECTORS_VERSION
from tests.authz.world import CANARY, CANARY_SLUG, CONTENT_CANARIES, World, body_text
from tests.detector_helpers import load_runs

READERS = (
    "owner", "admin", "developer", "viewer", "security", "downgraded", "dual",
    "writer", "reader", "wide", "wide_reader",
)  # fmt: skip
DENIED = ("billing",)


def with_content(name: str) -> list[dict[str, Any]]:
    wires = [json.loads(json.dumps(w)) for w in (w for r in load_runs(name).values() for w in r)]
    for w in wires:
        w["attributes"] = {
            **w.get("attributes", {}),
            "shell.command": CONTENT_CANARIES[0],
            "shell.cwd": "/srv/CWDCANARY",
            "file.path": CONTENT_CANARIES[1],
            "thing.command": CONTENT_CANARIES[2],
            "retry.reason": "RETRYREASONCANARY",
            "test.suite": "SUITECANARY",
        }
        w["payload"] = {"prompt": "PAYLOADCANARY"}
    return wires


def globex_cascade() -> list[dict[str, Any]]:
    wires = [json.loads(json.dumps(w)) for r in load_runs("looping").values() for w in r]
    for w in wires:
        w["agent_id"] = CANARY_SLUG
        if "tool.name" in w.get("attributes", {}):
            w["attributes"]["tool.name"] = f"{CANARY_SLUG}-tool"
    return wires


async def test_findings_are_identical_for_every_reader_and_carry_no_content(world: World) -> None:
    api = world.api
    for batch in (with_content("failing"), with_content("looping")):
        assert (await api.post_batch(batch, token="writer")).status_code == 202
    assert (await api.post_batch(globex_cascade(), token="other")).status_code == 202
    await api.drain()
    acme_runs = [next(iter(load_runs(n))) for n in ("failing", "looping")]
    globex_run = next(iter(load_runs("cascade")))

    for run_id in acme_runs:
        bodies = {}
        for actor in READERS:
            response = await world.get(f"/v1/runs/{run_id}/findings", actor)
            assert response.status_code == 200, (actor, response.text)
            bodies[actor] = response.json()
        owner = bodies["owner"]
        assert owner["items"] and owner["state"]["detectors_version"] == DETECTORS_VERSION
        for actor, body in bodies.items():
            assert body["items"] == owner["items"], actor
            assert body["health"] == owner["health"], actor
            text = body_text(await world.get(f"/v1/runs/{run_id}/findings", actor))
            for canary in (*CONTENT_CANARIES, "RETRYREASONCANARY", "SUITECANARY", "PAYLOADCANARY"):
                assert canary not in text, (actor, canary)
            assert CANARY not in text and CANARY_SLUG not in text, actor
        for actor in DENIED:
            denied = await world.get(f"/v1/runs/{run_id}/findings", actor)
            assert denied.status_code == 403 and denied.json()["error"]["code"] == (
                "PERMISSION_DENIED"
            )

    # a globex run read from acme is the same 404 as an id that exists nowhere; acme's list never
    # carries globex findings, and the badge on acme's own list matches its findings
    random = await world.get("/v1/runs/run_01M4QNE6M04CKP6JH7TAZ62Q99/findings", "owner")
    foreign = await world.get(f"/v1/runs/{globex_run}/findings", "owner")
    assert foreign.status_code == random.status_code == 404
    assert {**foreign.json()["error"], "request_id": None} == {
        **random.json()["error"],
        "request_id": None,
    }
    listed = await world.get("/v1/runs", "viewer", limit=100)
    assert CANARY_SLUG not in listed.text
    badges = {r["id"]: r["findings"] for r in listed.json()["items"]}
    assert all(badges[r]["count"] > 0 and badges[r]["state"] == "current" for r in acme_runs)
    viewer_list = await world.get("/v1/runs", "owner", limit=100)
    assert listed.json()["items"] == viewer_list.json()["items"]

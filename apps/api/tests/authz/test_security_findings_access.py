"""Security findings end to end (Phase 13 gates 1, 5, 6): a planted run goes in through the real
ingestion path, the worker derives findings, and every role reads the same fixed, content-free
rows. Viewers (and every other role) learn no path, command or host from findings, facets or
search; billing, which cannot read runs, cannot read findings; another workspace sees nothing.
"""

import io
import json
from typing import Any

import pytest
from sqlalchemy import delete, func, select

from abb_api import cli
from abb_api.db import tables as t
from abb_api.findings import security_words as w
from tests.authz.world import CANARY, CANARY_SLUG, World, body_text
from tests.conftest import make_settings
from tests.security_helpers import AWS_KEY, PLANT, planted_run

PEOPLE = ("owner", "admin", "developer", "viewer", "security")
KEYS = ("reader", "wide_reader")
READERS = (*PEOPLE, *KEYS)
SEVERITY = {
    "sec.secret_file_read": "high",
    "sec.credential_in_output": "critical",
    "sec.destructive_command": "critical",
    "sec.risky_git": "high",
    "sec.privileged_db": "high",
    "sec.unexpected_destination": "medium",
}
WORLD_FINDINGS = 1  # acme's seeded content run reads a path ending in .ssh/id_rsa
LEAKS = (
    PLANT, PLANT.lower(), AWS_KEY, "AKIA", "-----BEGIN", "MIIEow", "exfil", "id_rsa", "/work",
    "DROP TABLE", "rm -rf", "--force", "psql", "make deploy",
)  # fmt: skip


async def put_settings(
    world: World, actor: str, body: dict[str, Any], project: str | None = None
) -> Any:
    project = project or world.acme.project_id
    return await world.api.client.put(
        f"/v1/projects/{project}/security-settings",
        headers={**world.actors[actor], "content-type": "application/json"},
        content=json.dumps(body).encode(),
    )


async def plant(world: World) -> tuple[str, dict[str, list[str]]]:
    """Allowlist one host, ingest the planted run into acme and a planted run into globex."""
    response = await put_settings(world, "owner", {"destination_allowlist": ["api.example.com"]})
    assert response.status_code == 200, response.text
    run, expected = planted_run("acme")
    assert (await world.api.post_batch(run.wires, token="writer")).status_code == 202
    # globex: the same shape with the globex canary everywhere, in a workspace with no allowlist
    other, _ = planted_run(CANARY_SLUG)
    assert (await world.api.post_batch(other.wires, token="other")).status_code == 202
    await world.api.drain()
    return run.run_id, expected


async def listing(world: World, actor: str, **params: str | int) -> Any:
    return await world.get("/v1/security/findings", actor, **params)


async def test_the_planted_run_yields_exactly_the_expected_findings_for_every_role(
    world: World,
) -> None:
    run_id, expected = await plant(world)
    bodies = {}
    for actor in READERS:
        response = await listing(world, actor, limit=100)
        assert response.status_code == 200, (actor, response.text)
        bodies[actor] = response.json()
    owner = bodies["owner"]
    # The world's own seeded run reads `.ssh/id_rsa` (its content canary), which is a real
    # secret-file read: one more finding, in another run.
    assert len(owner["items"]) == 6 + WORLD_FINDINGS
    planted = [i for i in owner["items"] if i["run_id"] == run_id]
    items = {i["detector"]: i for i in planted}
    assert set(items) == set(SEVERITY) and len(planted) == 6, sorted(items)
    for detector, item in items.items():
        assert item["severity"] == SEVERITY[detector]
        assert item["kind"] == "probable" and item["run_id"] == run_id
        assert item["evidence_event_ids"] == expected[detector], detector
        assert item["start_event_id"] == expected[detector][0]
        assert item["detector_version"] == f"{detector}@1.0.0"
        assert item["title"].startswith("Probable: ")
        assert item["count"] == len(expected[detector]) and item["rule"]
    info = {i.id: i for i in w.SECURITY_CATALOG}
    for detector, item in items.items():
        if detector != "sec.credential_in_output":
            assert item["message"] == info[detector].message
    assert "AWS-style access key" in items["sec.credential_in_output"]["message"]
    assert "private key" in items["sec.credential_in_output"]["message"]
    # identical for every role and key (metadata, gate 1)
    for actor, body in bodies.items():
        assert body == owner, actor
    # nothing of the content, from this workspace or the other, in any body
    for actor in READERS:
        text = body_text(await listing(world, actor, limit=100))
        for leak in (*LEAKS, CANARY, CANARY_SLUG):
            assert leak not in text and leak.lower() not in text.lower(), (actor, leak)
    # billing cannot read runs, so it cannot read their findings; a stranger sees only a 404
    denied = await listing(world, "billing")
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "PERMISSION_DENIED"
    assert (await listing(world, "no_membership")).status_code == 404


async def test_facets_filters_and_paging(world: World) -> None:
    run_id, _ = await plant(world)
    body = (await listing(world, "viewer", limit=100)).json()
    severity = {f["value"]: f["count"] for f in body["facets"]["severity"]}
    assert severity == {"critical": 2, "high": 3 + WORLD_FINDINGS, "medium": 1}
    assert {f["value"] for f in body["facets"]["detector"]} == set(SEVERITY)
    assert [(f["value"], f["label"], f["count"]) for f in body["facets"]["project"]] == [
        (world.acme.project_id, "alpha", 6 + WORLD_FINDINGS)
    ]
    critical = (await listing(world, "viewer", severity="critical")).json()
    assert {i["detector"] for i in critical["items"]} == {
        "sec.credential_in_output",
        "sec.destructive_command",
    }
    # a facet counts under the other filters but not its own: choosing a value keeps its siblings
    assert {f["value"] for f in critical["facets"]["severity"]} == {"critical", "high", "medium"}
    assert {f["value"] for f in critical["facets"]["detector"]} == {
        "sec.credential_in_output",
        "sec.destructive_command",
    }
    one = (await listing(world, "viewer", detector="sec.risky_git")).json()
    assert [i["detector"] for i in one["items"]] == ["sec.risky_git"]
    none = (await listing(world, "viewer", detector="sec.nope")).json()
    assert none["items"] == []
    by_project = await listing(world, "viewer", project_id=world.acme.project_id)
    assert len(by_project.json()["items"]) == 6 + WORLD_FINDINGS
    by_status = await listing(world, "viewer", run_status="FAILED")
    assert by_status.json()["items"] == []
    future = await listing(world, "viewer", since="2099-01-01T00:00:00Z")
    assert future.json()["items"] == []
    wide = await listing(
        world, "viewer", since="2000-01-01T00:00:00Z", until="2099-01-01T00:00:00Z"
    )
    assert len(wide.json()["items"]) == 6 + WORLD_FINDINGS
    naive = await listing(world, "viewer", since="2026-01-01T00:00:00")
    assert naive.status_code == 422
    # paging: no row twice, no row lost, newest run first
    seen: list[str] = []
    cursor = None
    for _ in range(10):
        extra: dict[str, str] = {"cursor": cursor} if cursor else {}
        page = (await listing(world, "viewer", limit=2, **extra)).json()
        seen.extend(i["id"] for i in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == 6 + WORLD_FINDINGS
    assert (await listing(world, "viewer", cursor="garbage")).status_code == 400
    # a project that is not ours is the same 404 as one that does not exist
    foreign = await listing(world, "owner", project_id=world.globex.project_id)
    assert foreign.status_code == 404
    # a key bound to another project sees none of acme's alpha findings
    assert (await listing(world, "beta")).json()["items"] == []
    assert run_id


async def test_the_run_pages_carry_the_security_group_and_a_separate_chip_count(
    world: World,
) -> None:
    run_id, expected = await plant(world)
    bodies = {}
    for actor in READERS:
        response = await world.get(f"/v1/runs/{run_id}/findings", actor)
        assert response.status_code == 200
        bodies[actor] = response.json()
    owner = bodies["owner"]
    security = [i for i in owner["items"] if i["category"] == "security"]
    assert {i["detector"] for i in security} == set(SEVERITY)
    assert all(i["evidence"]["rule"] for i in security)
    reliability = [i for i in owner["items"] if i["category"] == "reliability"]
    for actor, body in bodies.items():
        assert body["items"] == owner["items"], actor
        assert body["health"] == owner["health"], actor
        for leak in LEAKS:
            assert leak.lower() not in json.dumps(body).lower(), (actor, leak)
    run = (await world.get(f"/v1/runs/{run_id}", "viewer")).json()
    assert run["security_findings"] == {"count": 6, "max_severity": "critical", "state": "current"}
    # the reliability badge counts reliability findings only: the security ones are counted apart
    assert run["findings"]["count"] == len(reliability)
    listed = (await world.get("/v1/runs", "viewer", limit=100)).json()["items"]
    by_id = {r["id"]: r for r in listed}
    assert by_id[run_id]["security_findings"]["count"] == 6
    assert by_id[world.acme.run_id]["security_findings"]["count"] == WORLD_FINDINGS
    quiet = [r for r in listed if r["id"] not in (run_id, world.acme.run_id)]
    assert quiet and all(r["security_findings"]["count"] == 0 for r in quiet)
    assert expected


async def test_viewers_cannot_learn_paths_or_hosts_from_search_or_facets(world: World) -> None:
    run_id, _ = await plant(world)
    probes = (
        PLANT,
        f"{PLANT.lower()}-acme",
        "exfil",
        "id_rsa",
        ".env",
        "finding:sec.secret_file_read",
        "finding:destructive",
        "tool:psql",
    )
    for actor in ("viewer", "owner"):
        for q in probes:
            response = await world.get("/v1/runs", actor, q=q, limit=100)
            text = body_text(response)
            for leak in (AWS_KEY, "exfil.example", "/work/", "-----BEGIN"):
                assert leak not in text.replace(q, ""), (actor, q, leak)
            if actor == "viewer" and response.status_code == 200:
                assert run_id not in text or q in ("tool:psql",), (actor, q)
    # the run listing's facets name tools, models and agents, never findings or content
    facets = (await world.get("/v1/runs", "viewer", q="tool:pytest", limit=100)).json()
    assert "sec." not in json.dumps(facets) and "risky_git" not in json.dumps(facets)
    # `finding:` is reserved and answers the same for both roles
    a = await world.get("/v1/runs", "viewer", q="finding:risky_git")
    b = await world.get("/v1/runs", "owner", q="finding:risky_git")
    assert a.status_code == b.status_code
    assert a.json().get("error", {}).get("code") == b.json().get("error", {}).get("code")


async def test_workspaces_do_not_see_each_others_findings(world: World) -> None:
    await plant(world)
    mine = (await listing(world, "owner", limit=100)).json()
    theirs = (await listing(world, "other", limit=100)).json()
    assert len(mine["items"]) == 6 + WORLD_FINDINGS
    assert len(theirs["items"]) == 5  # globex has no allowlist and no seeded secret read
    assert {i["id"] for i in mine["items"]}.isdisjoint({i["id"] for i in theirs["items"]})
    assert {i["run_id"] for i in mine["items"]}.isdisjoint({i["run_id"] for i in theirs["items"]})
    assert world.globex.run_id not in body_text(await listing(world, "owner", limit=100))
    assert all(f["label"] != "p" for f in mine["facets"]["project"])


async def test_the_detector_catalogue_reflects_mutes_and_the_allowlist(world: World) -> None:
    anything = (await world.get("/v1/security/detectors", "viewer")).json()["items"]
    assert [d["id"] for d in anything] == [i.id for i in w.SECURITY_CATALOG]
    assert all(d["muted"] is False for d in anything)
    assert {d["id"]: d["active"] for d in anything}["sec.unexpected_destination"] is None
    assert all(d["version"] == "1.0.0" and d["severities"] for d in anything)
    await put_settings(world, "owner", {"muted_detectors": ["sec.risky_git"]})
    scoped = (
        await world.get("/v1/security/detectors", "viewer", project_id=world.acme.project_id)
    ).json()["items"]
    state = {d["id"]: (d["muted"], d["active"]) for d in scoped}
    assert state["sec.risky_git"] == (True, False)
    assert state["sec.unexpected_destination"] == (False, False)  # no allowlist yet
    assert state["sec.secret_file_read"] == (False, True)
    await put_settings(world, "owner", {"destination_allowlist": ["a.example.com"]})
    again = (
        await world.get("/v1/security/detectors", "viewer", project_id=world.acme.project_id)
    ).json()["items"]
    assert {d["id"]: d["active"] for d in again}["sec.unexpected_destination"] is True
    assert (await world.get("/v1/security/detectors", "billing")).status_code == 403
    nope = await world.get("/v1/security/detectors", "viewer", project_id=world.globex.project_id)
    assert nope.status_code == 404


async def test_settings_are_for_owners_and_admins_and_every_change_is_audited(world: World) -> None:
    path = f"/v1/projects/{world.acme.project_id}/security-settings"
    for actor in ("developer", "viewer", "security", "billing"):
        assert (await world.get(path, actor)).status_code == 403, actor
        assert (await put_settings(world, actor, {"capture_mode": "FULL"})).status_code == 403
    for actor in ("owner", "admin"):
        assert (await world.get(path, actor)).status_code == 200
    first = (await world.get(path, "owner")).json()
    assert first["capture_mode"] == "FULL" and first["muted_detectors"] == []
    assert first["updated_at"] is not None  # the fixture stored FULL
    changed = await put_settings(
        world, "admin",
        {"capture_mode": "METADATA_ONLY", "muted_detectors": ["sec.risky_git", "sec.privileged_db"],
         "destination_allowlist": ["API.Example.com", "*.cdn.example.net"]},
    )  # fmt: skip
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["capture_mode"] == "METADATA_ONLY"
    assert body["muted_detectors"] == ["sec.privileged_db", "sec.risky_git"]
    assert body["destination_allowlist"] == ["*.cdn.example.net", "api.example.com"]
    assert {d["id"] for d in body["detectors"] if d["muted"]} == set(body["muted_detectors"])
    # a request that changes nothing writes nothing
    before = await _audit_rows(world)
    assert (
        await put_settings(world, "owner", {"muted_detectors": body["muted_detectors"]})
    ).status_code == 200
    assert await _audit_rows(world) == before
    audit = (await world.get("/v1/audit", "owner", limit=50)).json()["items"]
    rows = [r for r in audit if r["action"] == "security.settings.update"]
    assert rows and rows[0]["resource_id"] == world.acme.project_id
    assert rows[0]["details"]["muted_added"] == ["sec.privileged_db", "sec.risky_git"]
    assert rows[0]["details"]["capture_mode_to"] == "METADATA_ONLY"
    assert rows[0]["actor_id"].startswith("user:")


async def _audit_rows(world: World) -> int:
    async with world.api.engine.connect() as conn:
        return int(
            await conn.scalar(
                select(func.count())
                .select_from(t.audit_log)
                .where(t.audit_log.c.action == "security.settings.update")
            )
            or 0
        )


@pytest.mark.parametrize(
    "body",
    [
        {"destination_allowlist": ["http://evil.example.com/path"]},
        {"destination_allowlist": ["ev il.com"]},
        {"destination_allowlist": ["*"]},
        {"destination_allowlist": ["a" * 300 + ".com"]},
        {"muted_detectors": ["sec.not_a_detector"]},
        {"muted_detectors": ["risky_git"]},
        {"capture_mode": "EVERYTHING"},
        {"muted_detectors": "sec.risky_git"},
    ],
)
async def test_invalid_settings_are_422_and_the_message_never_repeats_the_value(
    world: World, body: dict[str, Any]
) -> None:
    response = await put_settings(world, "owner", body)
    assert response.status_code == 422, response.text
    text = response.text
    for value in ("evil.example.com", "ev il.com", "not_a_detector", "EVERYTHING", "a" * 50):
        assert value not in text, value
    assert response.json()["error"]["code"] in ("SETTINGS_INVALID", "REQUEST_INVALID")
    assert (
        await world.get(f"/v1/projects/{world.acme.project_id}/security-settings", "owner")
    ).json()["destination_allowlist"] == []


async def test_settings_of_another_workspaces_project_are_a_404_like_an_unknown_one(
    world: World,
) -> None:
    unknown = "prj_01M4QNE6M04CKP6JH7TAZ62Q99"
    for project in (world.globex.project_id, unknown, "not-an-id"):
        got = await world.get(f"/v1/projects/{project}/security-settings", "owner")
        put = await put_settings(world, "owner", {"capture_mode": "FULL"}, project)
        assert got.status_code == put.status_code == 404, project
        assert world.globex.project_id not in got.text.replace(project, "")


async def test_a_mute_and_an_allowlist_change_apply_on_rebuild_and_a_rebuild_reproduces(
    world: World, database_url: str
) -> None:
    run_id, _ = await plant(world)
    base = (await listing(world, "owner", limit=100)).json()["items"]
    assert len(base) == 6 + WORLD_FINDINGS
    settings = make_settings(database_url)

    async def rebuild() -> None:
        code = await cli.run(
            ["rebuild-findings", "--workspace", "acme"],
            settings,
            out=io.StringIO(),
            err=io.StringIO(),
        )
        assert code == 0
        await world.api.drain()

    # rebuild over intact rows: the same findings with the same ids
    await rebuild()
    again = (await listing(world, "owner", limit=100)).json()["items"]
    assert [
        (i["id"], i["fingerprint"] if "fingerprint" in i else i["id"], i["evidence_event_ids"])
        for i in again
    ] == [
        (i["id"], i["fingerprint"] if "fingerprint" in i else i["id"], i["evidence_event_ids"])
        for i in base
    ]
    # a mute does nothing until the run is analysed again, then removes that detector's finding
    assert (
        await put_settings(world, "owner", {"muted_detectors": ["sec.risky_git"]})
    ).status_code == 200
    assert len((await listing(world, "owner", limit=100)).json()["items"]) == 6 + WORLD_FINDINGS
    await rebuild()
    now = (await listing(world, "owner", limit=100)).json()["items"]
    assert {i["detector"] for i in now} == set(SEVERITY) - {"sec.risky_git"}
    assert len(now) == 5 + WORLD_FINDINGS
    # an extra allowlist entry removes the destination finding on the next rebuild
    assert (
        await put_settings(
            world,
            "owner",
            {
                "destination_allowlist": [
                    "api.example.com",
                    f"{PLANT.lower()}-acme.exfil.example.net",
                    f"{PLANT.lower()}-acme.exfil.example.org",
                ]
            },
        )
    ).status_code == 200
    await rebuild()
    now = (await listing(world, "owner", limit=100)).json()["items"]
    assert "sec.unexpected_destination" not in {i["detector"] for i in now}
    # unmuting brings the finding back with its original id; and a rebuild after the rows were
    # lost re-derives the stored set exactly
    assert (await put_settings(world, "owner", {"muted_detectors": []})).status_code == 200
    await rebuild()
    restored = (await listing(world, "owner", limit=100)).json()["items"]
    risky_before = next(i for i in base if i["detector"] == "sec.risky_git")
    risky_after = next(i for i in restored if i["detector"] == "sec.risky_git")
    assert risky_before["id"] == risky_after["id"]
    async with world.api.engine.begin() as conn:
        await conn.execute(delete(t.findings))
        await conn.execute(delete(t.run_findings))
    assert (await listing(world, "owner", limit=100)).json()["items"] == []
    await rebuild()
    rebuilt = (await listing(world, "owner", limit=100)).json()["items"]
    assert [(i["id"], i["detector"], i["evidence_event_ids"]) for i in rebuilt] == [
        (i["id"], i["detector"], i["evidence_event_ids"]) for i in restored
    ]
    assert run_id

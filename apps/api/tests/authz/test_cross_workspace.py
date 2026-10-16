"""Another workspace's data never reaches a caller, by content, by number, or by id (ADR-002).

Globex is seeded with a canary string in every free-text field and with distinctive magnitudes
(7 runs costing 777.777 each). A leak shows up as the canary, as a globex id, or as a total that is
not the acme-only expectation. Foreign ids and random ids must be indistinguishable: same status,
same body (but for the request id), on success paths and error paths alike.
"""

import json
import re
from collections.abc import Callable

import pytest
from abb_event_schema.ids import IdKind, new_id

from abb_api.auth.keys import generate_key
from tests.authz.registry import CASES, RouteCase
from tests.authz.world import (
    ACME_RUN_COST,
    ACME_RUNS,
    CANARY,
    GLOBEX_RUN_COST,
    World,
    body_text,
)

# A well-formed id that exists nowhere, per id parameter.
RANDOM_IDS: dict[str, Callable[[], str]] = {
    "run_id": lambda: new_id(IdKind.RUN),
    "event_id": lambda: new_id(IdKind.EVENT),
    "artifact_id": lambda: new_id(IdKind.ARTIFACT),
    "project_id": lambda: new_id(IdKind.PROJECT),
    "user_id": lambda: new_id(IdKind.USER),
    "invitation_id": lambda: new_id(IdKind.INVITATION),
    "key_id": lambda: generate_key().key_id,
    "evaluator_id": lambda: new_id(IdKind.EVALUATOR),
    "evaluation_id": lambda: new_id(IdKind.EVALUATION),
    "experiment_id": lambda: new_id(IdKind.EXPERIMENT),
    "variant_id": lambda: new_id(IdKind.VARIANT),
    "with_run_id": lambda: new_id(IdKind.RUN),
    "human_evaluator_id": lambda: new_id(IdKind.EVALUATOR),
    "approval_id": lambda: new_id(IdKind.APPROVAL),
    "destination_id": lambda: new_id(IdKind.DESTINATION),
    "rule_id": lambda: new_id(IdKind.ALERT_RULE),
    "workspace_id": lambda: new_id(IdKind.WORKSPACE),
}
# Every credential kind of the acme workspace (the globex key is the attacker's mirror image).
ACME_ACTORS = (
    "writer", "reader", "wide", "wide_reader", "ingest_only", "beta", "art_alpha", "art_wide",
    "owner", "developer", "viewer", "billing", "dual",
)  # fmt: skip
# 777.777 per run and 5444.439 in total; random ids can contain "777", so match the number.
MAGNITUDES = re.compile(r"777\.777|5444\.43|777\.78")
REQUEST_ID = re.compile(r"req_[0-9a-f]{32}")


def assert_clean(world: World, text: str, where: str) -> None:
    assert CANARY.lower() not in text.lower(), f"canary leaked: {where}"
    leaked = [i for i in world.globex.every_id if i in text]
    assert not leaked, f"globex ids leaked ({leaked}): {where}"
    assert not MAGNITUDES.search(text), f"globex magnitude leaked: {where}"


def anonymised(text: str) -> str:
    return REQUEST_ID.sub("req_", text)


@pytest.mark.parametrize("actor", ACME_ACTORS)
async def test_no_response_to_an_acme_caller_contains_globex_data(world: World, actor: str) -> None:
    for route, case in CASES.items():
        response = await world.send(case, case.build(world.acme, {}), actor)
        text = "" if case.transport == "socket" else body_text(response)
        assert_clean(world, text, f"{actor} {route}")
    # The listing and the stream are the places a leak would show up as extra rows.
    if actor == "wide_reader":
        for path in ("/v1/runs", "/v1/pricing", "/v1/analytics/cost"):
            assert_clean(world, body_text(await world.get(path, actor)), path)
        assert_clean(world, await world.read_stream(world.acme.run_id, actor), "stream frames")


@pytest.mark.parametrize(
    "case", [c for c in CASES.values() if c.id_params], ids=lambda c: c.template
)
async def test_foreign_ids_and_random_ids_are_indistinguishable(
    world: World, case: RouteCase
) -> None:
    for token in case.probe_actors or ("reader", "wide_reader", "owner", "dual", "other"):
        # Each key replays its own workspace's valid request with the other workspace's ids.
        own, foreign_side = (
            (world.globex, world.acme) if token == "other" else (world.acme, world.globex)
        )
        for param in case.id_params:
            foreign = getattr(foreign_side, param)
            for _ in range(2):  # two random ids, to see that nothing depends on the random value
                random_id = RANDOM_IDS[param]()
                a = await world.send(case, case.build(own, {param: foreign}), token)
                b = await world.send(case, case.build(own, {param: random_id}), token)
                where = f"{token} {case.template} {param}"
                assert a.status_code == b.status_code == 404, where
                assert anonymised(a.text) == anonymised(b.text), where
                leaked = [i for i in foreign_side.every_id if i in a.text]
                assert not leaked, f"{where}: {leaked}"


async def test_aggregates_equal_the_acme_only_expectation(world: World) -> None:
    summary = (await world.get("/v1/analytics/summary", "wide_reader")).json()
    assert summary["runs"]["total"] == ACME_RUNS
    assert summary["cost"]["total_usd"] == pytest.approx(ACME_RUNS * ACME_RUN_COST)
    assert summary["cost"]["total_usd"] != pytest.approx(
        (ACME_RUNS * ACME_RUN_COST) + 7 * GLOBEX_RUN_COST
    )
    cost = (await world.get("/v1/analytics/cost", "wide_reader")).json()
    text = body_text(await world.get("/v1/analytics/cost", "wide_reader"))
    assert cost and not MAGNITUDES.search(text)
    runs = (await world.get("/v1/runs", "wide_reader", limit=100)).json()["items"]
    assert len(runs) == ACME_RUNS
    assert not {r["id"] for r in runs} & world.globex.every_id
    # The other side is intact and sees only its own seven runs.
    theirs = (await world.get("/v1/runs", "other", limit=100)).json()["items"]
    assert len(theirs) == 7
    assert not {r["id"] for r in theirs} & world.acme.every_id


async def test_me_of_a_member_of_both_workspaces_carries_no_project_data(world: World) -> None:
    """`/v1/me` is the one cross-workspace read: memberships only, never anything inside them."""
    response = await world.api.client.get("/v1/me", headers=world.actors["dual"])
    assert response.status_code == 200
    body = response.json()
    assert {m["workspace"]["slug"] for m in body["memberships"]} == {"acme", "globex"}
    assert CANARY.lower() not in response.text.lower()  # workspace names are not canary-marked
    assert not [
        i for i in world.globex.every_id - {world.globex.workspace_id} if i in response.text
    ]


async def test_a_dual_member_sees_only_the_workspace_they_name(world: World) -> None:
    case = CASES[("GET", "/v1/runs")]
    acme = await world.send(case, case.build(world.acme, {}), "dual")
    assert world.acme.run_id in acme.text and world.globex.run_id not in acme.text
    globex_headers = {**world.actors["dual"], "x-abb-workspace": world.globex.workspace_id}
    globex = await world.api.client.get("/v1/runs", headers=globex_headers, params={"limit": "100"})
    assert world.globex.run_id in globex.text and world.acme.run_id not in globex.text


# What a search by an acme actor must never turn up: globex's canary in every searchable shape.
SEARCH_PROBES = (
    CANARY,
    "tool:canary-globex-7f3e-tool",
    "tool:canary-globex*",
    "model:canary-globex-7f3e-model",
    "model:canary-globex*",
    "agent:canary-globex-7f3e",
    "agent:canary*",
    "root_agent:canary-globex-7f3e",
    "name:CANARY",
    "-tool:canary-globex-7f3e-tool",
    "canary-globex-7f3e-tool",
    "cost:>=777",
)
SEARCH_ACTORS = tuple(a for a in ACME_ACTORS if a not in ("art_alpha", "art_wide"))


def without_echo(response_text: str) -> str:
    """The body minus `query.terms`: the caller's own typed text is allowed to come back."""
    body = json.loads(response_text)
    if isinstance(body.get("query"), dict):
        body["query"].pop("terms", None)
    return json.dumps(body)


@pytest.mark.parametrize("actor", SEARCH_ACTORS)
async def test_a_search_by_an_acme_caller_never_finds_globex(world: World, actor: str) -> None:
    for q in SEARCH_PROBES:
        response = await world.get("/v1/runs", actor, q=q, limit=100)
        if response.status_code == 403:  # billing, a key without the read scope
            assert_clean(world, without_echo(response.text), f"{actor} {q}")
            continue
        assert response.status_code == 200, (actor, q, response.text)
        body = response.json()
        text = without_echo(response.text)
        assert_clean(world, text, f"{actor} {q}")
        if not q.startswith("-") and q != "cost:>=777":
            assert body["items"] == [], (actor, q)
        assert not {i["id"] for i in body["items"]} & world.globex.every_id
        for values in (body["facets"] or {}).values():
            assert all(CANARY.lower() not in v["value"].lower() for v in values), (actor, q)


async def test_the_probes_are_potent_the_globex_owner_does_find_its_own_canary(
    world: World,
) -> None:
    """Guards the test above: the same probes do find globex's runs from inside globex."""
    for q in ("tool:canary-globex-7f3e-tool", "model:canary-globex*", "agent:canary-globex-7f3e"):
        found = await world.get("/v1/runs", "other", q=q, limit=100)
        assert len(found.json()["items"]) == 7, q
        assert not {i["id"] for i in found.json()["items"]} & world.acme.every_id


async def test_a_dual_member_searches_only_the_workspace_they_name(world: World) -> None:
    acme_run_ids = {world.acme.run_id}
    mine = await world.get("/v1/runs", "dual", q="tool:canary-globex-7f3e-tool")
    assert mine.json()["items"] == []
    in_globex = await world.api.client.get(
        "/v1/runs",
        params={"q": "tool:canary-globex-7f3e-tool", "limit": "100"},
        headers={**world.actors["dual"], "x-abb-workspace": world.globex.workspace_id},
    )
    assert len(in_globex.json()["items"]) == 7
    assert not {i["id"] for i in in_globex.json()["items"]} & world.acme.every_id
    ours = await world.get("/v1/runs", "dual", q="tool:acme-agent-tool", limit=100)
    assert len(ours.json()["items"]) == 3 and acme_run_ids <= {
        i["id"] for i in ours.json()["items"]
    }


async def test_a_cursor_from_one_workspace_reveals_nothing_in_the_other(world: World) -> None:
    first = await world.get("/v1/runs", "dual", q="tool:acme-agent-tool", limit=1)
    cursor = first.json()["next_cursor"]
    assert cursor
    replay = await world.api.client.get(
        "/v1/runs",
        params={"q": "tool:acme-agent-tool", "limit": "100", "cursor": cursor},
        headers={**world.actors["dual"], "x-abb-workspace": world.globex.workspace_id},
    )
    assert replay.status_code in (200, 400)
    assert not [i for i in world.acme.every_id if i in without_echo(replay.text)]
    assert replay.status_code == 400 or replay.json()["items"] == []  # globex has no such tool
    # And the plain replay under a stranger's search of globex's own data stays inside globex.
    own = await world.api.client.get(
        "/v1/runs",
        params={"q": "tool:canary-globex-7f3e-tool", "limit": "100", "cursor": cursor},
        headers={**world.actors["dual"], "x-abb-workspace": world.globex.workspace_id},
    )
    assert not [i for i in world.acme.every_id if i in without_echo(own.text)]

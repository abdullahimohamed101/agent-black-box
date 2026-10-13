"""Command text and file paths are captured content (ADR-061, review F3).

An actor without `payload.read` must not receive them on any route, in any shape: event lists and
detail, span names, stream frames, derived summaries, analytics and audit. Actors with it see
exactly what was ingested. The world seeds acme's first run with a shell span, a file edit and an
event under an unregistered attribute, each carrying a canary.
"""

import json

import pytest
from abb_event_schema.registry import CONTENT_ATTRIBUTES, KNOWN_ATTRIBUTES, AttrType

from tests.authz.registry import CASES
from tests.authz.world import (
    COMMAND_CANARY,
    CONTENT_CANARIES,
    CUSTOM_CANARY,
    PATH_CANARY,
    World,
    body_text,
)
from tests.ingest_helpers import make_run_ids, wire_event

WITHHELD_FROM = ("viewer", "downgraded")  # a VIEWER, and an OWNER demoted to VIEWER
SEES_CONTENT = ("owner", "admin", "developer", "security", "reader", "wide_reader")

# String attributes reviewed as metadata: kinds, labels, ids and enumerations, not what the agent
# ran or touched. A new string attribute is in neither set until someone decides (fail closed).
REVIEWED_METADATA = frozenset(
    {
        "run.name", "agent.state", "agent.child_id", "span.name", "span.kind", "llm.provider",
        "llm.model", "llm.error_type", "llm.service_tier", "llm.data_residency",
        "cost.pricing_version", "tool.name", "tool.operation",
        "tool.error_type", "file.hash_before", "file.hash_after", "file.language",
        "file.operation", "diff.artifact", "diff.withheld", "git.base_commit", "git.head_commit",
        "git.commit_hash", "shell.risk_class", "shell.category", "shell.stdout_artifact",
        "shell.stderr_artifact", "shell.output_withheld", "test.framework", "test.suite",
        "db.system", "db.operation", "http.method", "retry.reason", "retry.of_event_id",
        "timeout.operation", "loop.pattern", "policy.id", "policy.capability", "policy.reason",
        "secret.kind", "approval.id", "approval.capability",
        # Phase 14 (ADR-140): labels the policy service writes. `policy.decision` and
        # `approval.state` are closed vocabularies; `policy.reason` is built by the server only
        # from the matching rule's own description or id (tests/policies/test_evaluator.py plants
        # a sentinel in every intent field and checks it never reaches a reason), so it holds
        # the policy author's words and no captured content.
        "policy.decision", "approval.state",
        # Written by the server at ingestion (ADR-130): kind names and a reason, never a value.
        "redaction.server_kinds", "redaction.dropped_reason",
    }
)  # fmt: skip


def test_every_text_attribute_is_classified_as_content_or_reviewed_metadata() -> None:
    texty = {
        key
        for key, spec in KNOWN_ATTRIBUTES.items()
        if spec.type in (AttrType.STRING, AttrType.STRING_LIST)
    }
    unclassified = texty - CONTENT_ATTRIBUTES - REVIEWED_METADATA
    assert not unclassified, (
        f"classify {sorted(unclassified)}: add to CONTENT_ATTRIBUTES (withheld without "
        "payload.read) or to REVIEWED_METADATA here"
    )
    assert not CONTENT_ATTRIBUTES & REVIEWED_METADATA
    assert CONTENT_ATTRIBUTES <= set(KNOWN_ATTRIBUTES)
    assert REVIEWED_METADATA <= set(KNOWN_ATTRIBUTES)


# The run summary's first-error facts (ADR-112) are not event attributes, so they are not in
# REVIEWED_METADATA (which classifies attribute keys). They are metadata by construction: an event
# id, a sequence number, two counts, seconds and a USD figure, derived from event types, statuses,
# timestamps and cost lines, never from an attribute value, a payload or a name.
FIRST_ERROR_KEYS = (
    "first_error_event_id", "first_error_sequence", "retries_after", "seconds_after",
    "cost_after_usd", "cost_after_unpriced_calls",
)  # fmt: skip


@pytest.mark.parametrize("actor", ["viewer", "downgraded", *SEES_CONTENT])
async def test_first_error_facts_are_metadata_and_identical_for_every_role(
    world: World, actor: str
) -> None:
    ids = make_run_ids()
    token = "writer"  # acme's project write key
    llm = {"llm.provider": "example-provider", "llm.model": "m", "cost.estimated_usd": 0.25}
    events = [
        wire_event(ids, 1, event_type="run.started", agent_id="ab", span_id=..., attributes={}),
        wire_event(
            ids, 2, event_type="tool.call.failed", agent_id="ab", status="error",
            attributes={"tool.name": "t", "tool.error_type": "Boom"},
        ),
        wire_event(ids, 3, event_type="retry.attempted", agent_id="ab",
                   attributes={"retry.attempt": 1}),
        wire_event(ids, 4, event_type="llm.request.completed", agent_id="ab", status="success",
                   attributes=llm),
        wire_event(ids, 5, event_type="run.failed", agent_id="ab", span_id=..., attributes={}),
    ]  # fmt: skip
    assert (await world.api.post_batch(events, token=token)).status_code == 202
    await world.api.drain()
    run_id = ids["run_id"]

    detail = (await world.get(f"/v1/runs/{run_id}", actor)).json()
    reference = (await world.get(f"/v1/runs/{run_id}", "owner")).json()
    for body in (detail, reference):
        assert body["first_error"] == {
            "event_id": events[1]["event_id"],
            "sequence": 2,
            "retries_after": 1,
            "seconds_after": pytest.approx(3.0),
            "cost_after_usd": 0.25,
            "cost_after_unpriced_calls": 0,
        }
        assert {k: body["summary"][k] for k in FIRST_ERROR_KEYS}["retries_after"] == 1
    assert {k: detail["summary"][k] for k in FIRST_ERROR_KEYS} == {
        k: reference["summary"][k] for k in FIRST_ERROR_KEYS
    }
    listed = (await world.get("/v1/runs", actor, limit=100)).json()["items"]
    row = next(r for r in listed if r["id"] == run_id)
    assert row["first_error"] == detail["first_error"]
    assert {k: row["summary"][k] for k in FIRST_ERROR_KEYS} == {
        k: detail["summary"][k] for k in FIRST_ERROR_KEYS
    }


@pytest.mark.parametrize("actor", ["viewer", "downgraded", *SEES_CONTENT])
async def test_per_event_cost_is_metadata_and_identical_for_every_role(
    world: World, actor: str
) -> None:
    """`cost_usd` and `cost_unpriced` on events (ADR-121) come from the cost engine's stored lines:
    a USD figure and a flag, never an attribute value, a payload or a name. Every role gets them,
    on lists and on the detail, exactly as the owner does."""
    ids = make_run_ids()
    llm = {"llm.provider": "example-provider", "llm.model": "m", "cost.estimated_usd": 0.25}
    events = [
        wire_event(ids, 1, event_type="run.started", agent_id="ab", span_id=..., attributes={}),
        wire_event(ids, 2, event_type="llm.request.completed", agent_id="ab", status="success",
                   attributes=llm),
        wire_event(ids, 3, event_type="llm.request.completed", agent_id="ab", status="success",
                   attributes={"llm.provider": "x", "llm.model": "unknown", "llm.input_tokens": 3}),
        wire_event(ids, 4, event_type="run.completed", agent_id="ab", span_id=..., attributes={}),
    ]  # fmt: skip
    assert (await world.api.post_batch(events, token="writer")).status_code == 202
    await world.api.drain()
    run_id = ids["run_id"]

    def costs(body: dict) -> list[tuple[str, float | None, bool]]:  # type: ignore[type-arg]
        return [(e["event_id"], e["cost_usd"], e["cost_unpriced"]) for e in body["items"]]

    seen = (await world.get(f"/v1/runs/{run_id}/events", actor, limit=100)).json()
    reference = (await world.get(f"/v1/runs/{run_id}/events", "owner", limit=100)).json()
    assert costs(seen) == costs(reference)
    assert [c[1:] for c in costs(seen)] == [
        (None, False),
        (0.25, False),
        (0.0, True),
        (None, False),
    ]
    for event_id in (events[1]["event_id"], events[2]["event_id"]):
        one = (await world.get(f"/v1/runs/{run_id}/events/{event_id}", actor)).json()
        ref = (await world.get(f"/v1/runs/{run_id}/events/{event_id}", "owner")).json()
        assert (one["cost_usd"], one["cost_unpriced"]) == (ref["cost_usd"], ref["cost_unpriced"])


@pytest.mark.parametrize("actor", ["viewer", "downgraded", *SEES_CONTENT])
async def test_server_redaction_markers_are_metadata_and_identical_for_every_role(
    world: World, actor: str
) -> None:
    """`redaction.*` (ADR-130) say that the server redacted or dropped something, with kind names
    and a reason; they carry no content, so a viewer sees exactly what the owner sees."""
    ids = make_run_ids()
    secret = "AKIAIOSFODNN7EXAMPLE"  # the documented example key
    events = [
        wire_event(ids, 1, event_type="run.started", agent_id="ab", span_id=..., attributes={}),
        wire_event(
            ids, 2, event_type="shell.command.completed", agent_id="ab", status="success",
            attributes={"shell.command": f"echo {secret}", "shell.stdout_artifact": "artifact://x"},
            payload={"out": secret},
        ),
        wire_event(ids, 3, event_type="run.completed", agent_id="ab", span_id=..., attributes={}),
    ]  # fmt: skip
    assert (await world.api.post_batch(events, token="writer")).status_code == 202
    await world.api.drain()
    run_id, event_id = ids["run_id"], events[1]["event_id"]

    one = (await world.get(f"/v1/runs/{run_id}/events/{event_id}", actor)).json()
    reference = (await world.get(f"/v1/runs/{run_id}/events/{event_id}", "owner")).json()
    markers = {k: v for k, v in one["attributes"].items() if k.startswith("redaction.")}
    assert markers == {
        k: v for k, v in reference["attributes"].items() if k.startswith("redaction.")
    }
    assert markers["redaction.server_kinds"] == ["aws_access_key"]
    assert markers["redaction.server_count"] == 2
    assert secret not in json.dumps(one) and secret not in json.dumps(reference)
    listed = (await world.get(f"/v1/runs/{run_id}/events", actor, limit=100)).json()["items"]
    row = next(e for e in listed if e["event_id"] == event_id)
    assert row["attributes"].get("redaction.server_count") == 2


async def _seeded_event_ids(world: World) -> list[str]:
    listed = await world.get(f"/v1/runs/{world.acme.run_id}/events", "owner", limit=100)
    return [e["event_id"] for e in listed.json()["items"]]


@pytest.mark.parametrize("actor", WITHHELD_FROM)
async def test_no_route_returns_command_text_or_paths_to_an_actor_without_payload_read(
    world: World, actor: str
) -> None:
    for route, case in CASES.items():
        response = await world.send(case, case.build(world.acme, {}), actor)
        text = "" if case.transport == "socket" else body_text(response)
        for canary in CONTENT_CANARIES:
            assert canary not in text, f"{canary} leaked to {actor}: {route}"
    run = world.acme.run_id
    seen = [
        await world.get(f"/v1/runs/{run}", actor),
        await world.get("/v1/runs", actor, limit=100),
        await world.get(f"/v1/runs/{run}/events", actor, limit=100),
        await world.get(f"/v1/runs/{run}/spans", actor),
        await world.get("/v1/analytics/summary", actor),
        await world.get("/v1/audit", actor),
        await world.get(f"/v1/runs/{run}/evaluations", actor),
        await world.get("/v1/evaluators", actor),
        await world.get("/v1/experiments", actor),
        await world.get(f"/v1/experiments/{world.acme.experiment_id}", actor),
        await world.get(f"/v1/runs/{run}/compare", actor, with_run_id=world.acme.with_run_id),
        await world.get(f"/v1/runs/{world.acme.with_run_id}/compare", actor, with_run_id=run),
    ]
    for event_id in await _seeded_event_ids(world):
        seen.append(await world.get(f"/v1/runs/{run}/events/{event_id}", actor))
    frames = await world.read_stream(run, actor)
    assert "trace_event" in frames  # the stream did deliver; it just carried no content
    texts = [body_text(r) for r in seen] + [frames]
    for text in texts:
        for canary in CONTENT_CANARIES:
            assert canary not in text


@pytest.mark.parametrize("actor", WITHHELD_FROM)
async def test_evaluation_scores_stay_visible_but_judge_text_and_config_do_not(
    world: World, actor: str
) -> None:
    items = (await world.get(f"/v1/runs/{world.acme.run_id}/evaluations", actor)).json()["items"]
    (judge,) = [i for i in items if i["kind"] == "llm_judge"]
    assert (judge["score"], judge["label"], judge["ground_truth"]) == (0.5, "ok", False)
    assert judge["explanation"] is None and judge["explanation_withheld"] is True
    assert "raw" not in judge["metadata"] and judge["metadata_withheld"] == ["raw"]
    assert judge["metadata"]["judge_model"] == "fake-judge-1"


@pytest.mark.parametrize("actor", SEES_CONTENT)
async def test_actors_with_payload_read_see_judge_explanations(world: World, actor: str) -> None:
    items = (await world.get(f"/v1/runs/{world.acme.run_id}/evaluations", actor)).json()["items"]
    (judge,) = [i for i in items if i["kind"] == "llm_judge"]
    assert judge["explanation"].startswith("EXPLCANARY") and judge["explanation_withheld"] is False
    assert judge["metadata"]["raw"].startswith("EXPLCANARY")


@pytest.mark.parametrize("actor", WITHHELD_FROM)
async def test_a_comparison_aligns_on_metadata_and_counts_files_for_a_viewer(
    world: World, actor: str
) -> None:
    run = world.acme.run_id
    body = (
        await world.get(f"/v1/runs/{run}/compare", actor, with_run_id=world.acme.with_run_id)
    ).json()
    assert body["alignment"] == "metadata" and body["files"]["paths_withheld"] is True
    assert body["files"]["only_left"] == body["files"]["both"] == []
    shell = [r["left"] for r in body["steps"] if r["left"] and r["left"]["family"] == "shell"]
    assert shell and all(s["label"] is None and s["label_withheld"] for s in shell)


@pytest.mark.parametrize("actor", SEES_CONTENT)
async def test_a_comparison_shows_commands_and_paths_to_actors_with_payload_read(
    world: World, actor: str
) -> None:
    run = world.acme.run_id
    body = (
        await world.get(f"/v1/runs/{run}/compare", actor, with_run_id=world.acme.with_run_id)
    ).json()
    assert body["alignment"] == "content"
    text = json.dumps(body)
    assert COMMAND_CANARY in text and PATH_CANARY in text
    assert PATH_CANARY.lstrip("/") in body["files"]["only_left"]  # shown relative


@pytest.mark.parametrize("actor", WITHHELD_FROM)
async def test_a_viewer_still_sees_that_a_command_ran_and_how_it_went(
    world: World, actor: str
) -> None:
    run = world.acme.run_id
    events = (await world.get(f"/v1/runs/{run}/events", actor, limit=100)).json()["items"]
    shell = [e for e in events if e["event_type"].startswith("shell.command.")]
    assert len(shell) == 2
    done = next(e for e in shell if e["event_type"].endswith("completed"))
    assert done["attributes"]["shell.exit_code"] == 0 and done["duration_ms"] == 40
    assert done["attributes"]["shell.command"] != COMMAND_CANARY
    assert done["withheld_attributes"] == ["shell.command"]
    edit = next(e for e in events if e["event_type"] == "file.modified")
    assert edit["attributes"]["file.lines_added"] == 3
    assert edit["attributes"]["file.language"] == "python"
    assert edit["withheld_attributes"] == ["file.path"]
    custom = next(e for e in events if e["event_type"].startswith("custom."))
    assert custom["attributes"]["thing.count"] == 2
    assert custom["withheld_attributes"] == ["thing.command"]
    spans = (await world.get(f"/v1/runs/{run}/spans", actor)).json()["items"]
    shell_span = next(s for s in spans if s["kind"] == "shell")
    assert shell_span["name"] is None and shell_span["name_withheld"] is True
    assert shell_span["status"] == "success" and shell_span["duration_ms"] == 40
    tool_span = next(s for s in spans if s["kind"] == "tool")
    assert tool_span["name"] and tool_span["name_withheld"] is False  # only content is hidden


@pytest.mark.parametrize("actor", SEES_CONTENT)
async def test_actors_with_payload_read_see_the_command_and_the_path_unchanged(
    world: World, actor: str
) -> None:
    run = world.acme.run_id
    events = (await world.get(f"/v1/runs/{run}/events", actor, limit=100)).json()["items"]
    assert all(e["withheld_attributes"] == [] for e in events)
    text = json.dumps(events)
    assert COMMAND_CANARY in text and PATH_CANARY in text and CUSTOM_CANARY in text
    spans = (await world.get(f"/v1/runs/{run}/spans", actor)).json()["items"]
    assert COMMAND_CANARY in [s["name"] for s in spans]
    assert not any(s["name_withheld"] for s in spans)
    frames = await world.read_stream(run, actor)
    assert COMMAND_CANARY in frames and PATH_CANARY in frames


def _without_echo(text: str) -> str:
    """A search response minus the echo of the caller's own query text (`query.terms`)."""
    body = json.loads(text)
    if isinstance(body.get("query"), dict):
        body["query"].pop("terms", None)
    return json.dumps(body)


@pytest.mark.parametrize("actor", WITHHELD_FROM)
async def test_search_never_returns_content_or_finds_it_for_an_actor_without_payload_read(
    world: World, actor: str
) -> None:
    """`GET /v1/runs?q=` is a route like any other: the canaries in every syntactic form find
    nothing, and nothing in the response (items, facets, matched, flags) carries them."""
    forms = ("{w}", "{w}*", "-{w}", '"{w}"', "tool:acme-agent-tool {w}", "command:{w}", "file:{w}")
    for canary in CONTENT_CANARIES:
        for pattern in forms:
            response = await world.get("/v1/runs", actor, q=pattern.format(w=canary), limit=100)
            if pattern.startswith(("command:", "file:")):
                assert response.status_code == 403, pattern
            else:
                assert response.status_code == 200, pattern
                assert response.json()["items"] == [] or pattern.startswith("-"), pattern
            text = _without_echo(response.text)
            assert canary.lower() not in text.lower(), (canary, pattern)
    listing = await world.get("/v1/runs", actor, q="tool:acme-agent-tool", limit=100)
    assert len(listing.json()["items"]) == 3
    for canary in CONTENT_CANARIES:
        assert canary not in _without_echo(listing.text)


@pytest.mark.parametrize("actor", SEES_CONTENT[:-2])
async def test_search_finds_content_for_actors_that_may_read_it(world: World, actor: str) -> None:
    found = await world.get("/v1/runs", actor, q=f'"{PATH_CANARY}"')
    assert [i["id"] for i in found.json()["items"]] == [world.acme.run_id]

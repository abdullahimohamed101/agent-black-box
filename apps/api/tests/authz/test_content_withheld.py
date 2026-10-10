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

WITHHELD_FROM = ("viewer", "downgraded")  # a VIEWER, and an OWNER demoted to VIEWER
SEES_CONTENT = ("owner", "admin", "developer", "security", "reader", "wide_reader")

# String attributes reviewed as metadata: kinds, labels, ids and enumerations, not what the agent
# ran or touched. A new string attribute is in neither set until someone decides (fail closed).
REVIEWED_METADATA = frozenset(
    {
        "run.name", "agent.state", "agent.child_id", "span.name", "span.kind", "llm.provider",
        "llm.model", "llm.error_type", "cost.pricing_version", "tool.name", "tool.operation",
        "tool.error_type", "file.hash_before", "file.hash_after", "file.language",
        "file.operation", "diff.artifact", "diff.withheld", "git.base_commit", "git.head_commit",
        "git.commit_hash", "shell.risk_class", "shell.category", "shell.stdout_artifact",
        "shell.stderr_artifact", "shell.output_withheld", "test.framework", "test.suite",
        "db.system", "db.operation", "http.method", "retry.reason", "retry.of_event_id",
        "timeout.operation", "loop.pattern", "policy.id", "policy.capability", "policy.reason",
        "secret.kind", "approval.id", "approval.capability",
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

"""The search document builder (AC-8): pure, order-independent, bounded, split by permission."""

import random
import uuid
from typing import Any

from abb_event_schema.event import Event

from abb_api.runs.summary import derive_run
from abb_api.search.document import (
    MAX_ARRAY_BYTES,
    MAX_POSITIONS,
    MAX_VALUES,
    build_document,
    summary_agents,
)
from abb_api.search.tsliteral import parse_tsvector
from abb_api.tenancy import TenantContext
from tests.ingest_helpers import Tenant, build_event, make_run_ids

TENANT = Tenant(
    context=TenantContext(uuid.uuid4()),
    workspace_id="ws_01J9ZZZZZZZZZZZZZZZZZZZZZZ",
    projects={"p": "prj_01J9ZZZZZZZZZZZZZZZZZZZZZZ"},
    project_uuids={},
)
RUN = make_run_ids()
COMMAND = "CMDCANARY-curl -H 'Authorization: Bearer s3cr3t' https://example.test/x"
PATH = "/home/PATHCANARY/.ssh/id_rsa"


def ev(n: int, kind: str, attributes: dict[str, Any] | None = None, **kw: Any) -> Event:
    return build_event(
        TENANT, RUN, n=n, event_type=kind, attributes=attributes if attributes else {}, **kw
    )


def rich() -> list[Event]:
    return [
        ev(1, "run.started", {"run.name": "Fix the login"}),
        ev(2, "tool.call.completed", {"tool.name": "GitHub", "tool.operation": "create_issue"}),
        ev(
            3,
            "llm.request.completed",
            {"llm.provider": "Anthropic", "llm.model": "claude-3.5-sonnet", "llm.input_tokens": 3},
        ),
        ev(4, "shell.command.completed", {"shell.command": COMMAND, "shell.cwd": "/srv/CWDCANARY"}),
        ev(5, "file.modified", {"file.path": PATH}),
        ev(6, "db.query.completed", {"db.operation": "INSERT"}),
        ev(7, "policy.action.blocked", {"policy.id": "p1"}, tags=["Prod", "nightly"]),
        ev(
            8,
            "tool.call.failed",
            {"tool.name": "x", "custom.command": "CUSTOMCANARY-rm-rf", "mystery": "hidden"},
        ),
        ev(9, "run.completed"),
    ]


def build(events: list[Event], **kw: Any) -> Any:
    derived = derive_run(events)
    options: dict[str, Any] = {
        "run_name": derived.name,
        "run_metadata": {"ticket": "ABC-123", "nested": {"owner": "Dana"}, "n": 4},
        "agents": summary_agents(derived.summary),
        "error_count": derived.summary["error_count"],
    }
    options.update(kw)
    return build_document(events, **options)


def test_metadata_side_holds_names_never_content() -> None:
    doc = build(rich())
    assert doc.tools == ("github", "x") and doc.models == ("claude-3.5-sonnet",)
    assert doc.providers == ("anthropic",) and doc.agents == ("coding-agent",)
    assert doc.tags == ("prod", "nightly")
    assert "tool.call.completed" in doc.event_types and "shell.command.completed" in doc.event_types
    assert doc.security_events == 1 and doc.error_count == 1
    words = parse_tsvector(doc.metadata_doc)
    for expected in ("fix", "login", "github", "create_issue", "insert", "abc-123", "dana"):
        assert expected in words, expected
    assert "ticket" not in words  # keys are not indexed, only values
    flat = doc.metadata_doc.lower()
    for secret in (
        "cmdcanary",
        "pathcanary",
        "cwdcanary",
        "customcanary",
        "s3cr3t",
        "id_rsa",
        "hidden",
    ):
        assert secret not in flat, secret


def test_content_side_holds_commands_paths_and_unregistered_content_leaves() -> None:
    doc = build(rich())
    assert doc.commands == (COMMAND,) and doc.file_paths == (PATH,)
    words = parse_tsvector(doc.content_doc)
    for expected in (
        "cmdcanary-curl",
        "s3cr3t",
        "pathcanary",
        "id_rsa",
        "cwdcanary",
        "customcanary-rm-rf",
    ):
        assert any(expected in w for w in words), expected
    assert not doc.content_truncated and not doc.metadata_truncated


def test_the_span_name_attribute_is_indexed_but_the_derived_span_name_is_not() -> None:
    events = [
        ev(1, "run.started"),
        ev(2, "shell.command.started", {"shell.command": "CMDCANARY-x"}),
        ev(3, "tool.call.completed", {"tool.name": "t", "span.name": "Visible Step"}),
    ]
    doc = build(events)
    words = parse_tsvector(doc.metadata_doc)
    assert "visible" in words and "step" in words
    assert "cmdcanary-x" not in doc.metadata_doc.lower()


def test_unregistered_non_content_attributes_are_not_indexed() -> None:
    doc = build([ev(1, "run.started", {"vendor.note": "zebrafish"})])
    assert "zebrafish" not in doc.metadata_doc and "zebrafish" not in doc.content_doc


def test_the_document_does_not_depend_on_event_order_or_repetition() -> None:
    events = rich()
    expected = build(events)
    for seed in range(5):
        shuffled = events[:]
        random.Random(seed).shuffle(shuffled)
        assert build(shuffled) == expected
        assert build(shuffled).hash == expected.hash
    assert build(events + events[:3]).hash == expected.hash


def test_the_hash_changes_with_any_searchable_fact() -> None:
    base = build(rich())
    assert build(rich(), run_name="Another").hash != base.hash
    assert build(rich(), run_metadata={"ticket": "ABC-124"}).hash != base.hash
    assert build(rich(), error_count=5).hash != base.hash
    assert build([*rich(), ev(10, "tool.call.completed", {"tool.name": "slack"})]).hash != base.hash


def changed_fields(a: Any, b: Any) -> set[str]:
    return {name for name in a.__dataclass_fields__ if getattr(a, name) != getattr(b, name)}


def test_a_content_only_change_changes_the_hash() -> None:
    """Review F1: the sensitive half of the index must be rewritten when only content changes.

    Each case adds an event whose type, tool and vocabulary are already in the base document, so
    nothing on the metadata side moves; only the hash can tell the stored row is stale.
    """
    base = build(rich())
    extra_command = build(
        [*rich(), ev(10, "shell.command.completed", {"shell.command": "curl -H x https://y.test"})]
    )
    extra_path = build([*rich(), ev(10, "file.modified", {"file.path": "/srv/other/key.pem"})])
    extra_text = build(
        [*rich(), ev(10, "tool.call.failed", {"tool.name": "x", "custom.command": "OTHERCANARY"})]
    )
    full = [
        ev(i + 100, "shell.command.completed", {"shell.command": f"c{i}"})
        for i in range(MAX_VALUES)
    ]
    at_the_bound = build(full)
    over_the_bound = build(
        [*full, ev(5_000, "shell.command.completed", {"shell.command": "one-more"})]
    )
    cases = {
        "commands": extra_command,
        "file_paths": extra_path,
        "content_doc": extra_text,
    }
    for field, doc in cases.items():
        changed = changed_fields(base, doc)
        assert field in changed, field
        assert not changed & {"metadata_doc", "metadata_truncated", "tools", "event_types"}, field
        assert doc.hash != base.hash, field
    # the flag alone: the dropped value leaves every other field as it was
    assert changed_fields(at_the_bound, over_the_bound) == {"content_truncated"}
    assert over_the_bound.hash != at_the_bound.hash


def test_arrays_are_bounded_and_set_only_the_metadata_flag() -> None:
    events = [ev(1, "run.started")] + [
        ev(i + 2, "tool.call.completed", {"tool.name": f"tool-{i}"}) for i in range(MAX_VALUES + 1)
    ]
    doc = build(events)
    assert len(doc.tools) == MAX_VALUES and doc.tools[0] == "tool-0"
    assert doc.metadata_truncated and not doc.content_truncated


def test_content_is_bounded_and_sets_only_the_content_flag() -> None:
    long_text = " ".join(f"w{i}" for i in range(9_000))
    # one event may carry 4,096 characters; spread the tokens over many events
    chunks = [long_text[i : i + 4_000] for i in range(0, len(long_text), 4_000)]
    events = [ev(1, "run.started")] + [
        ev(i + 2, "shell.command.completed", {"shell.command": chunk})
        for i, chunk in enumerate(chunks)
    ]
    doc = build(events)
    positions = [p for ps in parse_tsvector(doc.content_doc).values() for p in ps]
    assert doc.content_truncated and not doc.metadata_truncated
    assert max(positions) <= MAX_POSITIONS


def test_array_text_is_capped_at_64_kib() -> None:
    chunk = "x" * 4_000
    events = [ev(1, "run.started")] + [
        ev(i + 2, "shell.command.completed", {"shell.command": f"{i}{chunk}"}) for i in range(30)
    ]
    doc = build(events)
    assert sum(len(c.encode()) for c in doc.commands) <= MAX_ARRAY_BYTES
    assert len(doc.commands) < 30 and doc.content_truncated


def test_values_too_long_to_be_queried_are_dropped_and_flagged() -> None:
    doc = build([ev(1, "tool.call.completed", {"tool.name": "t" * 300})])
    assert doc.tools == () and doc.metadata_truncated


def test_error_count_is_taken_from_the_derivation() -> None:
    events = rich()
    assert build(events).error_count == derive_run(events).summary["error_count"]


def test_agents_come_from_the_given_list() -> None:
    doc = build(rich(), agents=["Planner", "coder"])
    assert doc.agents == ("planner", "coder")
    assert "planner" in parse_tsvector(doc.metadata_doc)

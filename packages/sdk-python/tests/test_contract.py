"""ADR-013: the stdlib builder must never drift from the event contract (abb_event_schema)."""

import json
import re
from pathlib import Path
from typing import Any

import abb_event_schema
import pytest
from abb_event_schema import limits
from abb_event_schema.ids import IdKind, id_pattern, parse_id
from abb_event_schema.parse import parse_event_in
from abb_event_schema.registry import EVENT_TYPES, lookup
from jsonschema import Draft202012Validator

from blackbox import events, ids
from blackbox.stats import Stats
from tests.helpers import events_of, offline, types_of

SCHEMA_PATH = Path(abb_event_schema.__file__ or "").parents[2] / "schemas/1.0/event-in.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text())
VALIDATOR = Draft202012Validator(SCHEMA)


def assert_contract(event: dict[str, Any]) -> None:
    parse_event_in(json.loads(json.dumps(event)))  # raises EventValidationError on any violation
    assert not list(VALIDATOR.iter_errors(event)), event["event_type"]


def test_limits_equal_the_contracts() -> None:
    for name in (
        "MAX_EVENT_BYTES",
        "MAX_INLINE_PAYLOAD_BYTES",
        "MAX_ATTRIBUTES",
        "MAX_ATTRIBUTE_KEY_LENGTH",
        "MAX_ATTRIBUTE_STRING_LENGTH",
        "MAX_ATTRIBUTE_LIST_ITEMS",
        "MAX_TAGS",
        "MAX_TAG_LENGTH",
        "MAX_SAFE_INTEGER",
    ):
        assert getattr(events, name) == getattr(limits, name), name


def test_priorities_equal_the_registrys_for_every_registered_type() -> None:
    for event_type, spec in EVENT_TYPES.items():
        assert events.priority_of(event_type) == spec.priority.value, event_type
    custom = lookup("custom.anything")
    assert custom is not None and events.priority_of("custom.anything") == custom.priority.value


def test_ids_match_the_contract_and_are_monotonic() -> None:
    made = [ids.new_id(ids.RUN) for _ in range(5000)]
    assert all(re.fullmatch(id_pattern(IdKind.RUN), i) for i in made)
    assert made == sorted(made) and len(set(made)) == len(made)
    for prefix, kind in (
        (ids.EVENT, IdKind.EVENT),
        (ids.TRACE, IdKind.TRACE),
        (ids.SPAN, IdKind.SPAN),
    ):
        assert parse_id(ids.new_id(prefix), kind).kind is kind


def test_event_type_pattern_equals_the_contract() -> None:
    from abb_event_schema.registry import EVENT_TYPE_PATTERN

    assert events.EVENT_TYPE_RE.pattern == EVENT_TYPE_PATTERN


def test_a_realistic_run_validates_against_models_and_json_schema() -> None:
    bb = offline(payload_mode="full", agent_version="1.2.3", tags=("ci",))
    with bb.run("fix oauth", metadata={"issue": 1842, "repo": "acme/web"}) as run:
        with run.span("plan", kind="agent"):
            run.event("custom.note", {"note": "hello"}, payload={"k": "v"})
        with run.span("github-search", kind="tool") as tool:
            tool.set_attribute("tool.result_count", 3)
            tool.set_payload({"query": "oauth timeout"})
        with run.llm_call("openai", "gpt-4.1", temperature=0.2) as llm:
            llm.record_usage(10, 20, cached_input_tokens=5, cost_usd=0.001)
        with run.span("db", kind="db"), run.span("search", kind="retrieval"):
            pass
        run.event("retry.attempted", {"retry.attempt": 2, "retry.reason": "timeout"})
        try:
            with run.span("boom", kind="shell"):
                raise RuntimeError("x")
        except RuntimeError:
            pass
        try:
            with run.llm_call("openai", "gpt-4.1"):
                raise ValueError("bad")
        except ValueError:
            pass
        try:
            with run.span("t", kind="tool"):
                raise ValueError("bad")
        except ValueError:
            pass
    evs = events_of(bb)
    assert len(evs) == 20
    for e in evs:
        assert_contract(e)
    assert {"tool.call.failed", "llm.request.failed", "span.failed"} <= set(types_of(evs))


@pytest.mark.parametrize("status", ["success", "error", "timeout", "blocked", "cancelled"])
def test_every_run_ending_validates(status: str) -> None:
    bb = offline()
    run = bb.run("r")
    run.end(status)
    for e in events_of(bb):
        assert_contract(e)


def test_hostile_attribute_values_never_produce_an_invalid_event() -> None:
    bb = offline()
    nasty = {
        "ok": "fine",
        "bad key!": 1,
        "nan": float("nan"),
        "inf": float("inf"),
        "big": 2**70,
        "obj": object(),
        "nested": {"a": 1},
        "nul": "a\x00b",
        "long": "x" * 10_000,
        "lst": [1, 2, "a"],
        "badlist": [1, {"x": 1}],
        "none": None,
        "flag": True,
    }
    with bb.run("r", metadata=nasty) as run:
        run.event("custom.hostile", nasty)
        run.event("not a type", nasty)  # coerced to custom.* or dropped, never raises
        run.event("x" * 100 + ".y", nasty)
    for e in events_of(bb):
        assert_contract(e)
    assert bb.stats()["attributes_dropped"] > 0


def test_attribute_count_and_tags_are_bounded() -> None:
    stats = Stats()
    cleaned = events.clean_attributes({f"k{i}": i for i in range(200)}, stats)
    assert len(cleaned) == limits.MAX_ATTRIBUTES and stats["attributes_dropped"] == 136
    assert len(events.clean_tags(["t"] * 50)) == limits.MAX_TAGS
    assert all(len(t) <= limits.MAX_TAG_LENGTH for t in events.clean_tags(["x" * 500]))

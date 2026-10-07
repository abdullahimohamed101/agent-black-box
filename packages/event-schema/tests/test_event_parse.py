import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from abb_event_schema import limits
from abb_event_schema.errors import ErrorCode, EventValidationError
from abb_event_schema.event import Event, EventIn, finalize
from abb_event_schema.parse import dumps, parse_event, parse_event_in
from tests.helpers import PRJ, WS, evt, make_event, spn, without

RECEIVED = datetime(2026, 10, 6, 20, 13, 23, tzinfo=timezone.utc)


def rejected(raw: Any) -> EventValidationError:
    with pytest.raises(EventValidationError) as exc:
        parse_event_in(raw)
    return exc.value


def issue_codes(err: EventValidationError) -> set[str]:
    return {i.code for i in err.issues}


# ---------------------------------------------------------------- valid events


def test_minimal_valid_event_parses_and_round_trips() -> None:
    event = parse_event_in(make_event())
    assert event.event_type == "tool.call.completed"
    assert event.occurred_at == datetime(2026, 10, 6, 20, 13, 22, 29000, tzinfo=timezone.utc)
    again = parse_event_in(dumps(event))
    assert again == event


def test_finalize_binds_tenant_and_event_round_trips() -> None:
    final = finalize(
        parse_event_in(make_event()), workspace_id=WS, project_id=PRJ, received_at=RECEIVED
    )
    assert isinstance(final, Event)
    assert (final.workspace_id, final.project_id, final.received_at) == (WS, PRJ, RECEIVED)
    assert parse_event(dumps(final)) == final


def test_events_are_immutable() -> None:
    event = parse_event_in(make_event())
    with pytest.raises(Exception):  # noqa: B017 - pydantic frozen-instance error
        event.event_type = "tool.call.failed"  # type: ignore[misc]


def test_offset_timestamps_are_normalised_to_utc() -> None:
    event = parse_event_in(make_event(occurred_at="2026-10-06T22:13:22+02:00"))
    assert event.occurred_at.utcoffset() == timedelta(0)
    assert event.occurred_at.hour == 20


def test_dict_input_is_accepted_and_equals_json_input() -> None:
    raw = make_event()
    assert parse_event_in(raw) == parse_event_in(json.dumps(raw))


# ---------------------------------------------------------------- tenant binding


def test_finalize_rejects_a_foreign_workspace() -> None:
    event = parse_event_in(make_event(workspace_id="ws_01J9YYYYYYYYYYYYYYYYYYYYYY"))
    with pytest.raises(EventValidationError) as exc:
        finalize(event, workspace_id=WS, project_id=PRJ, received_at=RECEIVED)
    assert exc.value.code is ErrorCode.EVENT_INVALID
    assert {i.loc for i in exc.value.issues} == {("workspace_id",)}


def test_finalize_accepts_matching_ids() -> None:
    event = parse_event_in(make_event(workspace_id=WS, project_id=PRJ))
    assert finalize(event, workspace_id=WS, project_id=PRJ, received_at=RECEIVED).workspace_id == WS


# ---------------------------------------------------------------- missing fields


@pytest.mark.parametrize(
    "field",
    ["schema_version", "event_id", "run_id", "trace_id", "agent_id", "event_type", "occurred_at"],
)
def test_missing_required_envelope_field(field: str) -> None:
    err = rejected(without(make_event(), field))
    assert err.code in (ErrorCode.EVENT_INVALID, ErrorCode.EVENT_SCHEMA_UNSUPPORTED)
    assert (field,) in {i.loc for i in err.issues}


def test_canonical_event_requires_server_fields() -> None:
    with pytest.raises(EventValidationError) as exc:
        parse_event(make_event())  # no workspace_id / project_id / received_at
    locs = {i.loc for i in exc.value.issues}
    assert {("workspace_id",), ("project_id",), ("received_at",)} <= locs


@pytest.mark.parametrize(
    ("event_type", "attributes", "missing"),
    [
        ("llm.request.completed", {"llm.model": "m"}, "llm.provider"),
        ("tool.call.started", {}, "tool.name"),
        ("file.modified", {}, "file.path"),
        ("shell.command.completed", {"shell.exit_code": 0}, "shell.command"),
        ("retry.attempted", {}, "retry.attempt"),
        ("approval.requested", {}, "approval.id"),
        ("policy.action.blocked", {}, "policy.id"),
        ("span.started", {"span.kind": "custom"}, "span.name"),
    ],
)
def test_known_types_require_their_attributes(
    event_type: str, attributes: dict[str, Any], missing: str
) -> None:
    err = rejected(make_event(event_type=event_type, attributes=attributes))
    assert ("attributes", missing) in {i.loc for i in err.issues}
    assert "attribute_required" in issue_codes(err)


@pytest.mark.parametrize(
    "event_type", ["tool.call.started", "tool.call.completed", "llm.request.failed"]
)
def test_span_bound_events_require_span_id(event_type: str) -> None:
    err = rejected(
        make_event(
            event_type=event_type,
            span_id=...,
            attributes={"tool.name": "t", "llm.provider": "p", "llm.model": "m"},
        )
    )
    assert "span_id_required" in issue_codes(err)


def test_span_started_needs_a_known_kind() -> None:
    err = rejected(
        make_event(event_type="span.started", attributes={"span.name": "x", "span.kind": "banana"})
    )
    assert "span_kind_invalid" in issue_codes(err)


# ---------------------------------------------------------------- malformed events


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_id", "evt_not-a-ulid"),
        ("event_id", "run_01J90000000000000000000001"),  # wrong prefix for the field
        ("run_id", ""),
        ("trace_id", None),
        ("agent_id", "Has Spaces"),
        ("agent_id", "UPPER"),
        ("agent_id", "a" * 65),
        ("event_type", "Tool.Call"),
        ("event_type", "toolcall"),  # one segment
        ("event_type", "a.b.c.d.e"),  # five segments
        ("event_type", "tool." + "x" * 80),
        ("occurred_at", "yesterday"),
        ("occurred_at", "2026-10-06T20:13:22"),  # naive: no UTC offset
        ("occurred_at", 1_790_000_000),
        ("sequence", -1),
        ("sequence", "5"),  # strict: no string -> int coercion on the wire
        ("sequence", 2**53),
        ("sequence", True),
        ("status", "great"),
        ("duration_ms", -5),
        ("duration_ms", "fast"),
        ("tags", "production"),
        ("tags", [""]),
        ("tags", ["x"] * (limits.MAX_TAGS + 1)),
        ("payload_ref", "https://example.com/x"),
        ("payload_ref", "artifact://"),
        ("agent_version", ""),
        ("sdk", {"name": "", "version": "1"}),
    ],
)
def test_malformed_field_is_rejected(field: str, value: Any) -> None:
    err = rejected(make_event(**{field: value}))
    assert err.code is ErrorCode.EVENT_INVALID
    assert err.issues
    assert field in {str(loc) for i in err.issues for loc in i.loc}


def test_naive_timestamp_has_a_specific_code() -> None:
    err = rejected(make_event(occurred_at="2026-10-06T20:13:22"))
    assert "timestamp_timezone_required" in issue_codes(err)


@pytest.mark.parametrize("raw", ["", "{", "[]", "null", '"text"', "42"])
def test_non_object_or_broken_json(raw: str) -> None:
    err = rejected(raw)
    assert err.code is ErrorCode.EVENT_INVALID


def test_rejects_non_json_python_objects() -> None:
    err = rejected({"x": {1, 2}})
    assert err.code is ErrorCode.EVENT_INVALID


def test_parent_without_span_and_self_parent() -> None:
    assert "parent_without_span" in issue_codes(
        rejected(
            make_event(
                span_id=...,
                parent_span_id=spn(2),
                event_type="file.read",
                attributes={"file.path": "a"},
            )
        )
    )
    assert "span_is_own_parent" in issue_codes(rejected(make_event(parent_span_id=spn(1))))


def test_valid_parent_child_is_accepted() -> None:
    event = parse_event_in(make_event(parent_span_id=spn(2)))
    assert event.parent_span_id == spn(2)


# ---------------------------------------------------------------- attributes


def test_unknown_attributes_are_preserved_exactly() -> None:
    attrs = {
        "tool.name": "github",
        "vendor.custom_flag": True,
        "vendor.score": 0.25,
        "vendor.tags": ["a", "b"],
        "vendor.note": "naïve ✓",
    }
    event = parse_event_in(make_event(attributes=attrs))
    assert event.attributes == attrs
    assert parse_event_in(dumps(event)).attributes == attrs


def test_unknown_top_level_fields_are_ignored_not_fatal() -> None:
    event = parse_event_in(make_event(future_field={"x": 1}, schema_version="1.7"))
    assert not hasattr(event, "future_field")
    assert event.schema_version == "1.7"


def test_unknown_but_well_formed_event_types_are_accepted() -> None:
    assert parse_event_in(make_event(event_type="vendor.thing.happened", attributes={}))
    assert parse_event_in(make_event(event_type="custom.cache_hit", attributes={"key": "x"}))


@pytest.mark.parametrize(
    ("attributes", "code"),
    [
        ({"Bad Key": 1}, "attribute_key_invalid"),
        ({"UPPER.key": 1}, "attribute_key_invalid"),
        ({"k" * 129: 1}, "attribute_key_invalid"),
        ({"nested": {"a": 1}}, "attribute_value_invalid"),
        ({"list": [[1]]}, "attribute_value_invalid"),
        ({"none": None}, "attribute_value_invalid"),
        ({"big": 2**53}, "attribute_integer_out_of_range"),
        ({"long": "x" * (limits.MAX_ATTRIBUTE_STRING_LENGTH + 1)}, "attribute_string_too_long"),
        ({"many": list(range(limits.MAX_ATTRIBUTE_LIST_ITEMS + 1))}, "attribute_list_too_long"),
        ({"tool.name": 5}, "attribute_type_mismatch"),
        ({"tool.name": "t", "llm.input_tokens": "12"}, "attribute_type_mismatch"),
        ({"tool.name": "t", "llm.input_tokens": True}, "attribute_type_mismatch"),
        ({"tool.name": "t", "llm.input_tokens": -1}, "attribute_below_minimum"),
        (
            {"tool.name": "t", "llm.latency_ms": 1.5, "shell.exit_code": 1.5},
            "attribute_type_mismatch",
        ),
        ({"tool.name": "t", "test.failing": [1, 2]}, "attribute_type_mismatch"),
    ],
)
def test_attribute_rules(attributes: dict[str, Any], code: str) -> None:
    assert code in issue_codes(rejected(make_event(attributes=attributes)))


def test_too_many_attributes() -> None:
    attrs: dict[str, Any] = {f"k{i}": i for i in range(limits.MAX_ATTRIBUTES + 1)}
    attrs["tool.name"] = "t"
    assert "attributes_too_many" in issue_codes(rejected(make_event(attributes=attrs)))


def test_known_attribute_types_apply_to_custom_events_too() -> None:
    err = rejected(make_event(event_type="custom.thing", attributes={"llm.model": 5}))
    assert "attribute_type_mismatch" in issue_codes(err)


def test_negative_exit_code_is_allowed_signal_style() -> None:
    event = parse_event_in(
        make_event(
            event_type="shell.command.completed",
            attributes={"shell.command": "x", "shell.exit_code": -9},
        )
    )
    assert event.attributes["shell.exit_code"] == -9


# ---------------------------------------------------------------- NUL, payload, size


@pytest.mark.parametrize(
    "mutation",
    [
        {"attributes": {"tool.name": "a\x00b"}},
        {"attributes": {"tool.name": "t", "vendor.list": ["ok", "a\x00"]}},
        {"tags": ["a\x00"]},
        {"agent_version": "1\x002"},
        {"payload": {"k": "v\x00"}},
        {"payload": {"k\x00": "v"}},
        {"payload": {"deep": [{"x": "\x00"}]}},
    ],
)
def test_nul_characters_are_rejected_everywhere(mutation: dict[str, Any]) -> None:
    assert "string_contains_nul" in issue_codes(rejected(make_event(**mutation)))


def test_inline_payload_roundtrip_and_limits() -> None:
    event = parse_event_in(make_event(payload={"prompt": "hi", "n": [1, 2, {"a": None}]}))
    assert event.payload == {"prompt": "hi", "n": [1, 2, {"a": None}]}
    big = {"blob": "x" * limits.MAX_INLINE_PAYLOAD_BYTES}
    assert "payload_too_large" in issue_codes(rejected(make_event(payload=big)))


def test_payload_nesting_is_bounded() -> None:
    node: Any = {}
    for _ in range(40):
        node = {"n": node}  # nested dict
    assert "payload_too_deep" in issue_codes(rejected(make_event(payload=node)))


def test_event_over_256kb_is_too_large() -> None:
    raw = json.dumps(make_event(payload={"a": "x" * 60_000}))
    padded = raw[:-1] + ',"future":"' + "y" * limits.MAX_EVENT_BYTES + '"}'
    err = rejected(padded)
    assert err.code is ErrorCode.EVENT_TOO_LARGE


def test_non_finite_numbers_are_rejected() -> None:
    err = rejected('{"schema_version":"1.0","duration_ms":NaN}')
    assert err.code is ErrorCode.EVENT_INVALID


# ---------------------------------------------------------------- schema versions


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.7"])
def test_supported_versions(version: str) -> None:
    assert parse_event_in(make_event(schema_version=version)).schema_version == version


@pytest.mark.parametrize("version", ["2.0", "0.1", "9.9"])
def test_unsupported_major_versions(version: str) -> None:
    assert rejected(make_event(schema_version=version)).code is ErrorCode.EVENT_SCHEMA_UNSUPPORTED


@pytest.mark.parametrize("version", ["1", "v1.0", "", "1.0.0", 1, None])
def test_malformed_versions(version: Any) -> None:
    assert rejected(make_event(schema_version=version)).code is ErrorCode.EVENT_SCHEMA_UNSUPPORTED


def test_missing_version() -> None:
    assert (
        rejected(without(make_event(), "schema_version")).code is ErrorCode.EVENT_SCHEMA_UNSUPPORTED
    )


# ---------------------------------------------------------------- never echo values


SECRET = "sk-live-SUPERSECRET-123456"


@pytest.mark.parametrize(
    "mutation",
    [
        {"event_id": SECRET},
        {"agent_id": SECRET.upper()},
        {"event_type": SECRET},
        {"occurred_at": SECRET},
        {"sequence": SECRET},
        {"status": SECRET},
        {"tags": [SECRET + "\x00"]},
        {"payload_ref": SECRET},
        {"attributes": {SECRET: 1}},
        {"attributes": {"tool.name": 5, "vendor.secret": {"nested": SECRET}}},
        {"attributes": {"tool.name": SECRET + "\x00"}},
        {"payload": {"k": SECRET + "\x00"}},
        {"sdk": {"name": SECRET * 5, "version": "1"}},
    ],
)
def test_error_output_never_contains_submitted_values(mutation: dict[str, Any]) -> None:
    err = rejected(make_event(**mutation))
    assert err.issues, "the mutation must actually be rejected"
    rendered = repr(err.issues) + err.message + str(err)
    assert "SUPERSECRET" not in rendered.upper()


def test_tag_limit_constant_matches_tag_type() -> None:
    assert limits.MAX_TAG_LENGTH == 64
    assert parse_event_in(make_event(tags=["t" * 64])).tags == ["t" * 64]
    assert "tags" in {
        str(loc) for i in rejected(make_event(tags=["t" * 65])).issues for loc in i.loc
    }


def test_constructing_directly_validates_too() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        EventIn.model_validate(make_event(event_id="nope"))
    assert evt(1) == EventIn.model_validate(make_event()).event_id


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("1.0", "timestamp_format_invalid"),  # pydantic would read this as a Unix timestamp
        ("1790000000", "timestamp_format_invalid"),
        ("2026-10-06 20:13:22Z", "timestamp_format_invalid"),
        ("2026-10-06t20:13:22z", "timestamp_format_invalid"),
        ("2026-10-06", "timestamp_format_invalid"),
        ("2026-13-01T00:00:00Z", "timestamp_format_invalid"),
        ("2026-02-30T00:00:00Z", "timestamp_format_invalid"),
        ("2026-10-06T20:13:60Z", "timestamp_format_invalid"),
        ("2026-10-06T20:13:22", "timestamp_timezone_required"),
        ("2026-10-06T20:13:22.5", "timestamp_timezone_required"),
    ],
)
def test_timestamps_must_be_canonical_rfc3339(value: str, code: str) -> None:
    assert code in issue_codes(rejected(make_event(occurred_at=value)))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-10-06T20:13:22Z", datetime(2026, 10, 6, 20, 13, 22, tzinfo=timezone.utc)),
        ("2026-10-06T20:13:22.5Z", datetime(2026, 10, 6, 20, 13, 22, 500000, tzinfo=timezone.utc)),
        (
            "2026-10-06T20:13:22.123456789Z",
            datetime(2026, 10, 6, 20, 13, 22, 123456, tzinfo=timezone.utc),
        ),
        ("2026-10-06T22:13:22+02:00", datetime(2026, 10, 6, 20, 13, 22, tzinfo=timezone.utc)),
        ("2026-10-06T15:43:22-04:30", datetime(2026, 10, 6, 20, 13, 22, tzinfo=timezone.utc)),
    ],
)
def test_valid_rfc3339_forms_normalise_to_utc(value: str, expected: datetime) -> None:
    assert parse_event_in(make_event(occurred_at=value)).occurred_at == expected


# Python's `$` also matches before a trailing newline; every pattern must reject it.
@pytest.mark.parametrize(
    "mutation",
    [
        {"event_id": evt(1) + "\n"},
        {"run_id": make_event()["run_id"] + "\n"},
        {"event_type": "tool.call.completed\n"},
        {"schema_version": "1.0\n"},
        {"occurred_at": "2026-10-06T20:13:22Z\n"},
        {"agent_id": "coding-agent\n"},
        {"payload_ref": "artifact://a/b\n"},
        {"attributes": {"tool.name": "t", "vendor.key\n": 1}},
    ],
)
def test_trailing_newline_is_never_accepted(mutation: dict[str, Any]) -> None:
    assert rejected(make_event(**mutation)).issues


def test_text_that_cannot_be_utf8_is_a_validation_error_not_a_crash() -> None:
    assert rejected('{"a":"\ud800"}').code is ErrorCode.EVENT_INVALID


def test_validation_error_carries_args() -> None:
    err = rejected({})
    assert err.args and err.args[0] in {"EVENT_INVALID", "EVENT_SCHEMA_UNSUPPORTED"}

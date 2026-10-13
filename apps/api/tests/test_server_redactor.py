"""ServerRedactor on single events: limits that redaction could break, budgets, determinism."""

from typing import Any

import pytest
from abb_event_schema import limits
from abb_event_schema.event import Event, finalize
from abb_event_schema.ids import IdKind, new_id
from abb_event_schema.parse import parse_event, parse_event_in

from abb_api.db.event_rows import row_to_event
from abb_api.security.redaction import (
    ArtifactDecision,
    RedactionError,
    ServerRedactor,
    placeholder_body,
)
from abb_api.security.scanner import ScanFailed, SecretScanner
from abb_api.security.settings import CaptureMode
from tests.ingest_helpers import RECEIVED, make_run_ids, wire_event

AWS = "AKIAIOSFODNN7EXAMPLE"
REDACTED_AWS = "[REDACTED:aws_access_key]"


def event(**fields: Any) -> Event:
    attributes = fields.pop("attributes", {"tool.name": "t"})
    return finalize(
        parse_event_in(wire_event(make_run_ids(), 1, attributes=attributes, **fields)),
        workspace_id=new_id(IdKind.WORKSPACE),
        project_id=new_id(IdKind.PROJECT),
        received_at=RECEIVED,
    )


def valid(e: Event) -> Event:
    """What the database would hand back: the stored form must parse again."""
    return parse_event(e.to_wire())


def test_an_event_with_nothing_to_change_is_returned_as_is() -> None:
    e = event(tags=["a"], payload={"k": 1})
    for mode in CaptureMode:
        out = ServerRedactor().apply(e, mode if mode is CaptureMode.FULL else CaptureMode.FULL)
        assert out is e


def test_redaction_that_lengthens_a_value_still_fits_the_limits() -> None:
    # 200 keys of 20 characters become 25-character markers: 5,000 characters > 4,096
    value = (AWS + " ") * 195
    assert len(value) <= limits.MAX_ATTRIBUTE_STRING_LENGTH
    out = ServerRedactor().apply(
        event(attributes={"tool.name": "t", "note": value}), CaptureMode.FULL
    )
    assert len(out.attributes["note"]) == limits.MAX_ATTRIBUTE_STRING_LENGTH
    assert AWS not in out.attributes["note"]
    valid(out)


def test_a_tag_that_grows_is_cut_to_the_tag_limit() -> None:
    out = ServerRedactor().apply(event(tags=[AWS + "x" * 40]), CaptureMode.FULL)
    assert len(out.tags[0]) <= limits.MAX_TAG_LENGTH and AWS not in out.tags[0]
    valid(out)


def test_a_payload_pushed_over_the_inline_limit_by_markers_is_dropped() -> None:
    items = [AWS] * 2800  # ~64 KB of keys (just under the limit); the markers are ~78 KB
    e = event(payload={"items": items})
    out = ServerRedactor().apply(e, CaptureMode.FULL)
    assert out.payload is None
    assert out.attributes["redaction.dropped_reason"] == "too_large"
    assert out.attributes["redaction.server_count"] == 2800  # found, even though dropped
    valid(out)


def test_the_markers_fit_when_an_event_already_has_the_maximum_attributes() -> None:
    attributes = {f"x.k{i:02d}": "v" for i in range(limits.MAX_ATTRIBUTES - 2)}
    attributes["tool.name"] = "t"
    attributes["note"] = AWS
    e = event(attributes=attributes)
    assert len(e.attributes) == limits.MAX_ATTRIBUTES
    out = ServerRedactor().apply(e, CaptureMode.FULL)
    assert len(out.attributes) <= limits.MAX_ATTRIBUTES
    assert out.attributes["redaction.server_count"] == 1
    assert out.attributes["tool.name"] == "t"
    valid(out)


def test_the_size_budget_drops_what_does_not_fit_and_keeps_metadata_first() -> None:
    e = event(
        attributes={"tool.name": "t", "note": f"{AWS} ok"},
        payload={"big": "x" * 500, "more": AWS},
    )
    out = ServerRedactor(event_max_chars=200).apply(e, CaptureMode.FULL)
    assert out.payload is None  # attributes are scanned first; the payload is what is cut
    assert out.attributes["note"] == f"{REDACTED_AWS} ok"
    assert out.attributes["redaction.dropped_reason"] == "too_large"
    assert out.attributes["redaction.server_count"] == 1


def test_the_time_budget_drops_with_scan_failed() -> None:
    clock = iter(range(0, 1000, 10))
    redactor = ServerRedactor(event_max_seconds=1.0, clock=lambda: float(next(clock)))
    out = redactor.apply(event(attributes={"tool.name": "t", "note": AWS}), CaptureMode.FULL)
    assert "note" not in out.attributes and out.attributes["tool.name"] == "[dropped]"
    assert out.attributes["redaction.dropped_reason"] == "scan_failed"


def test_scan_failed_outranks_too_large_which_outranks_the_mode() -> None:
    class Flaky(SecretScanner):
        def scan_text(self, text: str, budget: Any, **kwargs: Any) -> Any:
            if text == "BOOM":
                raise ScanFailed("x")
            return super().scan_text(text, budget, **kwargs)

    e = event(attributes={"tool.name": "t", "a": "BOOM"}, payload={"k": "v"}, payload_ref=None)
    out = ServerRedactor(Flaky()).apply(e, CaptureMode.METADATA_ONLY)
    assert out.attributes["redaction.dropped_reason"] == "scan_failed"
    plain = ServerRedactor().apply(e, CaptureMode.METADATA_ONLY)
    assert plain.attributes["redaction.dropped_reason"] == "mode_metadata_only"


def test_string_lists_are_scanned_item_by_item() -> None:
    out = ServerRedactor().apply(
        event(attributes={"tool.name": "t", "test.failing": [f"a {AWS}", "b"]}), CaptureMode.FULL
    )
    assert out.attributes["test.failing"] == [f"a {REDACTED_AWS}", "b"]


def test_keys_of_a_payload_are_scanned_and_secret_named_keys_are_replaced() -> None:
    out = ServerRedactor().apply(
        event(
            payload={
                AWS: "v",
                "api-key": ["x", {"y": "z"}],
                "Authorization": "Bearer abc",
                "n": None,
            }
        ),
        CaptureMode.FULL,
    )
    assert out.payload == {
        REDACTED_AWS: "v",
        "api-key": "[REDACTED:api_key]",
        "Authorization": "[REDACTED:authorization]",
        "n": None,
    }


def test_numbers_and_booleans_under_secret_keys_are_kept() -> None:
    out = ServerRedactor().apply(
        event(payload={"max_tokens": 10, "token_ok": True}), CaptureMode.FULL
    )
    assert out.payload == {"max_tokens": 10, "token_ok": True}


def test_a_modified_event_is_valid_and_round_trips() -> None:
    e = event(
        event_type="shell.command.completed",
        attributes={"shell.command": f"echo {AWS}", "shell.exit_code": 0},
        payload={"out": AWS},
    )
    out = ServerRedactor().apply(e, CaptureMode.FULL)
    assert valid(out) == out
    assert out.attributes["shell.command"] == f"echo {REDACTED_AWS}"


def test_the_same_event_always_yields_the_same_stored_event() -> None:
    e = event(attributes={"tool.name": "t", "n": AWS}, payload={"a": [AWS, "x"]}, tags=[AWS])
    first, second = (
        ServerRedactor().apply(e, CaptureMode.FULL),
        ServerRedactor().apply(e, CaptureMode.FULL),
    )
    assert first == second and first.to_wire() == second.to_wire()


def test_a_redaction_that_cannot_produce_a_valid_event_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_: object, **__: object) -> None:
        raise ValueError("invalid")

    e = event(attributes={"tool.name": "t", "n": AWS})
    monkeypatch.setattr(Event, "model_validate", refuse)
    with pytest.raises(RedactionError):
        ServerRedactor().apply(e, CaptureMode.FULL)


def test_a_dropped_required_attribute_keeps_the_event_readable_from_a_row() -> None:
    class Down(SecretScanner):
        def scan_text(self, text: str, budget: Any, **kwargs: Any) -> Any:
            raise ScanFailed("down")

    e = event(event_type="file.read", attributes={"file.path": "/etc/x"})
    out = ServerRedactor(Down()).apply(e, CaptureMode.FULL)
    assert out.attributes["file.path"] == "[dropped]"

    class Row:  # the shape row_to_event reads
        def __init__(self, ev: Event) -> None:
            from abb_event_schema.ids import to_uuid

            self.schema_version, self.agent_id = ev.schema_version, ev.agent_id
            self.event_id, self.workspace_id = to_uuid(ev.event_id), to_uuid(ev.workspace_id)
            self.project_id, self.run_id = to_uuid(ev.project_id), to_uuid(ev.run_id)
            self.trace_id = to_uuid(ev.trace_id)
            self.span_id = self.parent_span_id = None
            self.agent_version, self.event_type = ev.agent_version, ev.event_type
            self.occurred_at, self.received_at = ev.occurred_at, ev.received_at
            self.sequence, self.status, self.duration_ms = ev.sequence, None, None
            self.attributes, self.payload, self.payload_ref = ev.attributes, None, None
            self.tags: list[str] = []
            self.sdk = None

    assert row_to_event(Row(out)).attributes["file.path"] == "[dropped]"


# ----------------------------------------------------------------------- artifact bodies


def decide(data: bytes, mode: CaptureMode, **kw: Any) -> ArtifactDecision:
    return ServerRedactor().artifact_body(
        data, mode, max_bytes=kw.get("max_bytes", 10**6), max_seconds=kw.get("max_seconds", 10.0)
    )


def test_artifact_decisions_per_mode() -> None:
    data = f"x {AWS} y\n".encode()
    full = decide(data, CaptureMode.FULL)
    assert full.body == f"x {REDACTED_AWS} y\n".encode()
    assert (full.count, full.kinds, full.dropped_reason) == (1, ("aws_access_key",), None)
    assert full.record() == {"server_count": 1, "server_kinds": ["aws_access_key"]}
    for mode, reason in (
        (CaptureMode.METADATA_ONLY, "mode_metadata_only"),
        (CaptureMode.DISABLED, "mode_disabled"),
    ):
        dropped = decide(data, mode)
        assert dropped.dropped_reason == reason and dropped.body == placeholder_body(reason)
        assert AWS.encode() not in dropped.body
    assert decide(data, CaptureMode.FULL, max_bytes=3).dropped_reason == "too_large"
    assert decide(data, CaptureMode.FULL, max_seconds=-1).dropped_reason == "scan_failed"

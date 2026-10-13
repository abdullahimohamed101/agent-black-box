"""Server redaction markers (ADR-130): typed attributes the server writes, never content."""

from typing import Any

import pytest

from abb_event_schema.errors import EventValidationError
from abb_event_schema.parse import parse_event_in
from abb_event_schema.registry import (
    DROPPED_REASONS,
    KNOWN_ATTRIBUTES,
    PAYLOAD_REFERENCE_ATTRIBUTES,
    SERVER_REDACTION_ATTRIBUTES,
    AttrType,
)
from tests.helpers import make_event


def test_the_markers_are_registered_with_their_types() -> None:
    assert SERVER_REDACTION_ATTRIBUTES == {
        "redaction.server_count",
        "redaction.server_kinds",
        "redaction.dropped_reason",
    }
    assert KNOWN_ATTRIBUTES["redaction.server_count"].type is AttrType.INTEGER
    assert KNOWN_ATTRIBUTES["redaction.server_kinds"].type is AttrType.STRING_LIST
    assert KNOWN_ATTRIBUTES["redaction.dropped_reason"].type is AttrType.STRING
    assert DROPPED_REASONS == ("mode_metadata_only", "mode_disabled", "scan_failed", "too_large")


def test_payload_references_are_registered_string_attributes() -> None:
    for key in PAYLOAD_REFERENCE_ATTRIBUTES:
        assert KNOWN_ATTRIBUTES[key].type is AttrType.STRING


def test_an_event_with_the_markers_is_valid() -> None:
    event = parse_event_in(
        make_event(
            attributes={
                "tool.name": "x",
                "redaction.server_count": 2,
                "redaction.server_kinds": ["aws_access_key", "private_key"],
                "redaction.dropped_reason": "too_large",
            }
        )
    )
    assert event.attributes["redaction.server_count"] == 2


@pytest.mark.parametrize(
    "attrs",
    [
        {"redaction.server_count": -1},
        {"redaction.server_count": "2"},
        {"redaction.server_kinds": "private_key"},
        {"redaction.dropped_reason": 3},
    ],
)
def test_wrong_types_are_rejected(attrs: dict[str, Any]) -> None:
    with pytest.raises(EventValidationError):
        parse_event_in(make_event(attributes={"tool.name": "x", **attrs}))

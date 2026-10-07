"""Parsing and serializing events at the wire boundary.

All wire input funnels through `parse_event`, which validates strictly (no type coercion) and
reports problems as `EventValidationError` with stable codes. Python callers constructing events
directly get pydantic's normal lax behaviour.
"""

import json
from typing import Any

from pydantic import ValidationError

from abb_event_schema import limits
from abb_event_schema.errors import (
    ErrorCode,
    EventValidationError,
    Issue,
    issues_from_validation_error,
)
from abb_event_schema.event import Event, EventIn
from abb_event_schema.versioning import check_supported


def _to_bytes(raw: str | bytes | dict[str, Any]) -> bytes:
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, str):
        try:
            return raw.encode("utf-8")
        except UnicodeEncodeError:  # e.g. an unpaired surrogate: not valid text on the wire
            raise EventValidationError(
                ErrorCode.EVENT_INVALID,
                "Event is not valid UTF-8 text.",
                [Issue((), "json_invalid", "Event is not valid UTF-8 text.")],
            ) from None
    try:
        return json.dumps(raw, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        raise EventValidationError(
            ErrorCode.EVENT_INVALID,
            "Event is not plain JSON.",
            [Issue((), "not_json", "Event is not plain JSON.")],
        ) from None


def _parse(model: type[EventIn] | type[Event], raw: str | bytes | dict[str, Any]) -> Any:
    data = _to_bytes(raw)
    if len(data) > limits.MAX_EVENT_BYTES:
        raise EventValidationError(
            ErrorCode.EVENT_TOO_LARGE,
            f"Event exceeds {limits.MAX_EVENT_BYTES} bytes.",
            [Issue((), "event_too_large", "Event is too large.")],
        )
    try:
        decoded = json.loads(data)
    except (ValueError, RecursionError):
        raise EventValidationError(
            ErrorCode.EVENT_INVALID,
            "Event is not valid JSON.",
            [Issue((), "json_invalid", "Event is not valid JSON.")],
        ) from None
    if not isinstance(decoded, dict):
        raise EventValidationError(
            ErrorCode.EVENT_INVALID,
            "Event must be a JSON object.",
            [Issue((), "not_an_object", "Event must be a JSON object.")],
        )
    version = decoded.get("schema_version")
    if not isinstance(version, str):
        raise EventValidationError(
            ErrorCode.EVENT_SCHEMA_UNSUPPORTED,
            "schema_version is required.",
            [Issue(("schema_version",), "missing", "Field required.")],
        )
    check_supported(version)
    try:
        return model.model_validate_json(data, strict=True)
    except ValidationError as exc:
        raise EventValidationError(
            ErrorCode.EVENT_INVALID, "Event failed validation.", issues_from_validation_error(exc)
        ) from None


def parse_event_in(raw: str | bytes | dict[str, Any]) -> EventIn:
    """Validate a client-submitted event."""
    result: EventIn = _parse(EventIn, raw)
    return result


def parse_event(raw: str | bytes | dict[str, Any]) -> Event:
    """Validate a stored/canonical event (all server-assigned fields present)."""
    result: Event = _parse(Event, raw)
    return result


def dumps(event: EventIn | Event) -> bytes:
    """Canonical wire encoding: compact JSON, UTF-8, unset optionals omitted."""
    return json.dumps(
        event.to_wire(), separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")

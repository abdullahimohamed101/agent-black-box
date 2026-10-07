"""The canonical event envelope (spec §64.1) as two models.

`EventIn` is what a client sends: an SDK only knows its API key, so `workspace_id` and
`project_id` are optional and, if present, must match the key. `Event` is the canonical stored
form with both set and the server's `received_at`. Events are immutable (INV-1).
"""

import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from abb_event_schema import limits
from abb_event_schema.enums import EventStatus, SpanKind
from abb_event_schema.errors import ErrorCode, EventValidationError, Issue
from abb_event_schema.ids import IdKind, id_pattern
from abb_event_schema.registry import (
    ATTRIBUTE_KEY_PATTERN,
    EVENT_TYPE_PATTERN,
    KNOWN_ATTRIBUTES,
    MAX_EVENT_TYPE_LENGTH,
    AttrType,
    SpanRole,
    lookup,
)
from abb_event_schema.versioning import SCHEMA_VERSION, SUPPORTED_MAJOR

_ATTR_KEY_RE = re.compile(ATTRIBUTE_KEY_PATTERN)
_MAX_PAYLOAD_DEPTH = 16


def _fail(code: str, message: str, loc: tuple[str | int, ...] = ()) -> PydanticCustomError:
    return PydanticCustomError(code, message, {"loc": list(loc)})


def _no_nul(value: str) -> str:
    # PostgreSQL text/jsonb cannot store U+0000; rejecting here beats a 500 at insert time.
    if "\x00" in value:
        raise _fail("string_contains_nul", "Strings must not contain NUL characters.")
    return value


TIMESTAMP_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?(Z|[+-]\d{2}:\d{2})$"
_TIMESTAMP_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})?$"
)


def _parse_rfc3339(value: Any) -> Any:
    """Accept timestamp *strings* only in canonical RFC 3339 form, and parse them ourselves.

    Pydantic would otherwise read "1.0" as a Unix timestamp and accept space-separated or
    lowercase variants that other languages and the JSON Schema reject. Parsing here also
    keeps Python 3.10 happy (`fromisoformat` only learned "Z" in 3.11).
    """
    if not isinstance(value, str):
        return value
    match = _TIMESTAMP_RE.match(value)
    if match is None:
        raise _fail(
            "timestamp_format_invalid", "Timestamps must be RFC 3339, e.g. 2026-10-06T20:13:22Z."
        )
    year, month, day, hour, minute, second, fraction, offset = match.groups()
    if offset is None:
        raise _fail("timestamp_timezone_required", "Timestamps must include a UTC offset.")
    if offset == "Z":
        tz = timezone.utc
    else:
        sign = -1 if offset[0] == "-" else 1
        tz = timezone(sign * timedelta(hours=int(offset[1:3]), minutes=int(offset[4:6])))
    micros = int((fraction or "0").ljust(9, "0")[:6])  # sub-microsecond digits are dropped
    try:
        return datetime(
            int(year), int(month), int(day), int(hour), int(minute), int(second), micros, tz
        )
    except ValueError:
        raise _fail("timestamp_format_invalid", "Timestamp is not a real date and time.") from None


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise _fail("timestamp_timezone_required", "Timestamps must include a UTC offset.")
    return value.astimezone(timezone.utc)


EventId = Annotated[str, StringConstraints(pattern=id_pattern(IdKind.EVENT))]
RunId = Annotated[str, StringConstraints(pattern=id_pattern(IdKind.RUN))]
TraceId = Annotated[str, StringConstraints(pattern=id_pattern(IdKind.TRACE))]
SpanId = Annotated[str, StringConstraints(pattern=id_pattern(IdKind.SPAN))]
WorkspaceId = Annotated[str, StringConstraints(pattern=id_pattern(IdKind.WORKSPACE))]
ProjectId = Annotated[str, StringConstraints(pattern=id_pattern(IdKind.PROJECT))]
Name32 = Annotated[str, StringConstraints(min_length=1, max_length=32), AfterValidator(_no_nul)]
Name64 = Annotated[str, StringConstraints(min_length=1, max_length=64), AfterValidator(_no_nul)]
Name128 = Annotated[str, StringConstraints(min_length=1, max_length=128), AfterValidator(_no_nul)]
AgentSlug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]
EventTypeName = Annotated[
    str, StringConstraints(pattern=EVENT_TYPE_PATTERN, max_length=MAX_EVENT_TYPE_LENGTH)
]
SchemaVersionStr = Annotated[str, StringConstraints(pattern=r"^[0-9]{1,4}\.[0-9]{1,4}$")]
UtcDatetime = Annotated[datetime, BeforeValidator(_parse_rfc3339), AfterValidator(_require_utc)]
Tag = Name64  # limits.MAX_TAG_LENGTH == 64; asserted in tests
PayloadRef = Annotated[str, StringConstraints(pattern=r"^artifact://[A-Za-z0-9._~:/-]{1,480}$")]


class SdkInfo(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    name: Name64
    version: Name32


def _check_scalar(key: str, value: Any, *, allow_list: bool) -> None:
    loc = ("attributes", key)
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        if abs(value) > limits.MAX_SAFE_INTEGER:
            raise _fail("attribute_integer_out_of_range", f"Attribute {key} is out of range.", loc)
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _fail("attribute_not_finite", f"Attribute {key} must be a finite number.", loc)
        return
    if isinstance(value, str):
        if len(value) > limits.MAX_ATTRIBUTE_STRING_LENGTH:
            raise _fail("attribute_string_too_long", f"Attribute {key} is too long.", loc)
        _no_nul(value)
        return
    if allow_list and isinstance(value, list):
        if len(value) > limits.MAX_ATTRIBUTE_LIST_ITEMS:
            raise _fail("attribute_list_too_long", f"Attribute {key} has too many items.", loc)
        for item in value:
            _check_scalar(key, item, allow_list=False)
        return
    raise _fail(
        "attribute_value_invalid",
        f"Attribute {key} must be a string, number, boolean or a flat list of those.",
        loc,
    )


def _check_known_type(key: str, value: Any) -> None:
    spec = KNOWN_ATTRIBUTES.get(key)
    if spec is None:
        return  # unknown attributes are preserved untouched
    loc = ("attributes", key)
    is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
    ok = {
        AttrType.STRING: isinstance(value, str),
        AttrType.INTEGER: isinstance(value, int) and not isinstance(value, bool),
        AttrType.NUMBER: is_number,
        AttrType.BOOLEAN: isinstance(value, bool),
        AttrType.STRING_LIST: isinstance(value, list) and all(isinstance(i, str) for i in value),
    }[spec.type]
    if not ok:
        raise _fail("attribute_type_mismatch", f"Attribute {key} must be {spec.type.value}.", loc)
    if spec.minimum is not None and is_number and value < spec.minimum:
        raise _fail("attribute_below_minimum", f"Attribute {key} is below its minimum.", loc)


def _check_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    if len(attributes) > limits.MAX_ATTRIBUTES:
        raise _fail("attributes_too_many", "Too many attributes.", ("attributes",))
    for key, value in attributes.items():
        if len(key) > limits.MAX_ATTRIBUTE_KEY_LENGTH or not _ATTR_KEY_RE.match(key):
            # The key itself is not echoed: keys are caller-controlled text.
            raise _fail("attribute_key_invalid", "An attribute key is malformed.", ("attributes",))
        _check_scalar(key, value, allow_list=True)
        _check_known_type(key, value)
    return attributes


def _check_payload(payload: dict[str, Any]) -> dict[str, Any]:
    loc = ("payload",)
    stack: list[tuple[Any, int]] = [(payload, 0)]
    while stack:  # iterative: a deeply nested payload must not blow the Python stack
        node, depth = stack.pop()
        if depth > _MAX_PAYLOAD_DEPTH:
            raise _fail("payload_too_deep", "Payload is nested too deeply.", loc)
        if isinstance(node, dict):
            for key, child in node.items():
                _no_nul(key)
                stack.append((child, depth + 1))
        elif isinstance(node, list):
            stack.extend((child, depth + 1) for child in node)
        elif isinstance(node, str):
            _no_nul(node)
    try:
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise _fail("payload_not_json", "Payload must be plain JSON.", loc) from None
    if len(encoded.encode("utf-8")) > limits.MAX_INLINE_PAYLOAD_BYTES:
        raise _fail("payload_too_large", "Inline payload is too large; use payload_ref.", loc)
    return payload


class _Envelope(BaseModel):
    """Fields common to what clients send and what the server stores."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    schema_version: SchemaVersionStr
    event_id: EventId
    run_id: RunId
    trace_id: TraceId
    span_id: SpanId | None = None
    parent_span_id: SpanId | None = None
    agent_id: AgentSlug
    agent_version: Name128 | None = None
    event_type: EventTypeName
    occurred_at: UtcDatetime
    sequence: Annotated[int, Field(ge=0, le=limits.MAX_SAFE_INTEGER)] | None = None
    status: EventStatus | None = None
    duration_ms: (
        Annotated[float, Field(ge=0, le=limits.MAX_SAFE_INTEGER, allow_inf_nan=False)] | None
    ) = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] | None = None
    payload_ref: PayloadRef | None = None
    tags: list[Tag] = Field(default_factory=list, max_length=limits.MAX_TAGS)
    sdk: SdkInfo | None = None

    @field_validator("schema_version")
    @classmethod
    def _supported_version(cls, value: str) -> str:
        if int(value.split(".")[0]) != SUPPORTED_MAJOR:
            raise _fail("schema_version_unsupported", "Unsupported schema major version.")
        return value

    @field_validator("attributes")
    @classmethod
    def _attributes(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _check_attributes(value)

    @field_validator("payload")
    @classmethod
    def _payload(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if value is None else _check_payload(value)

    @model_validator(mode="after")
    def _cross_field_rules(self) -> "_Envelope":
        if self.parent_span_id is not None:
            if self.span_id is None:
                raise _fail(
                    "parent_without_span", "parent_span_id requires span_id.", ("parent_span_id",)
                )
            if self.parent_span_id == self.span_id:
                raise _fail(
                    "span_is_own_parent", "A span cannot be its own parent.", ("parent_span_id",)
                )
        spec = lookup(self.event_type)
        if spec is None:
            return self  # unknown but well-formed: accepted for forward compatibility
        if spec.span_role in (SpanRole.OPEN, SpanRole.CLOSE) and self.span_id is None:
            raise _fail("span_id_required", f"{self.event_type} requires span_id.", ("span_id",))
        for key in sorted(spec.required_attributes):
            if key not in self.attributes:
                raise _fail(
                    "attribute_required",
                    f"{self.event_type} requires attribute {key}.",
                    ("attributes", key),
                )
        if self.event_type == "span.started":
            kinds = {k.value for k in SpanKind}
            if self.attributes.get("span.kind") not in kinds:
                raise _fail(
                    "span_kind_invalid",
                    "span.kind is not a known span kind.",
                    ("attributes", "span.kind"),
                )
        return self

    def to_wire(self) -> dict[str, Any]:
        """JSON-ready dict with unset optional fields omitted."""
        return self.model_dump(mode="json", exclude_none=True)


class EventIn(_Envelope):
    workspace_id: WorkspaceId | None = None
    project_id: ProjectId | None = None


class Event(_Envelope):
    workspace_id: WorkspaceId
    project_id: ProjectId
    received_at: UtcDatetime


def finalize(event: EventIn, *, workspace_id: str, project_id: str, received_at: datetime) -> Event:
    """Bind a client event to the tenant resolved from its API key.

    A client may not claim another tenant: a mismatching id is rejected, never overwritten
    silently (INV-3).
    """
    issues: list[Issue] = []
    if event.workspace_id is not None and event.workspace_id != workspace_id:
        issues.append(Issue(("workspace_id",), "tenant_mismatch", "workspace_id does not match."))
    if event.project_id is not None and event.project_id != project_id:
        issues.append(Issue(("project_id",), "tenant_mismatch", "project_id does not match."))
    if issues:
        raise EventValidationError(
            ErrorCode.EVENT_INVALID, "Event does not belong to the authenticated project.", issues
        )
    data = {k: v for k, v in event.__dict__.items()}
    data.update(workspace_id=workspace_id, project_id=project_id, received_at=received_at)
    return Event.model_validate(data)


__all__ = ["SCHEMA_VERSION", "Event", "EventIn", "SdkInfo", "finalize"]

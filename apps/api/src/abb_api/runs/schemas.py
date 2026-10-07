"""HTTP shapes for the run query API. Internal rows never leave the repository layer."""

from datetime import datetime
from typing import Any, Literal

from abb_event_schema.ids import IdKind, id_pattern
from pydantic import BaseModel, Field, field_validator

MAX_METADATA_BYTES = 16 * 1024
_MAX_METADATA_DEPTH = 8

RunStatusName = Literal[
    "QUEUED",
    "RUNNING",
    "WAITING",
    "WAITING_FOR_APPROVAL",
    "SUCCESS",
    "FAILED",
    "CANCELLED",
    "TIMED_OUT",
    "BLOCKED",
]


class RunOut(BaseModel):
    id: str
    project_id: str
    name: str | None
    status: RunStatusName
    agent_id: str | None = Field(description="Agent slug of the run's earliest event.")
    trace_id: str
    started_at: datetime
    completed_at: datetime | None
    duration_ms: float | None
    ordering_mode: Literal["sequence", "time"]
    summary: dict[str, Any] = Field(
        description="Derived from events; see docs/architecture/api-v1.md."
    )
    summary_version: int
    summary_state: Literal["current", "processing"] = Field(
        description="`processing` while events are waiting to be folded into the summary."
    )
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class RunPage(BaseModel):
    items: list[RunOut]
    next_cursor: str | None


class CreateRunRequest(BaseModel):
    run_id: str | None = Field(default=None, pattern=id_pattern(IdKind.RUN))
    trace_id: str | None = Field(default=None, pattern=id_pattern(IdKind.TRACE))
    name: str | None = Field(default=None, max_length=256)
    agent_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _no_nul_in_name(cls, value: str | None) -> str | None:
        if value is not None and "\x00" in value:
            raise ValueError("name must not contain NUL characters")
        return value

    @field_validator("metadata")
    @classmethod
    def _bounded_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        stack: list[tuple[Any, int]] = [(value, 0)]
        while stack:  # iterative, so deep nesting cannot exhaust the stack
            node, depth = stack.pop()
            if depth > _MAX_METADATA_DEPTH:
                raise ValueError("metadata is nested too deeply")
            if isinstance(node, dict):
                for key, child in node.items():
                    if "\x00" in key:
                        raise ValueError("metadata must not contain NUL characters")
                    stack.append((child, depth + 1))
            elif isinstance(node, list):
                stack.extend((child, depth + 1) for child in node)
            elif isinstance(node, str) and "\x00" in node:
                raise ValueError("metadata must not contain NUL characters")
        import json

        if len(json.dumps(value, separators=(",", ":")).encode()) > MAX_METADATA_BYTES:
            raise ValueError(f"metadata exceeds {MAX_METADATA_BYTES} bytes")
        return value


class EventOut(BaseModel):
    """A stored event as returned by the API. `payload` is only present on the detail endpoint."""

    schema_version: str
    event_id: str
    workspace_id: str
    project_id: str
    run_id: str
    trace_id: str
    span_id: str | None = None
    parent_span_id: str | None = None
    agent_id: str
    agent_version: str | None = None
    event_type: str
    occurred_at: datetime
    received_at: datetime
    sequence: int | None = None
    status: str | None = None
    duration_ms: float | None = None
    attributes: dict[str, Any]
    payload: dict[str, Any] | None = Field(
        default=None,
        description="Inline payload; omitted from lists (`has_payload` says if present).",
    )
    payload_ref: str | None = None
    tags: list[str]
    sdk: dict[str, Any] | None = None
    has_payload: bool


class EventPage(BaseModel):
    items: list[EventOut]
    next_cursor: str | None
    ordering_mode: Literal["sequence", "time"]


class SpanOut(BaseModel):
    id: str
    run_id: str
    trace_id: str
    parent_span_id: str | None
    name: str | None
    kind: str | None
    agent_id: str
    status: str | None
    started_at: datetime | None
    ended_at: datetime | None
    duration_ms: float | None
    event_count: int


class SpanPage(BaseModel):
    items: list[SpanOut]
    next_cursor: str | None

"""HTTP shapes for the run query API. Internal rows never leave the repository layer."""

from datetime import datetime
from typing import Any, Literal

from abb_event_schema.ids import IdKind, id_pattern
from pydantic import BaseModel, Field, field_validator, model_serializer

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


class MatchedOut(BaseModel):
    tools: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    event_types: list[str] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    metadata_truncated: bool = Field(
        description="The run's metadata index hit its bounds; some values may not be searchable."
    )
    content_truncated: bool | None = Field(
        default=None,
        description="Same for captured content. Absent unless the caller holds `payload.read`.",
    )

    @model_serializer(mode="wrap")
    def _omit_unknown_content_flag(self, handler: Any) -> dict[str, Any]:
        # The flag is a volume bit about content: for a caller who may not read content the key
        # does not exist at all, rather than being null (ADR-101).
        data: dict[str, Any] = handler(self)
        if self.content_truncated is None:
            data.pop("content_truncated", None)
        return data


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
    summary_state: Literal["current", "processing", "failed"] = Field(
        description=(
            "`processing` while events wait to be folded into the summary; `failed` if "
            "summarization was dead-lettered and needs an operator (`jobs-retry`)."
        )
    )
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    # Appended after the existing fields (Phase 16). Phase 10 adds `findings` right after
    # `updated_at`: on merge, keep `findings` first and this block after it.
    matched: MatchedOut | None = Field(
        default=None,
        description="Why the run matched a search: the indexed values the query named. Only "
        "present when `q` named at least one indexed field or word.",
    )


class QueryTermOut(BaseModel):
    """One parsed term of `q`, echoed so the UI renders chips and removes a term exactly."""

    kind: Literal["field", "text"]
    field: str | None = Field(description="Canonical field name (field terms).")
    op: str | None = Field(description="`=`, `>=`, `<=`, `>`, `<` or `between` (field terms).")
    value: str | None = Field(description="Normalised value; times are resolved to instants.")
    value_end: str | None = Field(description="End of a `between` range (a whole UTC day).")
    lexemes: list[str] | None = Field(description="Normalised words (text terms).")
    phrase: bool
    prefix: bool
    negated: bool
    span: tuple[int, int] = Field(description="[start, end) character offsets within `q`.")


class QueryFieldOut(BaseModel):
    name: str
    kind: str
    description: str
    ordered: bool
    prefix: bool


class QueryOut(BaseModel):
    terms: list[QueryTermOut]
    fields: list[QueryFieldOut] = Field(
        description="The fields this caller may search (content fields only with `payload.read`)."
    )
    content_allowed: bool
    unindexed: int | None = Field(
        description="Runs in scope with no search document yet (they match no indexed term); "
        "null when the query does not use the index."
    )
    unindexed_capped: bool = Field(description="True when `unindexed` stopped counting at its cap.")


class FacetValueOut(BaseModel):
    value: str
    count: int


class FacetsOut(BaseModel):
    """Counts over the newest 1,000 matching runs, metadata only (never content)."""

    status: list[FacetValueOut]
    agents: list[FacetValueOut]
    tools: list[FacetValueOut]
    models: list[FacetValueOut]
    event_types: list[FacetValueOut]


class RunPage(BaseModel):
    items: list[RunOut]
    next_cursor: str | None
    # Present whenever `q` is sent (Phase 16); additive.
    query: QueryOut | None = None
    facets: FacetsOut | None = None
    facets_skipped: Literal["budget"] | None = Field(
        default=None, description="`budget` when the facets were left out to keep the time budget."
    )


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
    payload_withheld: bool = Field(
        default=False,
        description="True when the caller's role lacks `payload.read`: `payload` is then null "
        "although `has_payload` may be true. Never set on lists.",
    )
    withheld_attributes: list[str] = Field(
        default_factory=list,
        description="Attribute keys whose value is replaced by a marker because the caller's role "
        "lacks `payload.read` (shell command text, file paths and similar content). Empty for "
        "actors that hold it.",
    )


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
    name_withheld: bool = Field(
        default=False,
        description="True when `name` is null because it would show a command or a path and the "
        "caller's role lacks `payload.read`.",
    )
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

"""Registry of known event types and attribute keys (INV-6: agent concepts are typed).

Well-formed event types that are not listed here are still accepted (forward compatibility,
spec §64.5); they simply get no per-type required attributes. Attribute keys that *are* listed
must have the declared type wherever they appear, so `llm.model` means the same thing in a
custom event as in an `llm.request.completed` event.
"""

import re
from dataclasses import dataclass
from enum import Enum

from abb_event_schema.enums import EventClass, Priority, SpanKind

EVENT_TYPE_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){1,3}$"
_EVENT_TYPE_RE = re.compile(EVENT_TYPE_PATTERN)
MAX_EVENT_TYPE_LENGTH = 64

ATTRIBUTE_KEY_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$"


def is_valid_event_type_name(value: str) -> bool:
    return len(value) <= MAX_EVENT_TYPE_LENGTH and bool(_EVENT_TYPE_RE.fullmatch(value))


class SpanRole(str, Enum):
    """How an event relates to a span (spec §16)."""

    OPEN = "open"  # *.started: creates the span
    CLOSE = "close"  # *.completed / *.failed: ends the span
    POINT = "point"  # instantaneous; may reference a span


class AttrType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    STRING_LIST = "string_list"


@dataclass(frozen=True)
class AttrSpec:
    type: AttrType
    minimum: float | None = None


KNOWN_ATTRIBUTES: dict[str, AttrSpec] = {
    # run / agent / span
    "run.name": AttrSpec(AttrType.STRING),
    "agent.state": AttrSpec(AttrType.STRING),
    "agent.child_id": AttrSpec(AttrType.STRING),
    "span.name": AttrSpec(AttrType.STRING),
    "span.kind": AttrSpec(AttrType.STRING),
    # llm and cost (spec §150)
    "llm.provider": AttrSpec(AttrType.STRING),
    "llm.model": AttrSpec(AttrType.STRING),
    "llm.input_tokens": AttrSpec(AttrType.INTEGER, 0),
    "llm.output_tokens": AttrSpec(AttrType.INTEGER, 0),
    "llm.cached_input_tokens": AttrSpec(AttrType.INTEGER, 0),
    "llm.latency_ms": AttrSpec(AttrType.NUMBER, 0),
    "llm.temperature": AttrSpec(AttrType.NUMBER, 0),
    "llm.max_tokens": AttrSpec(AttrType.INTEGER, 0),
    "llm.error_type": AttrSpec(AttrType.STRING),
    "cost.estimated_usd": AttrSpec(AttrType.NUMBER, 0),
    "cost.pricing_version": AttrSpec(AttrType.STRING),
    # tools
    "tool.name": AttrSpec(AttrType.STRING),
    "tool.operation": AttrSpec(AttrType.STRING),
    "tool.latency_ms": AttrSpec(AttrType.NUMBER, 0),
    "tool.result_count": AttrSpec(AttrType.INTEGER, 0),
    "tool.error_type": AttrSpec(AttrType.STRING),
    # files and git
    "file.path": AttrSpec(AttrType.STRING),
    "file.size_before": AttrSpec(AttrType.INTEGER, 0),
    "file.size_after": AttrSpec(AttrType.INTEGER, 0),
    "file.hash_before": AttrSpec(AttrType.STRING),
    "file.hash_after": AttrSpec(AttrType.STRING),
    "file.language": AttrSpec(AttrType.STRING),
    "file.lines_added": AttrSpec(AttrType.INTEGER, 0),
    "file.lines_removed": AttrSpec(AttrType.INTEGER, 0),
    "file.operation": AttrSpec(AttrType.STRING),
    # Artifact reference `artifact://<art_id>` (ADR-030/031); content is in the store.
    "diff.artifact": AttrSpec(AttrType.STRING),
    "diff.withheld": AttrSpec(AttrType.STRING),  # sensitive_path | too_large
    "git.repo": AttrSpec(AttrType.STRING),
    "git.branch": AttrSpec(AttrType.STRING),
    "git.base_commit": AttrSpec(AttrType.STRING),
    "git.head_commit": AttrSpec(AttrType.STRING),
    "git.commit_hash": AttrSpec(AttrType.STRING),
    "git.changed_files": AttrSpec(AttrType.INTEGER, 0),
    "git.push_target": AttrSpec(AttrType.STRING),
    "git.pr_number": AttrSpec(AttrType.INTEGER, 0),
    "git.diff_stat_files": AttrSpec(AttrType.INTEGER, 0),
    # shell and tests (spec §82.3, §83)
    "shell.command": AttrSpec(AttrType.STRING),
    "shell.cwd": AttrSpec(AttrType.STRING),
    "shell.exit_code": AttrSpec(AttrType.INTEGER),
    "shell.duration_ms": AttrSpec(AttrType.NUMBER, 0),
    "shell.risk_class": AttrSpec(AttrType.STRING),
    "shell.category": AttrSpec(AttrType.STRING),
    "shell.stdout_artifact": AttrSpec(AttrType.STRING),
    "shell.stderr_artifact": AttrSpec(AttrType.STRING),
    "shell.stdout_bytes": AttrSpec(AttrType.INTEGER, 0),
    "shell.stderr_bytes": AttrSpec(AttrType.INTEGER, 0),
    "shell.output_truncated": AttrSpec(AttrType.BOOLEAN),
    "shell.output_withheld": AttrSpec(AttrType.STRING),  # sensitive_path
    "test.framework": AttrSpec(AttrType.STRING),
    "test.suite": AttrSpec(AttrType.STRING),
    "test.total": AttrSpec(AttrType.INTEGER, 0),
    "test.passed": AttrSpec(AttrType.INTEGER, 0),
    "test.failed": AttrSpec(AttrType.INTEGER, 0),
    "test.skipped": AttrSpec(AttrType.INTEGER, 0),
    "test.failing": AttrSpec(AttrType.STRING_LIST),
    # database and network
    "db.system": AttrSpec(AttrType.STRING),
    "db.operation": AttrSpec(AttrType.STRING),
    "db.rows_returned": AttrSpec(AttrType.INTEGER, 0),
    "db.latency_ms": AttrSpec(AttrType.NUMBER, 0),
    "http.method": AttrSpec(AttrType.STRING),
    "http.url": AttrSpec(AttrType.STRING),
    "http.status_code": AttrSpec(AttrType.INTEGER, 100),
    "http.latency_ms": AttrSpec(AttrType.NUMBER, 0),
    # reliability
    "retry.attempt": AttrSpec(AttrType.INTEGER, 1),
    "retry.reason": AttrSpec(AttrType.STRING),
    "retry.delay_ms": AttrSpec(AttrType.NUMBER, 0),
    "retry.of_event_id": AttrSpec(AttrType.STRING),
    "timeout.operation": AttrSpec(AttrType.STRING),
    "timeout.limit_ms": AttrSpec(AttrType.NUMBER, 0),
    "loop.pattern": AttrSpec(AttrType.STRING_LIST),
    "loop.repetitions": AttrSpec(AttrType.INTEGER, 0),
    "rate_limit.retry_after_ms": AttrSpec(AttrType.NUMBER, 0),
    # security and approvals
    "policy.id": AttrSpec(AttrType.STRING),
    "policy.capability": AttrSpec(AttrType.STRING),
    "policy.reason": AttrSpec(AttrType.STRING),
    "secret.kind": AttrSpec(AttrType.STRING),
    "approval.id": AttrSpec(AttrType.STRING),
    "approval.capability": AttrSpec(AttrType.STRING),
}


@dataclass(frozen=True)
class EventTypeSpec:
    event_type: str
    event_class: EventClass
    priority: Priority
    span_role: SpanRole
    span_kind: SpanKind | None = None
    required_attributes: frozenset[str] = frozenset()


def _spec(
    event_type: str,
    event_class: EventClass,
    priority: Priority,
    role: SpanRole,
    kind: SpanKind | None = None,
    required: tuple[str, ...] = (),
) -> EventTypeSpec:
    return EventTypeSpec(event_type, event_class, priority, role, kind, frozenset(required))


_C, _P0, _P1, _P2 = EventClass, Priority.P0, Priority.P1, Priority.P2
_OPEN, _CLOSE, _POINT = SpanRole.OPEN, SpanRole.CLOSE, SpanRole.POINT
_K = SpanKind

_SPECS: tuple[EventTypeSpec, ...] = (
    # run lifecycle
    _spec("run.started", _C.RUN, _P0, _POINT),
    _spec("run.completed", _C.RUN, _P0, _POINT),
    _spec("run.failed", _C.RUN, _P0, _POINT),
    _spec("run.cancelled", _C.RUN, _P0, _POINT),
    # agent lifecycle
    _spec("agent.started", _C.AGENT, _P0, _OPEN, _K.AGENT),
    _spec("agent.completed", _C.AGENT, _P0, _CLOSE, _K.AGENT),
    _spec("agent.state_changed", _C.AGENT, _P2, _POINT, None, ("agent.state",)),
    _spec("agent.spawned", _C.AGENT, _P0, _POINT, None, ("agent.child_id",)),
    # llm
    _spec("llm.request.started", _C.LLM, _P1, _OPEN, _K.LLM, ("llm.provider", "llm.model")),
    _spec("llm.request.completed", _C.LLM, _P1, _CLOSE, _K.LLM, ("llm.provider", "llm.model")),
    _spec("llm.request.failed", _C.LLM, _P0, _CLOSE, _K.LLM, ("llm.provider", "llm.model")),
    # tools
    _spec("tool.call.started", _C.TOOL, _P1, _OPEN, _K.TOOL, ("tool.name",)),
    _spec("tool.call.completed", _C.TOOL, _P1, _CLOSE, _K.TOOL, ("tool.name",)),
    _spec("tool.call.failed", _C.TOOL, _P0, _CLOSE, _K.TOOL, ("tool.name",)),
    # files
    _spec("file.read", _C.FILE, _P1, _POINT, None, ("file.path",)),
    _spec("file.created", _C.FILE, _P1, _POINT, None, ("file.path",)),
    _spec("file.modified", _C.FILE, _P1, _POINT, None, ("file.path",)),
    _spec("file.deleted", _C.FILE, _P1, _POINT, None, ("file.path",)),
    # git
    _spec("git.diff", _C.GIT, _P1, _POINT),
    _spec("git.commit", _C.GIT, _P1, _POINT),
    _spec("git.branch_created", _C.GIT, _P1, _POINT),
    _spec("git.push", _C.GIT, _P1, _POINT),
    # shell
    _spec("shell.command.started", _C.SHELL, _P1, _OPEN, _K.SHELL, ("shell.command",)),
    _spec("shell.command.completed", _C.SHELL, _P1, _CLOSE, _K.SHELL, ("shell.command",)),
    _spec("shell.command.failed", _C.SHELL, _P0, _CLOSE, _K.SHELL, ("shell.command",)),
    # database
    _spec("db.query.started", _C.DATABASE, _P1, _OPEN, _K.DB),
    _spec("db.query.completed", _C.DATABASE, _P1, _CLOSE, _K.DB),
    _spec("db.query.failed", _C.DATABASE, _P0, _CLOSE, _K.DB),
    # network
    _spec("http.request", _C.NETWORK, _P2, _POINT, None, ("http.method",)),
    _spec("http.response", _C.NETWORK, _P2, _POINT, None, ("http.status_code",)),
    # reliability
    _spec("retry.attempted", _C.RELIABILITY, _P0, _POINT, None, ("retry.attempt",)),
    _spec("timeout.occurred", _C.RELIABILITY, _P0, _POINT),
    _spec("loop.detected", _C.RELIABILITY, _P0, _POINT),
    _spec("rate_limit.hit", _C.RELIABILITY, _P1, _POINT),
    # security and policy
    _spec("policy.warning", _C.SECURITY, _P0, _POINT),
    _spec("policy.action.blocked", _C.SECURITY, _P0, _POINT, None, ("policy.id",)),
    _spec("secret.detected", _C.SECURITY, _P0, _POINT),
    # approvals
    _spec("approval.requested", _C.APPROVAL, _P0, _POINT, None, ("approval.id",)),
    _spec("approval.granted", _C.APPROVAL, _P0, _POINT, None, ("approval.id",)),
    _spec("approval.denied", _C.APPROVAL, _P0, _POINT, None, ("approval.id",)),
    # generic spans for custom instrumentation (D6)
    _spec("span.started", _C.SPAN, _P1, _OPEN, _K.CUSTOM, ("span.name", "span.kind")),
    _spec("span.completed", _C.SPAN, _P1, _CLOSE, _K.CUSTOM),
    _spec("span.failed", _C.SPAN, _P0, _CLOSE, _K.CUSTOM),
)

EVENT_TYPES: dict[str, EventTypeSpec] = {s.event_type: s for s in _SPECS}

CUSTOM_PREFIX = "custom."
_CUSTOM_SPEC_PRIORITY = Priority.P1


def lookup(event_type: str) -> EventTypeSpec | None:
    """Spec for a registered type, a generic one for `custom.*`, or None if unknown."""
    known = EVENT_TYPES.get(event_type)
    if known is not None:
        return known
    if event_type.startswith(CUSTOM_PREFIX):
        return EventTypeSpec(event_type, EventClass.CUSTOM, _CUSTOM_SPEC_PRIORITY, SpanRole.POINT)
    return None


def classify(event_type: str) -> EventClass:
    spec = lookup(event_type)
    return spec.event_class if spec else EventClass.CUSTOM

"""Steps: what the agent did, derived in canonical order from the actor's own projection (D6).

A step is one closing lifecycle event (tool, llm, shell, db, span, agent) or one point event of the
file, git, reliability, security and approval families. `*.started`, `run.*`, `http.*` and
`custom.*` are not steps. The alignment key and the label are built from the projection the caller
may see: with `payload.read` the key holds the normalised name (command text, path, tool, model,
span name); without it the key holds tool, model, span kind and agent only, so the response can
never act as an equality oracle on withheld content.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from abb_event_schema.event import Event

from abb_api.comparison.normalise import normalise

LIFECYCLE_FAMILIES = frozenset({"tool", "llm", "shell", "db", "span", "agent"})
POINT_FAMILIES = frozenset(
    {"file", "git", "retry", "timeout", "loop", "rate_limit", "policy", "secret", "approval"}
)
# Families whose name is command text or a path: hidden from the metadata projection.
CONTENT_FAMILIES = frozenset({"shell", "file", "git"})
MAX_LABEL = 200
MAX_PREFIXES = 8

Key = tuple[str, str | None]


@dataclass(frozen=True)
class Step:
    event_id: str
    span_id: str | None
    family: str
    event_type: str
    status: str | None
    duration_ms: float | None
    offset_ms: float | None
    key: Key
    label: str | None
    label_withheld: bool


def family_of(event_type: str) -> str:
    return event_type.split(".", 1)[0]


def is_step(event_type: str) -> bool:
    """Whether an event of this type is a step (shared with the judge's prompt)."""
    family = family_of(event_type)
    if family in LIFECYCLE_FAMILIES:
        return event_type.endswith((".completed", ".failed"))
    return family in POINT_FAMILIES


def workdir_prefixes(events: Sequence[Event]) -> list[str]:
    """The run's working directories, from `shell.cwd` (a few distinct values at most)."""
    found: list[str] = []
    for event in events:
        cwd = event.attributes.get("shell.cwd")
        if isinstance(cwd, str) and len(cwd) > 1 and cwd not in found:
            found.append(cwd[:512])
            if len(found) >= MAX_PREFIXES:
                break
    return found


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _name(
    event: Event, family: str, openers: dict[str, Event], *, content: bool
) -> tuple[str | None, bool]:
    """(display name, withheld) for the actor's projection."""
    attrs = event.attributes
    if family == "tool":
        return _text(attrs.get("tool.name")), False
    if family == "llm":
        return _text(attrs.get("llm.model")), False
    if family == "agent":
        return event.agent_id, False
    if family == "span":
        opener = openers.get(event.span_id or "")
        source = opener.attributes if opener is not None else {}
        return _text(source.get("span.name" if content else "span.kind")), False
    if family in CONTENT_FAMILIES:
        if not content:
            return None, True
        key = {"shell": "shell.command", "file": "file.path", "git": "git.branch"}[family]
        return _text(attrs.get(key)), False
    return None, False


def extract_steps(
    events: Sequence[Event], *, started_at: datetime, content: bool, limit: int
) -> tuple[list[Step], str | None]:
    """(steps, truncated_at_event). `events` are in canonical order; at most `limit` steps are
    kept and `truncated_at_event` is the last kept step's event id when more existed."""
    openers = {
        e.span_id: e for e in events if e.span_id is not None and e.event_type == "span.started"
    }
    prefixes = workdir_prefixes(events) if content else []
    steps: list[Step] = []
    for event in events:
        if not is_step(event.event_type):
            continue
        if len(steps) >= limit:
            return steps, steps[-1].event_id if steps else None
        family = family_of(event.event_type)
        name, withheld = _name(event, family, openers, content=content)
        norm_name: str | None = name
        if content and name is not None:
            norm_name = normalise(name, prefixes)
            if family == "file":
                norm_name = norm_name.lstrip("/")
        lead = family if family in LIFECYCLE_FAMILIES else event.event_type
        status = getattr(event.status, "value", event.status)
        steps.append(
            Step(
                event_id=event.event_id,
                span_id=event.span_id,
                family=family,
                event_type=event.event_type,
                status=status,
                duration_ms=event.duration_ms,
                offset_ms=round((event.occurred_at - started_at).total_seconds() * 1000, 3),
                key=(lead, norm_name),
                label=name[:MAX_LABEL] if name is not None else None,
                label_withheld=withheld,
            )
        )
    return steps, None

"""The field catalogue: data, not code paths (plan D12).

Each row names the storage column the compiler targets and whether the field is **content**
(`needs_content`): searchable only by an actor who holds `payload.read` (ADR-101). Adding a field
is one row here, one document line and one test row.
"""

from dataclasses import dataclass
from enum import StrEnum


class Kind(StrEnum):
    ENUM = "enum"
    SLUG = "slug"
    TEXT = "text"
    DECIMAL = "decimal"
    INT = "int"
    DURATION = "duration"
    TIME = "time"
    NAME = "name"
    EVENT = "event"
    BOOL = "bool"
    GLOB = "glob"
    SEVERITY = "severity"


@dataclass(frozen=True)
class Field:
    name: str
    kind: Kind
    column: str  # documentation of the target; the compiler owns the SQL
    needs_content: bool = False
    ordered: bool = False  # accepts >=, <=, >, <
    prefix: bool = False  # accepts a trailing `*`
    description: str = ""


_CATALOGUE = (
    Field("status", Kind.ENUM, "runs.status", description="run status"),
    Field("agent", Kind.SLUG, "run_search.agents", prefix=True, description="any agent of the run"),
    Field("root_agent", Kind.SLUG, "runs.agent_slug", prefix=True, description="the first agent"),
    Field("name", Kind.TEXT, "runs.name", description="run name contains"),
    Field("cost", Kind.DECIMAL, "runs.cost_usd", ordered=True, description="estimated cost, USD"),
    Field("retries", Kind.INT, "runs.retry_count", ordered=True, description="retry count"),
    Field("errors", Kind.INT, "run_search.error_count", ordered=True, description="error events"),
    Field("severity", Kind.SEVERITY, "run_search.error_count", description="severity:error"),
    Field("duration", Kind.DURATION, "runs.duration_ms", ordered=True, description="e.g. 60s"),
    Field("started", Kind.TIME, "runs.started_at", ordered=True, description="date or -24h"),
    Field("event", Kind.EVENT, "run_search.event_types", prefix=True, description="event type"),
    Field("tool", Kind.NAME, "run_search.tools", prefix=True, description="tool name"),
    Field("model", Kind.NAME, "run_search.models", prefix=True, description="model name"),
    Field("provider", Kind.NAME, "run_search.providers", prefix=True, description="provider"),
    Field("tag", Kind.NAME, "run_search.tags", prefix=True, description="tag"),
    Field("security", Kind.BOOL, "run_search.security_events", description="security events"),
    Field("file", Kind.GLOB, "run_search.file_paths", needs_content=True, description="file path"),
    Field(
        "command", Kind.TEXT, "run_search.commands", needs_content=True, description="command line"
    ),
)

CATALOGUE: dict[str, Field] = {f.name: f for f in _CATALOGUE}

# Named in the grammar for later phases; refused today exactly like an unknown field.
RESERVED = frozenset({"finding", "verdict"})

OPERATORS = (">=", "<=", ">", "<")


def content_fields() -> frozenset[str]:
    return frozenset(f.name for f in _CATALOGUE if f.needs_content)

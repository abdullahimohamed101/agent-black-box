"""Read-time projection that withholds captured content from actors without `payload.read`.

Shell command text and file paths are content (ADR-061, review F3): a command line routinely holds
tokens and hostnames, a path holds user and project names. Stored events and derived rows stay
as ingested (INV-1, INV-2); only the response differs per actor. What remains visible is metadata:
kinds, exit codes, durations, risk classes, line counts, languages and hashes.
"""

from typing import Any

from abb_event_schema.registry import CONTENT_ATTRIBUTES

WITHHELD = "[withheld]"

# Unregistered attributes are kept untouched at ingestion, so an integration can send a command
# under a name this API has never seen. Judge those by their last segment, conservatively.
_CONTENT_LEAVES = frozenset(
    {"command", "cmd", "argv", "args", "path", "paths", "filename", "file", "cwd", "url", "uri"}
)

# Span kinds whose display name is built from content (`spans.py` falls back to the command or the
# path); a span with no opener has no kind and may be named by a point event's path.
_CONTENT_SPAN_KINDS = frozenset({None, "shell", "file", "git"})


def is_content_attribute(key: str) -> bool:
    return key in CONTENT_ATTRIBUTES or key.rsplit(".", 1)[-1] in _CONTENT_LEAVES


def withhold_attributes(attributes: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The attributes with content values replaced by a marker, and the keys that were replaced."""
    hidden = sorted(k for k in attributes if is_content_attribute(k))
    if not hidden:
        return attributes, []
    return {k: (WITHHELD if k in hidden else v) for k, v in attributes.items()}, hidden


def span_name_is_content(kind: str | None) -> bool:
    return kind in _CONTENT_SPAN_KINDS

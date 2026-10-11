"""Which captured values are content (ADR-061): shared by the read projection and by search.

Kept outside `runs/` and `search/` so neither imports the other: the withholding projection
(`runs/content.py`) and the search document builder (`search/`) must classify an attribute the
same way, from one place. A future server-side redaction phase can move individual attributes
between the metadata and content sides by changing the classification here only.
"""

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


def span_name_is_content(kind: str | None) -> bool:
    return kind in _CONTENT_SPAN_KINDS

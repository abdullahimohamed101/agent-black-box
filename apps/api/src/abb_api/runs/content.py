"""Read-time projection that withholds captured content from actors without `payload.read`.

Shell command text and file paths are content (ADR-061, review F3): a command line routinely holds
tokens and hostnames, a path holds user and project names. Stored events and derived rows stay
as ingested (INV-1, INV-2); only the response differs per actor. What remains visible is metadata:
kinds, exit codes, durations, risk classes, line counts, languages and hashes.

The classification itself lives in `abb_api/content.py` (shared with search).
"""

from typing import Any

from abb_api.content import WITHHELD, is_content_attribute, span_name_is_content

__all__ = ["WITHHELD", "is_content_attribute", "span_name_is_content", "withhold_attributes"]


def withhold_attributes(attributes: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The attributes with content values replaced by a marker, and the keys that were replaced."""
    hidden = sorted(k for k in attributes if is_content_attribute(k))
    if not hidden:
        return attributes, []
    return {k: (WITHHELD if k in hidden else v) for k, v in attributes.items()}, hidden

"""Idempotency helpers (spec §66, ADR-006).

Identity is `(workspace_id, event_id)`; the first accepted write wins (INV-1). The content hash
lets the server tell a harmless retry (same id, same content) from a conflict (same id, different
content) without ever overwriting history.
"""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from abb_event_schema.event import Event, EventIn

# Server-assigned or tenant-implied: they must not make a retry look like a different event.
_EXCLUDED_FROM_HASH = frozenset({"received_at", "workspace_id", "project_id"})


def canonical_json(event: Event | EventIn) -> bytes:
    wire = {k: v for k, v in event.to_wire().items() if k not in _EXCLUDED_FROM_HASH}
    return json.dumps(
        wire, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def content_hash(event: Event | EventIn) -> str:
    """SHA-256 over the canonical form: stable across key order and across EventIn/Event."""
    return hashlib.sha256(canonical_json(event)).hexdigest()


@dataclass
class DedupResult:
    unique: list[Any] = field(default_factory=list)
    duplicates: int = 0  # same id, same content: harmless retries
    conflicts: list[str] = field(default_factory=list)  # same id, different content


def dedupe(events: Iterable[Event | EventIn]) -> DedupResult:
    """Collapse repeated events within one batch, keeping the first occurrence in order.

    Identity is `(workspace_id, event_id)` (INV-3): the same event id in two workspaces is two
    different events, never a duplicate. Client events without a tenant yet share one namespace.
    """
    seen: dict[tuple[str | None, str], str] = {}
    result = DedupResult()
    for event in events:
        digest = content_hash(event)
        key = (event.workspace_id, event.event_id)
        previous = seen.get(key)
        if previous is None:
            seen[key] = digest
            result.unique.append(event)
        elif previous == digest:
            result.duplicates += 1
        else:
            result.conflicts.append(event.event_id)  # tenant is implied by the batch
    return result

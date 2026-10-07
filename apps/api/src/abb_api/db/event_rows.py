"""Mapping between canonical events and `events` rows (the only place that knows both shapes)."""

import uuid
from typing import Any

from abb_event_schema.dedup import content_hash
from abb_event_schema.event import Event
from abb_event_schema.ids import IdKind, from_uuid, to_uuid
from abb_event_schema.parse import parse_event


def event_to_row(event: Event) -> dict[str, Any]:
    """Column values for INSERT. Ids become uuids; the content hash is stored as bytes."""
    wire = event.to_wire()
    return {
        "workspace_id": to_uuid(event.workspace_id),
        "event_id": to_uuid(event.event_id),
        "project_id": to_uuid(event.project_id),
        "run_id": to_uuid(event.run_id),
        "trace_id": to_uuid(event.trace_id),
        "span_id": to_uuid(event.span_id) if event.span_id else None,
        "parent_span_id": to_uuid(event.parent_span_id) if event.parent_span_id else None,
        "agent_id": event.agent_id,
        "agent_version": event.agent_version,
        "event_type": event.event_type,
        "occurred_at": event.occurred_at,
        "received_at": event.received_at,
        "sequence": event.sequence,
        "status": event.status.value if event.status else None,
        "duration_ms": event.duration_ms,
        "attributes": event.attributes,
        "payload": event.payload,
        "payload_ref": event.payload_ref,
        "tags": list(event.tags),
        "schema_version": event.schema_version,
        "sdk": wire.get("sdk"),
        "content_hash": bytes.fromhex(content_hash(event)),
    }


def _pub(kind: IdKind, value: uuid.UUID | None) -> str | None:
    return from_uuid(kind, value) if value is not None else None


def row_to_event(row: Any) -> Event:
    """Rebuild the canonical event from a row (validated again: the database is not trusted)."""
    data: dict[str, Any] = {
        "schema_version": row.schema_version,
        "event_id": from_uuid(IdKind.EVENT, row.event_id),
        "workspace_id": from_uuid(IdKind.WORKSPACE, row.workspace_id),
        "project_id": from_uuid(IdKind.PROJECT, row.project_id),
        "run_id": from_uuid(IdKind.RUN, row.run_id),
        "trace_id": from_uuid(IdKind.TRACE, row.trace_id),
        "span_id": _pub(IdKind.SPAN, row.span_id),
        "parent_span_id": _pub(IdKind.SPAN, row.parent_span_id),
        "agent_id": row.agent_id,
        "agent_version": row.agent_version,
        "event_type": row.event_type,
        "occurred_at": row.occurred_at.isoformat(),
        "received_at": row.received_at.isoformat(),
        "sequence": row.sequence,
        "status": row.status,
        "duration_ms": row.duration_ms,
        "attributes": row.attributes,
        "payload": row.payload,
        "payload_ref": row.payload_ref,
        "tags": list(row.tags),
        "sdk": row.sdk,
    }
    return parse_event({k: v for k, v in data.items() if v is not None})

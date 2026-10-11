"""A `RunSnapshot` from the run row and a capped, canonically ordered event load (ADR-090)."""

from abb_event_schema.event import Event
from abb_event_schema.ids import IdKind, from_uuid
from abb_event_schema.ordering import sort_events
from abb_event_schema.spans import derive_spans

from abb_api.evaluations.protocol import RunSnapshot
from abb_api.runs.queries import RunRecord


def build_snapshot(record: RunRecord, loaded: list[Event]) -> RunSnapshot:
    """Events are ordered once here (never by arrival, spec §65); spans are derived from them."""
    events = tuple(sort_events(loaded))
    return RunSnapshot(
        run_id=from_uuid(IdKind.RUN, record.id),
        status=record.status,
        started_at=record.started_at,
        duration_ms=record.duration_ms,
        agent_slug=record.agent_slug,
        ordering_mode=record.ordering_mode,
        summary=record.summary,
        events=events,
        spans=derive_spans(events),
    )

"""Which model calls are retry cost (ADR-042). Pure; canonically ordered events and spans."""

from collections.abc import Mapping, Sequence

from abb_event_schema.event import Event
from abb_event_schema.spans import Span


def retry_call_ids(ordered: Sequence[Event], spans: Mapping[str, Span]) -> set[str]:
    """Event ids of `llm.request.completed` events that are retries.

    A call is a retry when a `retry.attempted` event whose `span_id` is S precedes it (canonical
    order) and the call's span is S or a descendant of S. Retries with no span id attribute
    nothing.
    """
    first_retry_at: dict[str, int] = {}  # scope span -> position of its first retry event
    for position, event in enumerate(ordered):
        if event.event_type == "retry.attempted" and event.span_id is not None:
            first_retry_at.setdefault(event.span_id, position)
    if not first_retry_at:
        return set()

    ancestry_cache: dict[str, tuple[str, ...]] = {}

    def lineage(span_id: str) -> tuple[str, ...]:
        cached = ancestry_cache.get(span_id)
        if cached is not None:
            return cached
        chain: list[str] = []
        seen: set[str] = set()
        node: str | None = span_id
        while node is not None and node not in seen:  # a cycle ends the walk
            seen.add(node)
            chain.append(node)
            span = spans.get(node)
            node = span.parent_span_id if span else None
        ancestry_cache[span_id] = tuple(chain)
        return ancestry_cache[span_id]

    retries: set[str] = set()
    for position, event in enumerate(ordered):
        if event.event_type != "llm.request.completed" or event.span_id is None:
            continue
        if any(first_retry_at.get(s, position) < position for s in lineage(event.span_id)):
            retries.add(event.event_id)
    return retries

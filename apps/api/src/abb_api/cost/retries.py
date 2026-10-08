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

    unretried = len(ordered)  # larger than any position: "no retry on this lineage"
    earliest: dict[str, int] = {}  # span -> earliest retry position on the span or an ancestor

    def retry_position(span_id: str) -> int:
        """Memoised and iterative: every span is visited once however deep the chain (O(n))."""
        if span_id in earliest:
            return earliest[span_id]
        path: list[str] = []
        on_path: set[str] = set()
        node: str | None = span_id
        inherited = unretried
        while node is not None:
            if node in earliest:
                inherited = earliest[node]
                break
            if node in on_path:  # a cycle ends the walk
                break
            on_path.add(node)
            path.append(node)
            span = spans.get(node)
            node = span.parent_span_id if span else None
        for visited in reversed(path):
            inherited = min(inherited, first_retry_at.get(visited, unretried))
            earliest[visited] = inherited
        return earliest[span_id]

    retries: set[str] = set()
    for position, event in enumerate(ordered):
        if event.event_type != "llm.request.completed" or event.span_id is None:
            continue
        if retry_position(event.span_id) < position:
            retries.add(event.event_id)
    return retries

"""Retry-cost attribution (ADR-042): scope spans, ordering, nesting, hostile ancestry."""

import uuid
from typing import Any

from abb_event_schema.event import Event
from abb_event_schema.ids import IdKind, new_id
from abb_event_schema.ordering import sort_events
from abb_event_schema.spans import derive_spans

from abb_api.cost.retries import retry_call_ids
from abb_api.tenancy import TenantContext
from tests.ingest_helpers import Tenant, build_event, make_run_ids

TENANT = Tenant(
    context=TenantContext(uuid.uuid4()),
    workspace_id="ws_01J9ZZZZZZZZZZZZZZZZZZZZZZ",
    projects={"p": "prj_01J9ZZZZZZZZZZZZZZZZZZZZZZ"},
    project_uuids={},
)
RUN = make_run_ids()
LLM: dict[str, Any] = {"llm.provider": "p", "llm.model": "m", "llm.input_tokens": 1}


class Trace:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def add(
        self,
        kind: str,
        *,
        span: str | None,
        parent: str | None = None,
        attrs: dict[str, Any] | None = None,
    ) -> str:
        extra: dict[str, Any] = {"span_id": span if span else ...}
        if parent:
            extra["parent_span_id"] = parent
        attributes = attrs or (
            {"llm.provider": "p", "llm.model": "m"} if kind.startswith("llm") else {}
        )
        event = build_event(
            TENANT, RUN, n=len(self.events) + 1, event_type=kind, attributes=attributes, **extra
        )
        self.events.append(event)
        return event.event_id

    def llm(self, span: str | None, parent: str | None = None) -> str:
        return self.add("llm.request.completed", span=span, parent=parent, attrs=LLM)

    def retry(self, span: str | None, attempt: int = 1) -> None:
        self.add("retry.attempted", span=span, attrs={"retry.attempt": attempt})

    def retries(self) -> set[str]:
        ordered = sort_events(self.events)
        return retry_call_ids(ordered, derive_spans(self.events))


def sid() -> str:
    return new_id(IdKind.SPAN)


def test_calls_after_a_scoped_retry_are_retry_cost() -> None:
    t, scope = Trace(), sid()
    first = t.llm(scope)
    t.retry(scope)
    second = t.llm(scope)
    assert t.retries() == {second} and first not in t.retries()


def test_descendants_of_the_scope_count_and_siblings_do_not() -> None:
    t, scope, child, other = Trace(), sid(), sid(), sid()
    t.add("tool.call.started", span=scope, attrs={"tool.name": "t"})
    t.retry(scope)
    inside = t.llm(child, parent=scope)
    outside = t.llm(other)
    assert t.retries() == {inside} and outside not in t.retries()


def test_retries_without_a_span_attribute_nothing() -> None:
    t = Trace()
    t.llm(sid())
    t.retry(None)
    t.llm(sid())
    assert t.retries() == set()


def test_only_calls_after_the_first_retry_of_a_scope_count() -> None:
    t, scope = Trace(), sid()
    before = t.llm(scope)
    t.retry(scope, 1)
    t.retry(scope, 2)
    after = t.llm(scope)
    result = t.retries()
    assert after in result and before not in result


def test_a_cyclic_ancestry_terminates() -> None:
    t, a, b = Trace(), sid(), sid()
    t.add("tool.call.started", span=a, parent=b, attrs={"tool.name": "t"})
    t.add("tool.call.started", span=b, parent=a, attrs={"tool.name": "t"})
    t.retry(a)
    call = t.llm(b, parent=a)
    assert call in t.retries()  # terminates and still finds the scope on the walk

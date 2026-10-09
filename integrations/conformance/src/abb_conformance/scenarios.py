"""The scenarios every adapter must pass and the canonical shape each must produce (spec 70)."""

import json
from collections.abc import Callable
from typing import Any, Protocol

from blackbox import BlackBox

from abb_conformance.harness import capture, validate_events

TOOL_NAME = "lookup"
LLM_INPUT, LLM_OUTPUT, LLM_CACHED = 120, 30, 20
# Built at runtime so no scanner sees a literal secret; both match the SDK's redaction patterns.
SECRET = "sk-ant-api03-" + "Zq9" * 8
PASSWORD_ASSIGNMENT = "password=" + "hunter2-conformance"
CONCURRENCY = 8


class ScenarioError(RuntimeError):
    """The failure a driver raises from inside the framework; adapters must let it propagate."""


class Driver(Protocol):
    """How one adapter runs the standard operations through its framework (real or faked).

    `perform` runs the scenario inside a run of `bb` (the adapter may create the run itself). It
    must raise `ScenarioError` out of the framework call for the `*_failure` scenarios and must not
    raise for any other scenario. `hostile` feeds malformed framework inputs and must not raise.
    """

    name: str
    supports: frozenset[str]
    llm_provider: str
    llm_model: str

    def perform(self, scenario: str, bb: BlackBox) -> None: ...


# scenario -> options for the client under test
_OPTIONS: dict[str, dict[str, Any]] = {
    "redactor_raises": {"redactor": lambda event: 1 / 0},
    # Payloads are allowed by the client here, so absence proves the adapter's own default-off gate.
    "sensitive": {"payload_mode": "full"},
}


def _spans(events: list[dict[str, Any]], prefix: str) -> dict[str, dict[str, dict[str, Any]]]:
    """span_id -> {"open": event, "close": event} for event types starting with `prefix`."""
    spans: dict[str, dict[str, dict[str, Any]]] = {}
    for e in events:
        if e["event_type"].startswith(prefix) and e.get("span_id"):
            role = "open" if e["event_type"].endswith(".started") else "close"
            spans.setdefault(e["span_id"], {})[role] = e
    return spans


def _structure(
    events: list[dict[str, Any]], *, payloads_allowed: bool = False, allow_unclosed: bool = False
) -> None:
    """Rules every scenario obeys: lifecycle, span pairing, parenting, ordering, no payloads."""
    assert events, "the adapter emitted nothing"
    validate_events(events)
    by_run: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        by_run.setdefault(e["run_id"], []).append(e)
        if not payloads_allowed:
            assert "payload" not in e and "payload_ref" not in e, "payloads are off by default"
    for run_id, items in by_run.items():
        # Queue order may differ from creation order across threads; `sequence` is the truth.
        items.sort(key=lambda e: e["sequence"])
        assert [e["sequence"] for e in items] == list(range(1, len(items) + 1)), "gaps in sequence"
        types = [e["event_type"] for e in items]
        assert types[0] == "run.started", f"{run_id}: first event is {types[0]}"
        ends = [t for t in types if t in ("run.completed", "run.failed", "run.cancelled")]
        assert len(ends) == 1 and types[-1] == ends[0], f"{run_id}: bad run end {types}"
        assert len({e["trace_id"] for e in items}) == 1
        opened: dict[str, int] = {}
        closed: set[str] = set()
        for e in items:
            span_id = e.get("span_id")
            if e["event_type"].endswith(".started") and span_id:
                assert span_id not in opened, f"span {span_id} started twice"
                opened[span_id] = e["sequence"]
                parent = e.get("parent_span_id")
                assert parent is None or parent in opened, "parent span not opened before child"
            elif span_id and e["event_type"].endswith((".completed", ".failed")):
                assert span_id in opened and span_id not in closed, f"span {span_id} closed badly"
                closed.add(span_id)
        if not allow_unclosed:
            assert closed == set(opened), f"unclosed spans: {set(opened) - closed}"


def _one(spans: dict[str, dict[str, dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    assert len(spans) == 1, f"expected exactly one span, got {len(spans)}"
    return next(iter(spans.values()))


def _tool(driver: Driver, events: list[dict[str, Any]]) -> None:
    span = _one(_spans(events, "tool.call."))
    assert span["open"]["attributes"]["tool.name"] == TOOL_NAME
    assert span["close"]["event_type"] == "tool.call.completed"
    assert span["close"]["status"] == "success"
    assert span["close"]["attributes"]["tool.name"] == TOOL_NAME
    assert isinstance(span["close"]["attributes"]["tool.latency_ms"], (int, float))


def _tool_failure(driver: Driver, events: list[dict[str, Any]]) -> None:
    span = _one(_spans(events, "tool.call."))
    assert span["close"]["event_type"] == "tool.call.failed"
    assert span["close"]["status"] == "error"
    assert span["close"]["attributes"]["error.type"] == "ScenarioError"
    assert span["close"]["attributes"]["tool.name"] == TOOL_NAME


def _llm(driver: Driver, events: list[dict[str, Any]]) -> None:
    span = _one(_spans(events, "llm.request."))
    assert span["open"]["attributes"]["llm.provider"] == driver.llm_provider
    assert span["open"]["attributes"]["llm.model"] == driver.llm_model
    done = span["close"]
    assert done["event_type"] == "llm.request.completed" and done["status"] == "success"
    attrs = done["attributes"]
    assert attrs["llm.input_tokens"] == LLM_INPUT
    assert attrs["llm.output_tokens"] == LLM_OUTPUT
    assert attrs["llm.cached_input_tokens"] == LLM_CACHED
    assert attrs["llm.provider"] == driver.llm_provider and attrs["llm.model"] == driver.llm_model


def _llm_failure(driver: Driver, events: list[dict[str, Any]]) -> None:
    span = _one(_spans(events, "llm.request."))
    assert span["close"]["event_type"] == "llm.request.failed"
    assert span["close"]["status"] == "error"
    assert span["close"]["attributes"]["error.type"] == "ScenarioError"
    assert span["open"]["attributes"]["llm.model"] == driver.llm_model


def _nested(driver: Driver, events: list[dict[str, Any]]) -> None:
    tool = _one(_spans(events, "tool.call."))
    llm = _one(_spans(events, "llm.request."))
    parents = {tool["open"].get("parent_span_id"), llm["open"].get("parent_span_id")}
    assert len(parents) == 1 and None not in parents, "tool and llm must share the enclosing step"
    (outer_id,) = parents
    outer = [e for e in events if e.get("span_id") == outer_id]
    assert len(outer) == 2, "the enclosing step must have its own started/closed pair"
    assert outer[0].get("parent_span_id") is None


def _concurrent(driver: Driver, events: list[dict[str, Any]]) -> None:
    # Tool-calling adapters run CONCURRENCY named tool calls; model-only wrappers run model calls.
    if getattr(driver, "concurrent_kind", "tool") == "llm":
        spans = _spans(events, "llm.request.")
        assert len(spans) == CONCURRENCY
        for span in spans.values():
            assert span["close"]["event_type"] == "llm.request.completed"
            assert span["close"]["attributes"]["llm.input_tokens"] == LLM_INPUT
        return
    spans = _spans(events, "tool.call.")
    assert len(spans) == CONCURRENCY
    names = set()
    for span in spans.values():
        name = span["open"]["attributes"]["tool.name"]
        assert span["close"]["attributes"]["tool.name"] == name, "start/close crossed between calls"
        assert span["close"]["event_type"] == "tool.call.completed"
        names.add(name)
    assert names == {f"{TOOL_NAME}-{i}" for i in range(CONCURRENCY)}


def _sensitive(driver: Driver, events: list[dict[str, Any]]) -> None:
    blob = json.dumps(events)
    assert SECRET not in blob and PASSWORD_ASSIGNMENT not in blob, "a secret reached an event"
    assert _spans(events, "tool.call.") or _spans(events, "llm.request.")


def _sensitive_full(driver: Driver, events: list[dict[str, Any]]) -> None:
    _sensitive(driver, events)
    assert any("payload" in e for e in events), "payload capture was requested but nothing captured"


def _nothing_more(driver: Driver, events: list[dict[str, Any]]) -> None:
    return None


_CHECKS: dict[str, Callable[[Driver, list[dict[str, Any]]], None]] = {
    "tool": _tool,
    "tool_failure": _tool_failure,
    "llm": _llm,
    "llm_failure": _llm_failure,
    "nested": _nested,
    "concurrent": _concurrent,
    "sensitive": _sensitive,
    "sensitive_full": _sensitive_full,
    "hostile": _nothing_more,
    "redactor_raises": _nothing_more,
    "late_end": _nothing_more,
}
_FAILING = {"tool_failure", "llm_failure"}
SCENARIOS: tuple[str, ...] = tuple(_CHECKS)


def check_scenario(driver: Driver, scenario: str) -> bool:
    """Run one scenario against the driver; AssertionError on any deviation.

    Returns False (nothing checked) when the driver does not support the scenario.
    """
    if scenario not in _CHECKS:
        raise ValueError(f"unknown scenario {scenario!r}")
    if scenario not in driver.supports:
        return False
    options = dict(_OPTIONS.get(scenario, {}))
    if scenario == "sensitive_full":
        options["payload_mode"] = "full"

    def drive(bb: BlackBox) -> None:
        if scenario in _FAILING:
            try:
                driver.perform(scenario, bb)
            except ScenarioError:
                return
            raise AssertionError("the framework error was swallowed instead of propagating")
        driver.perform(
            scenario, bb
        )  # any exception here is the adapter's bug: let it fail the test

    events = capture(drive, **options)
    if scenario == "redactor_raises":  # the SDK drops what it cannot redact: only validity holds
        validate_events(events)
    else:
        _structure(
            events,
            payloads_allowed=scenario == "sensitive_full",
            allow_unclosed=scenario == "late_end",
        )
    _CHECKS[scenario](driver, events)
    return True

"""Health as explained dimensions (ADR-081): boundaries, state awareness and no numeric score."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import get_args

import pytest
from abb_event_schema.enums import RunStatus

from abb_api.findings.health import FindingRef, FindingsState, health
from abb_api.findings.model import RunFacts, Severity


def facts(**overrides: object) -> RunFacts:
    base: dict[str, object] = {
        "status": RunStatus.SUCCESS,
        "terminal_event_id": None,
        "started_at": datetime(2026, 10, 1, tzinfo=UTC),
        "event_count": 10,
        "error_count": 0,
        "retry_count": 0,
        "retries_unattributed": 0,
        "tool_spans_finished": 20,
        "tool_spans_ok": 20,
        "ordering_mode": "sequence",
        "agent_slug": "a",
        "cost_usd": Decimal("1.00"),
        "retry_cost_usd": Decimal("0.00"),
    }
    base.update(overrides)
    return RunFacts(**base)  # type: ignore[arg-type]


def ref(kind: str, severity: Severity = Severity.WARNING, id: str = "fnd_1") -> FindingRef:
    return FindingRef(id=id, finding_type=kind, severity=severity)


def dims(f: RunFacts, findings: list[FindingRef] | None = None, state: FindingsState = "current"):  # type: ignore[no-untyped-def]
    return {d.name: d for d in health(f, findings or [], state).dimensions}


@pytest.mark.parametrize(
    ("status", "errors", "retries", "expected"),
    [
        (RunStatus.FAILED, 0, 0, "bad"),
        (RunStatus.TIMED_OUT, 0, 0, "bad"),
        (RunStatus.BLOCKED, 0, 0, "bad"),
        (RunStatus.SUCCESS, 1, 0, "warn"),
        (RunStatus.SUCCESS, 0, 1, "warn"),
        (RunStatus.RUNNING, 2, 0, "warn"),
        (RunStatus.SUCCESS, 0, 0, "ok"),
        (RunStatus.RUNNING, 0, 0, "ok"),
        (RunStatus.CANCELLED, 3, 3, "ok"),
    ],
)
def test_reliability_rules(status: RunStatus, errors: int, retries: int, expected: str) -> None:
    d = dims(facts(status=status, error_count=errors, retry_count=retries))["reliability"]
    assert d.status == expected and d.rule and d.inputs["status"] == status.value


@pytest.mark.parametrize("state", get_args(FindingsState))
def test_reliability_never_depends_on_the_findings_state(state: FindingsState) -> None:
    assert dims(facts(status=RunStatus.FAILED), state=state)["reliability"].status == "bad"
    assert dims(facts(), state=state)["reliability"].status == "ok"


@pytest.mark.parametrize(
    ("ok", "finished", "expected"),
    [
        (20, 20, "ok"),
        (19, 20, "ok"),
        (18, 20, "warn"),
        (16, 20, "warn"),
        (15, 20, "bad"),
        (0, 0, "no_data"),
    ],
)
def test_tool_reliability_boundaries(ok: int, finished: int, expected: str) -> None:
    d = dims(facts(tool_spans_ok=ok, tool_spans_finished=finished))["tool_reliability"]
    assert d.status == expected
    assert d.inputs["tool_spans_finished"] == finished


@pytest.mark.parametrize(
    ("retry_cost", "expected"),
    [
        ("0.00", "ok"),
        ("0.09", "ok"),
        ("0.10", "warn"),
        ("0.49", "warn"),
        ("0.50", "bad"),
        ("1.00", "bad"),
    ],
)
def test_cost_efficiency_boundaries(retry_cost: str, expected: str) -> None:
    d = dims(facts(retry_cost_usd=Decimal(retry_cost)))["cost_efficiency"]
    assert d.status == expected and d.inputs["cost_usd"] == "1.00"


def test_cost_efficiency_without_cost_is_no_data() -> None:
    assert dims(facts(cost_usd=Decimal(0)))["cost_efficiency"].status == "no_data"


def test_a_cost_spike_makes_cost_efficiency_warn_only_when_analysed() -> None:
    spike = [ref("cost_spike", id="fnd_spike")]
    current = dims(facts(), spike)["cost_efficiency"]
    assert current.status == "warn" and current.finding_ids == ("fnd_spike",)
    for state in ("none", "processing", "outdated", "failed"):
        d = dims(facts(), spike, state)["cost_efficiency"]
        assert d.status == "ok" and d.inputs["cost_spike"] == "no_data", state
        assert "no data" in d.rule and d.finding_ids == ()
    assert (
        dims(facts(retry_cost_usd=Decimal("0.6")), spike, "failed")["cost_efficiency"].status
        == "bad"
    )


@pytest.mark.parametrize(
    ("findings", "loops", "context"),
    [
        ([], "ok", "ok"),
        ([ref("possible_loop")], "warn", "ok"),
        ([ref("possible_loop", Severity.INFO)], "warn", "ok"),
        ([ref("reported_loop")], "warn", "ok"),
        ([ref("identical_request")], "warn", "ok"),
        ([ref("possible_loop", Severity.CRITICAL)], "bad", "ok"),
        ([ref("context_growth", Severity.INFO)], "ok", "ok"),
        ([ref("context_growth", Severity.WARNING)], "ok", "warn"),
        ([ref("context_growth", Severity.CRITICAL)], "ok", "bad"),
        ([ref("retry_storm", Severity.CRITICAL)], "ok", "ok"),
    ],
)
def test_finding_based_dimensions_when_current(
    findings: list[FindingRef], loops: str, context: str
) -> None:
    d = dims(facts(), findings)
    assert (d["loops"].status, d["context"].status) == (loops, context)


@pytest.mark.parametrize("state", ["none", "processing", "outdated", "failed"])
def test_finding_based_dimensions_are_no_data_unless_current(state: FindingsState) -> None:
    findings = [ref("possible_loop", Severity.CRITICAL), ref("context_growth", Severity.CRITICAL)]
    d = dims(facts(), findings, state)
    for name in ("loops", "context"):
        assert d[name].status == "no_data", (name, state)
        assert d[name].inputs == {"findings_state": state} and d[name].finding_ids == ()
    reasons = {
        "none": "Not analysed yet.",
        "processing": "Analysis in progress.",
        "outdated": "Findings outdated: detector rules changed.",
        "failed": "The latest analysis failed.",
    }
    assert d["loops"].rule == reasons[state]


def test_overall_is_the_worst_status_and_no_data_never_masks_a_warning() -> None:
    assert health(facts(), [], "current").overall == "ok"
    # a warning in reliability with every finding dimension unknown: still a warning, not no_data
    assert health(facts(retry_count=1), [], "none").overall == "warn"
    assert health(facts(status=RunStatus.FAILED), [], "none").overall == "bad"
    only_unknown = health(
        facts(tool_spans_finished=0, tool_spans_ok=0, cost_usd=Decimal(0)), [], "none"
    )
    assert only_unknown.overall == "ok"  # reliability is ok: no_data ranks below ok


def test_there_is_no_numeric_score() -> None:
    result = health(facts(), [], "current")
    assert {d.name for d in result.dimensions} == {
        "reliability",
        "tool_reliability",
        "cost_efficiency",
        "loops",
        "context",
    }
    fields = {name for name in vars(result)} | {n for d in result.dimensions for n in vars(d)}
    assert not fields & {"score", "overall_score", "points", "weight"}
    assert all(isinstance(d.rule, str) and d.rule.endswith(".") for d in result.dimensions)

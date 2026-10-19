"""Run health as explained dimensions, not a score (ADR-081).

Pure and computed at read time from the run's figures, its findings and the findings state. The
dimensions that depend on findings are `no_data` unless the analysis is `current`, so a run no
detector has looked at never reads `ok`. One exception (KI-097, ADR-174): while a newer analysis is
queued (`processing`) and an earlier completed one of the current rules exists, that earlier result
is read and the dimension says so (`stale`, `as_of`). `overall` is the worst status as a word.
"""

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Literal

from abb_event_schema.enums import RunStatus

from abb_api.findings.model import RunFacts, Severity

DimStatus = Literal["ok", "warn", "bad", "no_data"]
FindingsState = Literal["none", "processing", "current", "outdated", "failed"]
_RANK: dict[DimStatus, int] = {"no_data": 0, "ok": 1, "warn": 2, "bad": 3}
FAILED_RUNS = frozenset({RunStatus.FAILED, RunStatus.TIMED_OUT, RunStatus.BLOCKED})
LOOP_TYPES = frozenset({"possible_loop", "reported_loop", "identical_request"})

_WHY_NOT_ANALYSED: dict[str, str] = {
    "none": "not analysed yet",
    "processing": "analysis in progress",
    "outdated": "findings outdated: detector rules changed",
    "failed": "the latest analysis failed",
}


@dataclass(frozen=True)
class FindingRef:
    id: str
    finding_type: str
    severity: Severity


@dataclass(frozen=True)
class Dimension:
    name: str
    status: DimStatus
    inputs: dict[str, object]
    rule: str
    finding_ids: tuple[str, ...] = ()
    # Set on the dimensions that read findings, when they did: the completion time of the analysis
    # they read, and whether newer events are still being analysed.
    as_of: datetime | None = None
    stale: bool = False


STALE_NOTE = " Based on an earlier analysis; newer events are still being analysed."


def _from_analysis(d: Dimension, as_of: datetime | None, stale: bool) -> Dimension:
    return replace(d, as_of=as_of, stale=stale, rule=d.rule + (STALE_NOTE if stale else ""))


@dataclass(frozen=True)
class Health:
    overall: DimStatus
    dimensions: tuple[Dimension, ...]


def _reliability(facts: RunFacts) -> Dimension:
    inputs: dict[str, object] = {
        "status": facts.status.value,
        "error_count": facts.error_count,
        "retry_count": facts.retry_count,
    }
    if facts.status in FAILED_RUNS:
        return Dimension(
            "reliability", "bad", inputs, "The run ended FAILED, TIMED_OUT or BLOCKED."
        )
    if (facts.status is RunStatus.SUCCESS or not facts.status.is_terminal) and (
        facts.error_count > 0 or facts.retry_count > 0
    ):
        return Dimension(
            "reliability", "warn", inputs, "The run had errors or retries but did not fail."
        )
    return Dimension("reliability", "ok", inputs, "No failure, error or retry.")


def _tool_reliability(facts: RunFacts) -> Dimension:
    inputs: dict[str, object] = {
        "tool_spans_ok": facts.tool_spans_ok,
        "tool_spans_finished": facts.tool_spans_finished,
    }
    if facts.tool_spans_finished == 0:
        return Dimension("tool_reliability", "no_data", inputs, "No tool call has finished.")
    ratio = facts.tool_spans_ok / facts.tool_spans_finished
    inputs["success_ratio"] = round(ratio, 3)
    if ratio >= 0.95:
        return Dimension("tool_reliability", "ok", inputs, "At least 95% of tool calls succeeded.")
    if ratio >= 0.8:
        return Dimension("tool_reliability", "warn", inputs, "80% to 95% of tool calls succeeded.")
    return Dimension("tool_reliability", "bad", inputs, "Fewer than 80% of tool calls succeeded.")


def _cost_efficiency(facts: RunFacts, spikes: list[FindingRef], state: FindingsState) -> Dimension:
    inputs: dict[str, object] = {
        "cost_usd": format(facts.cost_usd, "f"),
        "retry_cost_usd": format(facts.retry_cost_usd, "f"),
    }
    if facts.cost_usd <= Decimal(0):
        return Dimension("cost_efficiency", "no_data", inputs, "The run has no priced cost.")
    ratio = facts.retry_cost_usd / facts.cost_usd
    inputs["retry_cost_ratio"] = round(float(ratio), 3)
    ids = tuple(s.id for s in spikes) if state == "current" else ()
    if state == "current":
        inputs["cost_spike"] = bool(spikes)
        spike_note = ""
    else:
        inputs["cost_spike"] = "no_data"
        spike_note = f" The cost-spike check has no data: {_WHY_NOT_ANALYSED[state]}."
    if ratio >= Decimal("0.5"):
        return Dimension(
            "cost_efficiency",
            "bad",
            inputs,
            "Retries cost half or more of the run." + spike_note,
            ids,
        )
    if ratio >= Decimal("0.1") or (state == "current" and spikes):
        return Dimension(
            "cost_efficiency",
            "warn",
            inputs,
            "Retries cost 10% to 50% of the run, or the run cost far more than usual." + spike_note,
            ids,
        )
    return Dimension(
        "cost_efficiency", "ok", inputs, "Retries cost under 10% of the run." + spike_note, ids
    )


def _by_findings(
    name: str,
    findings: list[FindingRef],
    state: FindingsState,
    ok_rule: str,
    warn_rule: str,
    bad_rule: str,
    *,
    info_is_ok: bool,
) -> Dimension:
    if state != "current":
        return Dimension(
            name, "no_data", {"findings_state": state}, _WHY_NOT_ANALYSED[state].capitalize() + "."
        )
    inputs: dict[str, object] = {"findings": len(findings)}
    ids = tuple(f.id for f in findings)
    if any(f.severity is Severity.CRITICAL for f in findings):
        return Dimension(name, "bad", inputs, bad_rule, ids)
    if any(not (info_is_ok and f.severity is Severity.INFO) for f in findings):
        return Dimension(name, "warn", inputs, warn_rule, ids)
    return Dimension(name, "ok", inputs, ok_rule, ids)


def health(
    facts: RunFacts,
    findings: list[FindingRef],
    state: FindingsState,
    *,
    as_of: datetime | None = None,
) -> Health:
    """`as_of` is when the stored analysis completed. It is only used when that analysis is
    `current`, or `processing` with a result of the current rules to fall back on (the caller
    passes it only then); every other state keeps its `no_data`."""
    stale = state == "processing" and as_of is not None
    reading: FindingsState = "current" if stale else state
    dimensions = [
        _reliability(facts),
        _tool_reliability(facts),
        _cost_efficiency(facts, [f for f in findings if f.finding_type == "cost_spike"], reading),
        _by_findings(
            "loops",
            [f for f in findings if f.finding_type in LOOP_TYPES],
            reading,
            "No loop or identical requests were found.",
            "A probable loop, a reported loop or identical requests were found.",
            "A long loop or many identical requests were found.",
            info_is_ok=False,
        ),
        _by_findings(
            "context",
            [f for f in findings if f.finding_type == "context_growth"],
            reading,
            "The context did not grow unusually (or only slightly).",
            "The context grew fast and is large.",
            "The context grew very large.",
            info_is_ok=True,
        ),
    ]
    if reading == "current":
        for i, d in enumerate(dimensions):
            # Cost efficiency reads the analysis only for its spike check, and only when it has a
            # priced cost to judge at all.
            if d.name in ("loops", "context") or (
                d.name == "cost_efficiency" and d.status != "no_data"
            ):
                dimensions[i] = _from_analysis(d, as_of, stale)
    overall = max((d.status for d in dimensions), key=_RANK.__getitem__)
    return Health(overall=overall, dimensions=tuple(dimensions))

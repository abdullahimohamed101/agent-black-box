"""Dry run: evaluate a draft policy against a project's recorded events (ADR-140).

Reproducible: runs are the project's newest `max_runs` (by start time, then id), events are read
in that run order then by time, and the evaluation is the pure `evaluate`. The same draft over the
same data gives the same answer. Bounded three ways: runs, mapped events, and a wall-clock budget
for the evaluation loop (the database read has its own statement timeout, set by the caller).

The result carries counts, rule ids (the author's own words) and sample hits made of ids,
capability, risk class, decision and rule id. It never carries a resource, a command, a path or an
argument: those are content, and a dry run is allowed to read them only because it is restricted
to owners and admins, who may read payloads anyway.
"""

import time
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from abb_api.policies.evaluator import evaluate
from abb_api.policies.mapping import intent_for
from abb_api.policies.model import DecisionKind, Policy
from abb_api.runs.event_queries import ScannedEvent

DEFAULT_MAX_RUNS = 100
DEFAULT_MAX_EVENTS = 20_000
DEFAULT_TIME_BUDGET_SECONDS = 5.0
DEFAULT_MAX_SAMPLES = 25
_CLOCK_EVERY = 256  # events between looks at the clock


@dataclass(frozen=True)
class DryRunLimits:
    max_runs: int = DEFAULT_MAX_RUNS
    max_events: int = DEFAULT_MAX_EVENTS
    time_budget_seconds: float = DEFAULT_TIME_BUDGET_SECONDS
    max_samples: int = DEFAULT_MAX_SAMPLES


@dataclass(frozen=True)
class SampleHit:
    run_id: uuid.UUID
    event_id: uuid.UUID
    capability: str
    risk_class: str | None
    decision: DecisionKind
    rule_id: str | None


@dataclass
class DryRunResult:
    runs_considered: int = 0
    events_evaluated: int = 0
    events_skipped: int = 0  # read but not guardable (no intent)
    truncated: str | None = None  # "events" | "time_budget" | "runs"
    by_decision: Counter[str] = field(default_factory=Counter)
    by_rule: Counter[tuple[str | None, str]] = field(default_factory=Counter)
    samples: list[SampleHit] = field(default_factory=list)


def evaluate_events(
    policy: Policy,
    events: Sequence[ScannedEvent],
    limits: DryRunLimits = DryRunLimits(),  # noqa: B008  (frozen, shared on purpose)
    *,
    clock: Callable[[], float] = time.monotonic,
) -> DryRunResult:
    """Count what `policy` would decide for each recorded action and keep the first few that it
    would deny or send for approval."""
    result = DryRunResult()
    deadline = clock() + limits.time_budget_seconds
    for index, event in enumerate(events):
        if index % _CLOCK_EVERY == 0 and index and clock() > deadline:
            result.truncated = "time_budget"
            break
        intent = intent_for(event.event_type, event.attributes)
        if intent is None:
            result.events_skipped += 1
            continue
        decision = evaluate(policy, intent)
        result.events_evaluated += 1
        result.by_decision[decision.decision.value] += 1
        result.by_rule[(decision.rule_id, decision.decision.value)] += 1
        if decision.decision is not DecisionKind.ALLOW and len(result.samples) < limits.max_samples:
            result.samples.append(
                SampleHit(
                    event.run_id,
                    event.event_id,
                    intent.capability,
                    intent.risk_class,
                    decision.decision,
                    decision.rule_id,
                )
            )
    return result

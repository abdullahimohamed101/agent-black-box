"""The closed set of quotas, their limits and the pure admission rule (ADR-154).

A quota names one thing a project may not grow without bound. Adding one means adding a member
here, a column in `project_quota_overrides` and a setting; the vocabulary is closed so a client,
an audit row and a screen can all name a quota without free text.

`admit` is the whole ingestion decision as a pure function: given what the project already has and
what a batch would add, which events are accepted and which are refused for which quota. It owns
the order of the checks (agent, then run, then events per day) and the promise that a refused
event costs nothing (no agent, no run, no event is counted for it).
"""

import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Literal

from abb_api.core.errors import AppError, ErrorCategory

QUOTA_EXCEEDED: Final = "QUOTA_EXCEEDED"
NEAR_LIMIT_PERCENT: Final = 80


class Quota(StrEnum):
    AGENTS = "agents"  # distinct agent slugs ever seen in the project
    RUNS_PER_DAY = "runs_per_day"  # runs created on one UTC day
    EVENTS_PER_DAY = "events_per_day"  # events accepted on one UTC day
    ARTIFACT_BYTES = "artifact_bytes"  # total stored artifact bytes


# The same bounds the migration's CHECK constraints enforce (an override outside them is 422).
BOUNDS: Final[dict[Quota, tuple[int, int]]] = {
    Quota.AGENTS: (1, 1_000_000),
    Quota.RUNS_PER_DAY: (1, 100_000_000),
    Quota.EVENTS_PER_DAY: (1, 100_000_000_000),
    Quota.ARTIFACT_BYTES: (1, 1024**5),
}

# Fixed sentences: a refusal never repeats a client value (and says which quota, in words).
QUOTA_WORDS: Final[dict[Quota, str]] = {
    Quota.AGENTS: "the distinct agents",
    Quota.RUNS_PER_DAY: "the runs per day",
    Quota.EVENTS_PER_DAY: "the events per day",
    Quota.ARTIFACT_BYTES: "the artifact storage",
}


@dataclass(frozen=True)
class QuotaLimits:
    agents: int
    runs_per_day: int
    events_per_day: int
    artifact_bytes: int

    def of(self, quota: Quota) -> int:
        return int(getattr(self, quota.value))


@dataclass(frozen=True)
class QuotaOverrides:
    """A project's overrides; None means "the environment default" for that quota."""

    agents: int | None = None
    runs_per_day: int | None = None
    events_per_day: int | None = None
    artifact_bytes: int | None = None

    def of(self, quota: Quota) -> int | None:
        value = getattr(self, quota.value)
        return None if value is None else int(value)

    def resolve(self, defaults: QuotaLimits) -> QuotaLimits:
        def pick(quota: Quota) -> int:
            override = self.of(quota)
            return defaults.of(quota) if override is None else override

        return QuotaLimits(
            agents=pick(Quota.AGENTS),
            runs_per_day=pick(Quota.RUNS_PER_DAY),
            events_per_day=pick(Quota.EVENTS_PER_DAY),
            artifact_bytes=pick(Quota.ARTIFACT_BYTES),
        )


def quota_exceeded(quota: Quota, *, limit: int | None = None, used: int | None = None) -> AppError:
    """The HTTP form of a refusal (non-batch endpoints): 413, not retryable.

    413 because the refusal is a capacity limit like the per-artifact size cap, and the SDK treats
    it as "do not retry". The batch endpoint reports the same code per event inside a 202.
    """
    details: dict[str, object] = {"quota": quota.value}
    if limit is not None:
        details["limit"] = limit
    if used is not None:
        details["used"] = used
    return AppError(
        QUOTA_EXCEEDED,
        f"This project has reached its quota for {QUOTA_WORDS[quota]}.",
        category=ErrorCategory.RATE_LIMIT,
        status_code=413,
        retryable=False,
        details=details,
    )


@dataclass(frozen=True)
class Candidate:
    """One event that is new to storage: its place in the batch, agent slug and run."""

    index: int
    agent: str
    run: uuid.UUID


@dataclass(frozen=True)
class Admission:
    accepted: list[int] = field(default_factory=list)
    refused: dict[int, Quota] = field(default_factory=dict)
    new_agents: frozenset[str] = frozenset()
    new_runs: frozenset[uuid.UUID] = frozenset()


def admit(
    candidates: Sequence[Candidate],
    *,
    limits: QuotaLimits,
    agents_used: int,
    runs_today: int,
    events_today: int,
    known_agents: Collection[str],
    known_runs: Collection[uuid.UUID],
) -> Admission:
    """Decide, in batch order, which new events fit. Pure.

    * an event of a known agent and a known run only needs room in the day's events;
    * an event naming an unknown agent needs room for one more agent, and an event of an unknown
      run room for one more run today; once a slug or run was refused, all its later events are
      refused too, whatever room appears;
    * an event refused for the day's event quota does not count its agent or run, so a later
      event of the same new agent or run is judged on its own.
    """
    accepted: list[int] = []
    refused: dict[int, Quota] = {}
    new_agents: set[str] = set()
    new_runs: set[uuid.UUID] = set()
    refused_agents: set[str] = set()
    refused_runs: set[uuid.UUID] = set()
    for c in candidates:
        agent_is_new = c.agent not in known_agents and c.agent not in new_agents
        if agent_is_new and (
            c.agent in refused_agents or agents_used + len(new_agents) >= limits.agents
        ):
            refused_agents.add(c.agent)
            refused[c.index] = Quota.AGENTS
            continue
        run_is_new = c.run not in known_runs and c.run not in new_runs
        if run_is_new and (
            c.run in refused_runs or runs_today + len(new_runs) >= limits.runs_per_day
        ):
            refused_runs.add(c.run)
            refused[c.index] = Quota.RUNS_PER_DAY
            continue
        if events_today + len(accepted) >= limits.events_per_day:
            refused[c.index] = Quota.EVENTS_PER_DAY
            continue
        accepted.append(c.index)
        if agent_is_new:
            new_agents.add(c.agent)
        if run_is_new:
            new_runs.add(c.run)
    return Admission(accepted, refused, frozenset(new_agents), frozenset(new_runs))


def status_of(used: int, limit: int) -> Literal["ok", "near_limit", "at_limit"]:
    """`ok`, `near_limit` (from 80 %) or `at_limit` (the limit is reached or passed)."""
    if used >= limit:
        return "at_limit"
    return "near_limit" if used * 100 >= limit * NEAR_LIMIT_PERCENT else "ok"

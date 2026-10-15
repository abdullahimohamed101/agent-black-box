"""The retention policy as pure data: tiers, settings, validation, effective days and cutoffs.

A tier's stored setting is `None` (the environment default), `FOREVER` or a whole number of days.
The *effective* policy replaces `None` by the default; `None` there means "keep forever".
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, Literal

from abb_api.core.config import RETENTION_CEILING_DAYS, RETENTION_FLOOR_DAYS, RetentionSettings

FOREVER: Final = "forever"
TierSetting = int | Literal["forever"] | None


class Tier(StrEnum):
    ARTIFACTS = "artifacts"  # the files of a run, after the run ended
    TRACE = "trace"  # events, spans, findings, evaluations, search entries, run-bound jobs
    RUN = "run"  # the run record and its rollup contribution; deleted last
    AUDIT = "audit"  # the audit log, by the age of the entry


class ReportTier(StrEnum):
    """What a retention report describes: the policy tiers plus two fixed-window clean-ups."""

    ARTIFACTS = "artifacts"
    TRACE = "trace"
    RUN = "run"
    AUDIT = "audit"
    OPERATIONAL = "operational"  # a workspace's finished jobs, expired counters, old usage rows
    PLATFORM = "platform"  # install level: dead sessions, people who never joined


TIERS: Final = tuple(Tier)

# Operational windows are not set by a workspace. Documented in docs/OPERATIONS.md.
FINISHED_JOB_DAYS: Final = 7
DEAD_LETTER_JOB_DAYS: Final = 30
COUNTER_GRACE_HOURS: Final = 1
USAGE_DAILY_DAYS: Final = 35
SESSION_GRACE_DAYS: Final = 7
STRANGER_DAYS: Final = 30


@dataclass(frozen=True)
class RetentionInvalid(ValueError):
    """A policy that cannot be saved. `reason` is one of a closed set; no value is repeated."""

    tier: Tier
    reason: Literal["below_floor", "above_ceiling", "order"]

    def __str__(self) -> str:
        return f"{self.tier.value}: {self.reason}"


def check_setting(tier: Tier, value: TierSetting) -> None:
    if isinstance(value, int):
        if value < RETENTION_FLOOR_DAYS:
            raise RetentionInvalid(tier, "below_floor")
        if value > RETENTION_CEILING_DAYS:
            raise RetentionInvalid(tier, "above_ceiling")


def defaults_of(settings: RetentionSettings) -> dict[Tier, int | None]:
    """The environment defaults as effective days (`None`: forever)."""
    raw = {
        Tier.ARTIFACTS: settings.retention_artifacts_days,
        Tier.TRACE: settings.retention_trace_days,
        Tier.RUN: settings.retention_run_days,
        Tier.AUDIT: settings.retention_audit_days,
    }
    return {tier: (None if value == FOREVER else int(value)) for tier, value in raw.items()}


def effective(
    defaults: Mapping[Tier, int | None], stored: Mapping[Tier, TierSetting]
) -> dict[Tier, int | None]:
    """Days per tier after applying a workspace's choices (`None`: keep forever)."""
    result: dict[Tier, int | None] = {}
    for tier in TIERS:
        setting = stored.get(tier)
        result[tier] = (
            defaults[tier] if setting is None else None if setting == FOREVER else int(setting)
        )
    return result


def check_order(days: Mapping[Tier, int | None]) -> None:
    """The run record is deleted last, so it outlives the trace detail, which outlives the files."""
    for inner, outer in ((Tier.ARTIFACTS, Tier.TRACE), (Tier.TRACE, Tier.RUN)):
        shorter, longer = days[inner], days[outer]
        if longer is not None and (shorter is None or shorter > longer):
            raise RetentionInvalid(inner, "order")


def validate(
    defaults: Mapping[Tier, int | None], stored: Mapping[Tier, TierSetting]
) -> dict[Tier, int | None]:
    """The effective days of a policy, or `RetentionInvalid` naming the offending tier."""
    for tier, value in stored.items():
        check_setting(tier, value)
    days = effective(defaults, stored)
    check_order(days)
    return days


def cutoff(now: datetime, days: int | None) -> datetime | None:
    """Data older than this is expired; `None` while the tier is kept forever."""
    return None if days is None else now - timedelta(days=days)

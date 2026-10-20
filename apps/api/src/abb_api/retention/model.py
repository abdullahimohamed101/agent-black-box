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
# The record of a policy change (`retention.*` audit rows) outlives the audit tier for this long,
# whatever the policy says, so shortening the audit period does not erase the shortening itself.
POLICY_RECORD_DAYS: Final = 365
POLICY_RECORD_PREFIX: Final = "retention."


@dataclass(frozen=True)
class RetentionInvalid(ValueError):
    """A policy that cannot be saved. `reason` is one of a closed set; no value is repeated."""

    tier: Tier
    reason: Literal["below_floor", "above_ceiling", "order"]

    def __str__(self) -> str:
        return f"{self.tier.value}: {self.reason}"


def floor_of(tier: Tier, audit_floor: int) -> int:
    """The shortest period a workspace may choose: the audit log has its own, higher minimum."""
    return audit_floor if tier is Tier.AUDIT else RETENTION_FLOOR_DAYS


def check_setting(tier: Tier, value: TierSetting, audit_floor: int = RETENTION_FLOOR_DAYS) -> None:
    if isinstance(value, int):
        if value < floor_of(tier, audit_floor):
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
    defaults: Mapping[Tier, int | None],
    stored: Mapping[Tier, TierSetting],
    audit_floor: int = RETENTION_FLOOR_DAYS,
) -> dict[Tier, int | None]:
    """The effective days of a policy, or `RetentionInvalid` naming the offending tier."""
    for tier, value in stored.items():
        check_setting(tier, value, audit_floor)
    days = effective(defaults, stored)
    check_order(days)
    return days


def cutoff(now: datetime, days: int | None) -> datetime | None:
    """Data older than this is expired; `None` while the tier is kept forever."""
    return None if days is None else now - timedelta(days=days)


# ----------------------------------------------------------------------- grace period (ADR-159)


@dataclass(frozen=True)
class Pending:
    """A shortening that has not taken effect: `previous` (a stored setting) is still enforced
    until `effective_at`, beside the new value; the LONGER of the two applies."""

    previous: TierSetting
    effective_at: datetime


def days_of(defaults: Mapping[Tier, int | None], tier: Tier, setting: TierSetting) -> int | None:
    """One tier's stored setting as effective days (`None`: forever)."""
    return defaults[tier] if setting is None else None if setting == FOREVER else int(setting)


def longer(a: int | None, b: int | None) -> int | None:
    """The longer of two periods; forever (`None`) beats any number of days."""
    return None if a is None or b is None else max(a, b)


def is_shorter(new: int | None, old: int | None) -> bool:
    return new is not None and (old is None or new < old)


def active(pending: Pending | None, now: datetime) -> Pending | None:
    """The pending shortening if it has not taken effect yet."""
    return pending if pending is not None and pending.effective_at > now else None


def enforced_days(
    defaults: Mapping[Tier, int | None],
    tier: Tier,
    setting: TierSetting,
    pending: Pending | None,
    now: datetime,
    audit_floor: int = RETENTION_FLOOR_DAYS,
) -> int | None:
    """What the job applies to this tier right now: the longer of the new and the previous period
    while a shortening waits, never an audit period below the audit minimum."""
    days = days_of(defaults, tier, setting)
    waiting = active(pending, now)
    if waiting is not None:
        days = longer(days, days_of(defaults, tier, waiting.previous))
    if tier is Tier.AUDIT and days is not None:
        days = max(days, audit_floor)
    return days


def enforced(
    defaults: Mapping[Tier, int | None],
    stored: Mapping[Tier, TierSetting],
    pending: Mapping[Tier, Pending],
    now: datetime,
    audit_floor: int = RETENTION_FLOOR_DAYS,
) -> dict[Tier, int | None]:
    return {
        tier: enforced_days(defaults, tier, stored.get(tier), pending.get(tier), now, audit_floor)
        for tier in TIERS
    }


def plan_pending(
    defaults: Mapping[Tier, int | None],
    tier: Tier,
    before: TierSetting,
    before_pending: Pending | None,
    new: TierSetting,
    now: datetime,
    grace: timedelta,
    audit_floor: int = RETENTION_FLOOR_DAYS,
) -> Pending | None:
    """The pending shortening a save leaves behind for one tier (`None`: takes effect at once).

    Measured against what is enforced now: a value that is not shorter than that (a lengthening,
    or saving the previous value again, which cancels) takes effect immediately and clears any
    pending change. A shortening starts the grace period; a second shortening during it keeps the
    earliest effective time and the original previous value (the longest period stays in force
    until the first deadline)."""
    current = enforced_days(defaults, tier, before, before_pending, now, audit_floor)
    if not is_shorter(days_of(defaults, tier, new), current) or grace <= timedelta(0):
        return None
    waiting = active(before_pending, now)
    if waiting is not None:
        return waiting
    return Pending(previous=before, effective_at=now + grace)

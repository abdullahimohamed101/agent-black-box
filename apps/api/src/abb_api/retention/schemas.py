"""HTTP shapes of the retention API (ADR-159). Counts and days only: never tenant content."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

TierName = Literal["artifacts", "trace", "run", "audit"]
ReportTierName = Literal["artifacts", "trace", "run", "audit", "operational", "platform"]
ModeName = Literal["off", "dry_run", "enforce"]
Source = Literal["default", "workspace"]


class PendingShorteningOut(BaseModel):
    """A shortening that has not taken effect: the longer period still applies until then."""

    effective_at: datetime = Field(description="When the shorter period starts to apply.")
    current_days: int | None = Field(
        description="The period enforced until then (the longer of the two); null: forever."
    )
    current_forever: bool


class TierPolicyOut(BaseModel):
    tier: TierName
    days: int | None = Field(
        description=(
            "Days a run (or audit entry) is kept after it ended, as saved; null: kept forever. "
            "While `pending` is set the longer `pending.current_days` is what is enforced."
        )
    )
    forever: bool
    pending: PendingShorteningOut | None = Field(
        default=None, description="Set while a saved shortening waits for its effective time."
    )
    source: Source = Field(description="`workspace` when this workspace chose it, else `default`.")
    default_days: int | None = Field(description="The environment default (null: forever).")


class RetentionPolicyOut(BaseModel):
    tiers: list[TierPolicyOut]
    mode: ModeName = Field(
        description=(
            "What the retention job does on this installation (`ABB_RETENTION_MODE`): `dry_run` "
            "only measures and reports; `enforce` deletes."
        )
    )
    floor_days: int
    audit_floor_days: int = Field(description="The audit tier's own minimum (owners only).")
    grace_hours: int = Field(
        description="A shortening takes effect after this many hours (0: at once)."
    )
    updated_at: datetime | None = Field(description="Null while the workspace has no choices.")
    updated_by: str | None


class RetentionPolicyIn(BaseModel):
    """A tier left out stays as it is; a whole number of days sets it; `"forever"` keeps it for
    good; `null` returns it to the environment default."""

    model_config = ConfigDict(extra="forbid")

    artifacts: StrictInt | Literal["forever"] | None = None
    trace: StrictInt | Literal["forever"] | None = None
    run: StrictInt | Literal["forever"] | None = None
    audit: StrictInt | Literal["forever"] | None = None


class PreviewTierOut(BaseModel):
    tier: TierName
    days: int | None
    forever: bool
    enforced_days: int | None = Field(
        description=(
            "The period the job applies right now if this policy were saved (the longer of the "
            "old and the new while a shortening waits); null: forever. The counts below are for "
            "`days`, once the shortening takes effect."
        ),
    )
    effective_at: datetime | None = Field(
        default=None, description="When the shorter period would start to apply; null: at once."
    )
    cutoff: datetime | None = Field(description="Data that ended before this would be deleted.")
    runs: int = Field(description="Runs whose data in this tier would be deleted.")
    events: int
    artifact_bytes: int
    rows: int = Field(
        description="Other rows: artifact records, derived rows of the runs, audit entries."
    )
    truncated: bool = Field(
        description="True when a bound was reached: the figures are a lower bound."
    )


class RetentionPreviewOut(BaseModel):
    as_of: datetime
    proposed: bool = Field(description="True when the query proposed values instead of the saved.")
    tiers: list[PreviewTierOut]
    note: str = Field(
        description="Nothing is deleted by a preview. Tiers are counted separately, not added."
    )


class RetentionReportOut(BaseModel):
    id: int
    tier: ReportTierName
    mode: Literal["dry_run", "enforce"]
    runs: int
    events: int
    artifact_bytes: int
    rows: int
    truncated: bool
    cutoff_at: datetime | None
    started_at: datetime
    finished_at: datetime


class RetentionReportsOut(BaseModel):
    items: list[RetentionReportOut]

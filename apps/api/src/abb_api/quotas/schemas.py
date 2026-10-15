"""HTTP shapes of the quota API (ADR-154). Counts and limits only: no tenant content."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

QuotaName = Literal["agents", "runs_per_day", "events_per_day", "artifact_bytes"]
QuotaStatus = Literal["ok", "near_limit", "at_limit"]


class QuotaUsageOut(BaseModel):
    quota: QuotaName
    period: Literal["total", "day"] = Field(
        description="`day` quotas count the current UTC day and start again at midnight UTC."
    )
    limit: int = Field(description="The limit in force: the override if there is one.")
    default: int = Field(description="The environment default (`ABB_QUOTA_*`).")
    overridden: bool
    used: int
    percent: int = Field(description="Whole percent of the limit used; above 100 after a lowering.")
    status: QuotaStatus = Field(
        description="`near_limit` from 80 percent; `at_limit` when new items are being refused."
    )


class ProjectQuotasOut(BaseModel):
    project_id: str
    day: date = Field(description="The UTC day the daily figures belong to.")
    resets_at: datetime = Field(
        description="When the daily quotas start again (next UTC midnight)."
    )
    enforced: bool = Field(
        description="False while `ABB_QUOTAS_ENABLED=false`: nothing is checked or counted."
    )
    quotas: list[QuotaUsageOut]
    updated_at: datetime | None = Field(description="Null while the project has no overrides.")
    updated_by: str | None


class QuotaOverridesIn(BaseModel):
    """A field left out stays as it is; a number sets the override; null removes it."""

    model_config = ConfigDict(extra="forbid")

    agents: StrictInt | None = None
    runs_per_day: StrictInt | None = None
    events_per_day: StrictInt | None = None
    artifact_bytes: StrictInt | None = None

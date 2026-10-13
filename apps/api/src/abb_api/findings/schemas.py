"""HTTP shapes of the findings route. Evidence holds metadata only (ADR-080), so one shape serves
every actor: there is no per-role projection to get wrong."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

FindingsStateName = Literal["none", "processing", "current", "outdated", "failed"]
DimensionStatus = Literal["ok", "warn", "bad", "no_data"]


class FindingsState(BaseModel):
    status: FindingsStateName = Field(
        description=(
            "`none`: not analysed yet; `processing`: an analysis is queued or running; "
            "`current`: up to date; `outdated`: the detector rules changed since this analysis "
            "(re-run `rebuild-findings`); `failed`: the newest analysis was dead-lettered and "
            "the items, if any, are from the last successful one."
        )
    )
    detectors_version: int | None
    summary_version_seen: int | None
    computed_at: datetime | None
    event_count_seen: int | None
    truncated: bool = Field(description="True when only the first events of a huge run were read.")


class DimensionOut(BaseModel):
    name: str
    status: DimensionStatus
    inputs: dict[str, Any]
    rule: str = Field(description="The one sentence that decided the status.")
    finding_ids: list[str]


class HealthOut(BaseModel):
    overall: DimensionStatus = Field(
        description="The worst dimension; `no_data` never masks `warn`."
    )
    dimensions: list[DimensionOut]


class FindingOut(BaseModel):
    id: str
    run_id: str
    detector: str
    detector_version: str = Field(description="`name@x.y.z`.")
    finding_type: str
    kind: Literal["deterministic", "probable"]
    severity: Literal["info", "warning", "medium", "high", "critical"]
    category: Literal["reliability", "security"] = Field(
        description=(
            "`security` for the Phase 13 detectors (ids `sec.*`: fixed wording, `evidence.rule` "
            "states the rule that matched, no captured content); `reliability` otherwise."
        )
    )
    confidence: float
    title: str = Field(description='Starts with "Probable:" iff `kind` is `probable`.')
    message: str
    start_event_id: str | None
    end_event_id: str | None
    evidence_event_ids: list[str]
    fingerprint: str
    evidence: dict[str, Any] = Field(
        description="Typed per `finding_type`; metadata only (ids, counts, names, thresholds)."
    )
    created_at: datetime
    updated_at: datetime


class RunFindingsOut(BaseModel):
    run_id: str
    state: FindingsState
    health: HealthOut
    items: list[FindingOut]

"""HTTP shapes of the security API (ADR-131). Metadata only: nothing here carries a path, command,
host, table name or secret, and the same shape serves every role that may call the route."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SeverityName = Literal["info", "warning", "medium", "high", "critical"]
CaptureModeName = Literal["FULL", "METADATA_ONLY", "DISABLED"]


class DetectorOut(BaseModel):
    id: str = Field(description="`sec.<name>`: the id a project can mute.")
    title: str
    description: str = Field(description="What the detector looks for, in fixed words.")
    severities: list[SeverityName] = Field(description="Severities it reports, ascending.")
    version: str = Field(description="The rule version (`x.y.z`) findings record.")
    muted: bool = Field(description="Muted for the project asked about (false without one).")
    active: bool | None = Field(
        description=(
            "Whether it can report for the project: not muted and, for the destination "
            "detector, an allowlist exists. Null when no project was named and it depends on one."
        )
    )


class DetectorList(BaseModel):
    items: list[DetectorOut]


class SecurityFindingOut(BaseModel):
    id: str
    run_id: str
    run_name: str | None
    run_status: str
    project_id: str
    project_slug: str
    project_name: str
    detector: str
    detector_version: str
    finding_type: str
    kind: Literal["probable"]
    severity: SeverityName
    title: str
    message: str = Field(description="The detector's fixed sentence; never any captured content.")
    rule: str | None = Field(description="The rule that matched, in words.")
    count: int | None = Field(description="Events (and artifacts) that matched.")
    start_event_id: str | None
    evidence_event_ids: list[str]
    run_started_at: datetime
    detected_at: datetime
    updated_at: datetime


class FacetValue(BaseModel):
    value: str
    label: str | None = Field(description="A project's name; null for severities and detectors.")
    count: int


class SecurityFacets(BaseModel):
    severity: list[FacetValue]
    detector: list[FacetValue]
    project: list[FacetValue]


class SecurityFindingPage(BaseModel):
    items: list[SecurityFindingOut]
    next_cursor: str | None
    facets: SecurityFacets = Field(
        description="Counts under the other filters, so a chosen value keeps its siblings."
    )


class SecuritySettingsOut(BaseModel):
    project_id: str
    capture_mode: CaptureModeName
    muted_detectors: list[str]
    destination_allowlist: list[str]
    updated_at: datetime | None = Field(description="Null while the project has no stored row.")
    updated_by: str | None
    detectors: list[DetectorOut]


class SecuritySettingsIn(BaseModel):
    """Fields left out stay as they are; a given list replaces the stored list."""

    capture_mode: CaptureModeName | None = None
    muted_detectors: list[str] | None = Field(default=None, max_length=64)
    destination_allowlist: list[str] | None = Field(default=None, max_length=256)

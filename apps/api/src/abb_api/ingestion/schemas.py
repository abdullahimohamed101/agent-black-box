"""HTTP response shapes for ingestion (the request is validated by abb-event-schema)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class IssueOut(BaseModel):
    loc: list[str | int]
    code: str
    message: str


class EventErrorOut(BaseModel):
    """Why one event of a batch was not stored."""

    index: int = Field(description="Position of the event in the submitted `events` array.")
    event_id: str | None = Field(description="The event's id, when it was readable.")
    code: str
    quota: Literal["agents", "runs_per_day", "events_per_day", "artifact_bytes"] | None = Field(
        default=None,
        description=(
            "Which quota refused the event; set only when `code` is `QUOTA_EXCEEDED`. Existing "
            "data keeps working; retrying does not help until the quota changes or its day ends."
        ),
    )
    issues: list[IssueOut] = []


class BatchResponse(BaseModel):
    batch_id: str | None
    accepted: int = Field(description="Events stored by this request.")
    duplicates: int = Field(description="Events already stored with identical content.")
    conflicts: int = Field(
        description="Events whose id exists with different content (kept first)."
    )
    rejected: int = Field(description="Events that were not stored; see `errors`.")
    errors: list[EventErrorOut]
    server_time: datetime
    request_id: str | None


class EventResponse(BaseModel):
    event_id: str
    status: Literal["accepted", "duplicate", "conflict"]
    server_time: datetime
    request_id: str | None

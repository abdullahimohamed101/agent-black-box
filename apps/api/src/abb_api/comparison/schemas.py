"""HTTP shape of `GET /v1/runs/{run_id}/compare`."""

from typing import Literal

from pydantic import BaseModel, Field


class StepOut(BaseModel):
    event_id: str
    span_id: str | None
    family: str
    event_type: str
    status: str | None
    duration_ms: float | None
    offset_ms: float | None
    label: str | None = Field(
        description=(
            "Tool, model, span or (with `payload.read`) command, path or branch. Null when "
            "`label_withheld`."
        )
    )
    label_withheld: bool


class RowOut(BaseModel):
    relation: Literal["equal", "changed", "left_only", "right_only"]
    left: StepOut | None
    right: StepOut | None


class SideOut(BaseModel):
    run_id: str
    status: str
    steps: int = Field(description="Steps compared (after the event and step caps).")
    truncated: bool
    truncated_at_event: str | None = Field(
        description="The last event compared when the run was cut by the caps."
    )


class MetricOut(BaseModel):
    metric: str
    left: float | int | None
    right: float | int | None
    delta: float | int | None = Field(description="`right - left`; null when either is missing.")


class CostModelOut(BaseModel):
    model: str
    left: float
    right: float
    delta: float


class FilesOut(BaseModel):
    paths_withheld: bool = Field(
        description="True without `payload.read`: per-side counts only, no paths, no overlap."
    )
    left_count: int
    right_count: int
    only_left: list[str]
    only_right: list[str]
    both: list[str]
    truncated: bool


class ScoreOut(BaseModel):
    score: float | None
    label: str | None


class EvaluationDiffOut(BaseModel):
    evaluator_id: str | None
    evaluator_name: str
    kind: str
    ground_truth: bool
    left: ScoreOut | None
    right: ScoreOut | None
    delta: float | None


class DivergenceOut(BaseModel):
    index: int = Field(description="Index into `steps`.")
    relation: Literal["changed", "left_only", "right_only"]


class ComparisonOut(BaseModel):
    left: SideOut
    right: SideOut
    alignment: Literal["content", "metadata"] = Field(
        description=(
            "`metadata` without `payload.read`: steps align on kinds, tool and model names only, "
            "and every relation below is computed from that projection."
        )
    )
    alignment_quality: Literal["full", "partial"] = Field(
        description="`partial`: the middle was too different to align within the work budget."
    )
    summary: list[MetricOut]
    steps: list[RowOut]
    divergence: DivergenceOut | None
    first_outcome_difference: int | None = Field(
        description="Index of the first aligned pair whose status differs."
    )
    cost_by_model: list[CostModelOut]
    files: FilesOut
    evaluations: list[EvaluationDiffOut]

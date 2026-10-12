"""HTTP shapes of the evaluation routes. Byte limits live in validators, ahead of the database."""

from datetime import datetime
from typing import Annotated, Any, Literal

from abb_event_schema.ids import IdKind, id_pattern
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

EvaluatorKind = Literal["deterministic", "metric", "human", "llm_judge"]

# A slug: it is shown to every reader and snapshotted into each evaluation.
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")]
Description = Annotated[
    str, StringConstraints(max_length=500, pattern=r"^[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f]*$")
]


class EvaluatorIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Slug = Field(description="Unique per workspace; lower-case letters, digits, `-`, `_`.")
    kind: EvaluatorKind
    implementation: Annotated[str, StringConstraints(min_length=1, max_length=64)] = Field(
        description="A built-in id, e.g. `tests_passed` or `cost_under`; `human` for verdicts."
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Validated by the implementation's own model; unknown keys are rejected.",
    )
    description: Description | None = None
    project_id: str | None = Field(
        default=None,
        pattern=id_pattern(IdKind.PROJECT),
        description="Omit for an evaluator that applies to every project of the workspace.",
    )


class EvaluatorOut(BaseModel):
    id: str
    name: str
    kind: EvaluatorKind
    implementation: str
    version: int
    config: dict[str, Any] = Field(
        description=(
            "Path globs, required paths and rubrics are null without `payload.read` and listed "
            "in `config_withheld`; thresholds and labels are always shown."
        )
    )
    config_withheld: list[str]
    description: str | None
    project_id: str | None
    ground_truth: bool = Field(description="False for model judges: an opinion, not a fact.")
    created_at: datetime
    archived_at: datetime | None


class EvaluatorPage(BaseModel):
    items: list[EvaluatorOut]


# ------------------------------------------------------------------ evaluations of a run

EvaluationState = Literal["pending", "running", "completed", "failed"]
# Free text is bounded in characters and in UTF-8 bytes ahead of the database (D2).
_NO_CONTROL = r"^[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f]*$"
VerdictLabel = Annotated[
    str, StringConstraints(min_length=1, max_length=64, pattern=r"^[^\x00-\x1f\x7f]+$")
]
MAX_EXPLANATION_CHARS = 4_000
MAX_EXPLANATION_BYTES = 16_384


class TriggerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluator_id: str = Field(pattern=id_pattern(IdKind.EVALUATOR))


class VerdictIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluator_id: str = Field(
        pattern=id_pattern(IdKind.EVALUATOR), description="A `human` evaluator."
    )
    label: VerdictLabel = Field(description="One of the evaluator's `allowed_labels`.")
    score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    explanation: (
        Annotated[str, StringConstraints(max_length=MAX_EXPLANATION_CHARS, pattern=_NO_CONTROL)]
        | None
    ) = None

    @field_validator("explanation")
    @classmethod
    def _bytes(cls, value: str | None) -> str | None:
        if value is not None and len(value.encode("utf-8")) > MAX_EXPLANATION_BYTES:
            raise ValueError("explanation is too large")
        return value


class EvaluationOut(BaseModel):
    id: str
    run_id: str
    evaluator_id: str | None
    evaluator_name: str
    evaluator_version: str
    kind: EvaluatorKind
    implementation: str | None
    ground_truth: bool = Field(description="False for model judges: an opinion, not a fact.")
    state: EvaluationState = Field(
        description=(
            "`pending` rows whose job was dead-lettered, or that waited past the stale limit "
            "with no job, read as `failed` with `error_code` `JOB_FAILED` (a read writes nothing; "
            "the next trigger of the evaluator writes it)."
        )
    )
    latest: bool = Field(description="The newest row of its evaluator on this run.")
    score: float | None
    label: str | None
    explanation: str | None = Field(
        description="Null (`explanation_withheld`) for human/judge text without `payload.read`."
    )
    explanation_withheld: bool
    metrics: dict[str, Any]
    evidence_event_ids: list[str]
    metadata: dict[str, Any] = Field(
        description="Judge model, template version, tokens and cost; `raw` needs `payload.read`."
    )
    metadata_withheld: list[str]
    error_code: str | None
    error: str | None = Field(description="Fixed text of a failure; needs `payload.read`.")
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class EvaluationPage(BaseModel):
    items: list[EvaluationOut]
    truncated: bool = Field(description="True when older rows were left out (newest first).")

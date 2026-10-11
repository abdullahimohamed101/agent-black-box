"""HTTP shapes of the evaluation routes. Byte limits live in validators, ahead of the database."""

from datetime import datetime
from typing import Annotated, Any, Literal

from abb_event_schema.ids import IdKind, id_pattern
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

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

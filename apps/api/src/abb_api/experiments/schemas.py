"""HTTP shapes of the experiment routes."""

import json
from datetime import datetime
from typing import Annotated, Literal

from abb_event_schema.ids import IdKind, id_pattern
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

MAX_VARIANT_KEYS = 32
MAX_CONFIG_BYTES = 8_192
Name = Annotated[
    str, StringConstraints(min_length=1, max_length=128, pattern=r"^[^\x00-\x1f\x7f]+$")
]
Text = Annotated[
    str, StringConstraints(max_length=2_000, pattern=r"^[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f]*$")
]
Label = Annotated[str, StringConstraints(max_length=128, pattern=r"^[^\x00-\x1f\x7f]*$")]
VariantName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")]
ConfigKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")]
ConfigValue = Annotated[str, StringConstraints(max_length=256, pattern=r"^[^\x00-\x1f\x7f]*$")]
EvaluatorRef = Annotated[str, StringConstraints(pattern=id_pattern(IdKind.EVALUATOR))]


class ExperimentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    project_id: str = Field(pattern=id_pattern(IdKind.PROJECT))
    description: Text | None = None
    dataset_label: Label | None = Field(
        default=None, description="A label for the task set; this product does not run tasks."
    )
    evaluator_ids: list[EvaluatorRef] = Field(
        default_factory=list, max_length=20, description="Evaluated by `evaluate`; up to 20."
    )

    @field_validator("evaluator_ids")
    @classmethod
    def _distinct(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class VariantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: VariantName
    variant_role: Literal["baseline", "candidate"] = Field(alias="role")
    config: dict[ConfigKey, ConfigValue] = Field(
        default_factory=dict,
        max_length=MAX_VARIANT_KEYS,
        description=(
            "Declared settings of the variant (model, prompt version, ...). Flat, at most 32 "
            "keys, values at most 256 characters. Visible to every reader: do not put secrets, "
            "prompts or paths here."
        ),
    )

    @field_validator("config")
    @classmethod
    def _bounded(cls, value: dict[str, str]) -> dict[str, str]:
        # The column CHECK is 8,192 bytes of jsonb text; refuse before the database does.
        rendered = json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
        if len(rendered.encode("utf-8")) > MAX_CONFIG_BYTES:
            raise ValueError("config is too large")
        return value


class AttachIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(pattern=id_pattern(IdKind.RUN))
    variant_id: str = Field(pattern=id_pattern(IdKind.VARIANT))


class VariantOut(BaseModel):
    id: str
    name: str
    variant_role: Literal["baseline", "candidate"] = Field(serialization_alias="role")
    config: dict[str, str]
    run_count: int
    changed_variables: list[str] | None = Field(
        description="Keys that differ from the baseline (a missing key counts); null for it."
    )
    controlled: bool | None = Field(
        description="True when exactly one variable changed against the baseline (spec §86)."
    )


class WarningOut(BaseModel):
    code: Literal[
        "not_single_variable", "variant_models_vary", "variant_agents_vary", "unevaluated_runs"
    ]
    variant_id: str | None
    keys: list[str] = Field(default_factory=list)
    count: int | None = None


class Distribution(BaseModel):
    mean: float | None
    median: float | None
    p95: float | None


class EvaluatorMetricsOut(BaseModel):
    evaluator_id: str
    completed: int
    failed: int
    mean_score: float | None
    pass_rate: float | None


class VariantMetricsOut(BaseModel):
    variant_id: str
    runs: int
    finished: int = Field(description="Runs with a terminal status; the figures below use them.")
    success_rate: float | None
    cost_usd: Distribution
    duration_ms: Distribution
    retry_rate: float | None = Field(description="Share of finished runs with at least one retry.")
    tool_calls_mean: float | None
    failure_categories: dict[str, int] = Field(
        description="Finished runs by non-success status; `recovered`: successes with errors."
    )
    evaluations: list[EvaluatorMetricsOut]


class EvaluatorInfoOut(BaseModel):
    id: str
    name: str
    kind: Literal["deterministic", "metric", "human", "llm_judge"]
    ground_truth: bool
    archived: bool


class AttachedRunOut(BaseModel):
    run_id: str
    variant_id: str
    status: str
    started_at: datetime
    added_at: datetime


class ExperimentSummaryOut(BaseModel):
    id: str
    project_id: str
    name: str
    description: str | None
    dataset_label: str | None
    variant_count: int
    run_count: int
    created_at: datetime
    archived_at: datetime | None


class ExperimentPage(BaseModel):
    items: list[ExperimentSummaryOut]


class ExperimentOut(BaseModel):
    id: str
    project_id: str
    name: str
    description: str | None
    dataset_label: str | None
    evaluators: list[EvaluatorInfoOut]
    variants: list[VariantOut]
    warnings: list[WarningOut]
    metrics: list[VariantMetricsOut]
    runs: list[AttachedRunOut]
    created_at: datetime
    archived_at: datetime | None


SkipReason = Literal["missing", "archived", "project_mismatch", "human", "judge_not_configured"]


class SkippedEvaluatorOut(BaseModel):
    evaluator_id: str
    reason: SkipReason


class EvaluateOut(BaseModel):
    enqueued: int = Field(description="New pending evaluations created by this call.")
    remaining: int = Field(description="Pairs still to start; call again.")
    jobs: int = Field(description="Runs whose evaluation job was queued or already waiting.")
    at_limit: int = Field(description="Pairs skipped: 20 results already exist for the evaluator.")
    skipped_evaluators: list[SkippedEvaluatorOut]

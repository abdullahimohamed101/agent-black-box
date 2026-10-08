"""Read models of the analytics API. Money is USD, rounded to 9 places; rates are 0..1 or null.

Rates are null (not 0) when there is nothing to divide by: "no data" is not "0% failures".
"""

from datetime import date, datetime

from pydantic import BaseModel, Field


class Window(BaseModel):
    start: datetime = Field(
        description="Inclusive UTC midnight. Windows are whole UTC days; requests snap outward."
    )
    end: datetime = Field(description="Exclusive, UTC midnight.")


class Percentiles(BaseModel):
    """Approximate: read from duration histograms with about 10% wide buckets (ADR-043)."""

    count: int
    p50_ms: float | None
    p95_ms: float | None


class RunCounts(BaseModel):
    total: int
    active: int = Field(description="Queued, running or waiting.")
    finished: int = Field(description="Success, failed, timed out or blocked (cancelled excluded).")
    success: int
    failed: int
    timed_out: int
    blocked: int
    cancelled: int


class Rates(BaseModel):
    success_rate: float | None = Field(description="success / finished")
    failure_rate: float | None = Field(description="(failed + blocked) / finished")
    timeout_rate: float | None = Field(description="timed out / finished")
    retry_rate: float | None = Field(description="runs with at least one retry / runs")


class CostHeadline(BaseModel):
    total_usd: float
    per_run_usd: float | None
    per_successful_run_usd: float | None
    retry_usd: float = Field(description="Cost of model calls made after a retry (ADR-042).")
    retry_share: float | None = Field(description="retry_usd / total_usd")
    unpriced_calls: int = Field(description="Model calls with no usable cost; counted as $0.")
    unrebuilt_runs: int = Field(
        description="Runs summarized before cost lines existed; `rebuild-costs` fills them in."
    )


class Behaviour(BaseModel):
    avg_llm_calls: float | None
    avg_tool_calls: float | None
    avg_retries: float | None
    avg_files_modified: float | None


class SpanRate(BaseModel):
    calls: int = Field(description="Spans that finished with a status.")
    success_rate: float | None


class Summary(BaseModel):
    window: Window
    runs: RunCounts
    rates: Rates
    cost: CostHeadline
    run_latency: Percentiles
    behaviour: Behaviour
    tools: SpanRate
    llm: SpanRate = Field(
        description="Model calls; `1 - success_rate` is the request failure rate."
    )
    active_agents: list[str] = Field(description="Agents with a run in progress (at most 10).")


class CostByDay(BaseModel):
    day: date
    cost_usd: float
    retry_usd: float
    runs: int


class CostByAgent(BaseModel):
    agent: str
    cost_usd: float
    calls: int


class CostByModel(BaseModel):
    provider: str | None
    model: str | None
    cost_usd: float
    calls: int
    input_tokens: int
    output_tokens: int
    unpriced_calls: int


class CostBySource(BaseModel):
    source: str = Field(description="provider_reported | estimated | client_estimate | unpriced")
    cost_usd: float
    calls: int


class CostByProject(BaseModel):
    project_id: str
    cost_usd: float
    runs: int


class OtherBucket(BaseModel):
    """Everything beyond the top-N groups: response size does not depend on how many names exist."""

    groups: int = Field(description="How many groups are folded into this bucket.")
    calls: int
    cost_usd: float = 0.0


class ExpensiveRun(BaseModel):
    run_id: str
    project_id: str
    name: str | None
    agent: str | None
    status: str
    started_at: datetime
    cost_usd: float
    retry_usd: float
    retry_count: int


class RetryBreakdown(BaseModel):
    """Spec §24: how much of the cost came from retries."""

    total_usd: float
    initial_usd: float
    retry_usd: float
    retry_share: float | None
    runs_with_retry_cost: int
    retries_unattributed: int = Field(
        description="`retry.attempted` events with no span id: counted, but no cost attributed."
    )


class CostReport(BaseModel):
    window: Window
    headline: CostHeadline
    retries: RetryBreakdown
    by_day: list[CostByDay]
    by_agent: list[CostByAgent]
    by_agent_other: OtherBucket | None
    by_model: list[CostByModel]
    by_model_other: OtherBucket | None
    by_source: list[CostBySource]
    by_project: list[CostByProject] | None = Field(
        description="Only when the query is not limited to one project."
    )
    expensive_runs: list[ExpensiveRun]


class FailureDay(BaseModel):
    day: date
    finished: int
    success: int
    failed: int
    timed_out: int
    blocked: int
    failure_rate: float | None


class ToolReliability(BaseModel):
    name: str
    calls: int
    success_rate: float | None
    p95_ms: float | None


class RetryHeavyRun(BaseModel):
    run_id: str
    project_id: str
    name: str | None
    agent: str | None
    status: str
    started_at: datetime
    retry_count: int
    retry_usd: float
    cost_usd: float


class ReliabilityReport(BaseModel):
    window: Window
    runs: RunCounts
    rates: Rates
    failure_trend: list[FailureDay]
    tools: list[ToolReliability]
    tools_other: OtherBucket | None
    retry_heavy_runs: list[RetryHeavyRun]


class SlowOperation(BaseModel):
    kind: str
    name: str | None
    calls: int
    p50_ms: float | None
    p95_ms: float | None


class PerformanceReport(BaseModel):
    window: Window
    run: Percentiles
    llm: Percentiles
    tool: Percentiles
    slow_operations: list[SlowOperation]

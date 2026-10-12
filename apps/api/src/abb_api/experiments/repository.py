"""Experiments, variants, attached runs and the metrics read-model. Every method carries the
tenant (INV-3); a project-bound caller sees only its project's experiments.

The metrics statement is a read-model over `runs` and `evaluations` by design (ADR-090): one SQL
statement with exact percentiles over at most 500 runs, never over events.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.db import tables as t
from abb_api.tenancy import TenantContext

TERMINAL = ("SUCCESS", "FAILED", "CANCELLED", "TIMED_OUT", "BLOCKED")


@dataclass(frozen=True)
class ExperimentRecord:
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str | None
    dataset_label: str | None
    evaluator_ids: list[str]
    created_by: uuid.UUID | None
    created_at: datetime
    archived_at: datetime | None


@dataclass(frozen=True)
class VariantRecord:
    id: uuid.UUID
    experiment_id: uuid.UUID
    name: str
    variant_role: str  # `baseline` | `candidate` (the column and the JSON field are `role`)
    config: dict[str, str]
    created_at: datetime


@dataclass(frozen=True)
class AttachedRun:
    run_id: uuid.UUID
    variant_id: uuid.UUID
    added_at: datetime
    status: str
    started_at: datetime
    agent_slug: str | None


@dataclass(frozen=True)
class VariantMetrics:
    variant_id: uuid.UUID
    runs: int
    finished: int
    success_rate: float | None
    cost_mean: float | None
    cost_median: float | None
    cost_p95: float | None
    duration_mean: float | None
    duration_median: float | None
    duration_p95: float | None
    retry_rate: float | None
    tool_calls_mean: float | None
    failure_categories: dict[str, int]
    models_distinct: int
    agents_distinct: int
    evaluations: list[dict[str, Any]]


@dataclass(frozen=True)
class PairState:
    run_id: uuid.UUID
    evaluator_id: uuid.UUID
    total: int
    open: int
    completed: int


_X = t.experiments.c
_V = t.experiment_variants.c
_R = t.experiment_runs.c
_EXPERIMENT_COLUMNS = (
    _X.id, _X.project_id, _X.name, _X.description, _X.dataset_label, _X.evaluator_ids,
    _X.created_by, _X.created_at, _X.archived_at,
)  # fmt: skip
_VARIANT_COLUMNS = (_V.id, _V.experiment_id, _V.name, _V["role"], _V.config, _V.created_at)

# One statement: per variant the aggregates over its finished runs, the failure categories and the
# latest terminal result of every evaluator per run. `percentile_cont` is exact (linear).
_METRICS = text(
    """
WITH member AS (
  SELECT er.variant_id, r.id AS run_id, r.status, r.cost_usd::float8 AS cost,
         r.duration_ms, r.retry_count, r.tool_calls, r.agent_slug,
         COALESCE((r.summary->>'error_count')::int, 0) AS error_count,
         r.summary->'models' AS models
  FROM experiment_runs er
  JOIN runs r ON r.workspace_id = er.workspace_id AND r.id = er.run_id
  WHERE er.workspace_id = :ws AND er.experiment_id = :exp
),
done AS (SELECT * FROM member WHERE status = ANY(:terminal)),
agg AS (
  SELECT m.variant_id, count(*) AS runs,
         count(d.run_id) AS finished,
         avg((d.status = 'SUCCESS')::int)::float8 AS success_rate,
         avg(d.cost) AS cost_mean,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY d.cost) AS cost_median,
         percentile_cont(0.95) WITHIN GROUP (ORDER BY d.cost) AS cost_p95,
         avg(d.duration_ms) AS duration_mean,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY d.duration_ms) AS duration_median,
         percentile_cont(0.95) WITHIN GROUP (ORDER BY d.duration_ms) AS duration_p95,
         avg((d.retry_count > 0)::int)::float8 AS retry_rate,
         avg(d.tool_calls)::float8 AS tool_calls_mean,
         count(DISTINCT m.models) AS models_distinct,
         count(DISTINCT m.agent_slug) AS agents_distinct
  FROM member m LEFT JOIN done d ON d.run_id = m.run_id
  GROUP BY m.variant_id
),
failures AS (
  SELECT variant_id, jsonb_object_agg(category, n) AS categories
  FROM (
    SELECT variant_id,
           CASE WHEN status = 'SUCCESS' THEN 'recovered' ELSE lower(status) END AS category,
           count(*) AS n
    FROM done WHERE status <> 'SUCCESS' OR error_count > 0
    GROUP BY 1, 2
  ) s GROUP BY variant_id
),
latest AS (
  SELECT DISTINCT ON (e.run_id, e.evaluator_id)
         m.variant_id, e.evaluator_id, e.state, e.score, e.label
  FROM evaluations e JOIN member m ON m.run_id = e.run_id
  WHERE e.workspace_id = :ws AND e.evaluator_id = ANY(:evaluators)
    AND e.state IN ('completed', 'failed')
  ORDER BY e.run_id, e.evaluator_id, e.created_at DESC, e.id DESC
),
scores AS (
  SELECT variant_id, jsonb_agg(jsonb_build_object(
           'evaluator_id', evaluator_id,
           'completed', completed, 'failed', failed,
           'mean_score', mean_score, 'pass_rate', pass_rate
         ) ORDER BY evaluator_id) AS evaluations
  FROM (
    SELECT variant_id, evaluator_id,
           count(*) FILTER (WHERE state = 'completed') AS completed,
           count(*) FILTER (WHERE state = 'failed') AS failed,
           avg(score) FILTER (WHERE state = 'completed') AS mean_score,
           avg((label = 'pass')::int) FILTER (WHERE state = 'completed') AS pass_rate
    FROM latest GROUP BY variant_id, evaluator_id
  ) g GROUP BY variant_id
)
SELECT a.*, f.categories, s.evaluations
FROM agg a
LEFT JOIN failures f ON f.variant_id = a.variant_id
LEFT JOIN scores s ON s.variant_id = a.variant_id
ORDER BY a.variant_id
"""
)

_PAIRS = text(
    """
SELECT er.run_id, ev.id AS evaluator_id,
       count(e.id) AS total,
       count(e.id) FILTER (WHERE e.state IN ('pending', 'running')) AS open,
       count(e.id) FILTER (WHERE e.state = 'completed') AS completed
FROM experiment_runs er
CROSS JOIN unnest(CAST(:evaluators AS uuid[])) AS ev(id)
LEFT JOIN evaluations e
  ON e.workspace_id = er.workspace_id AND e.run_id = er.run_id AND e.evaluator_id = ev.id
WHERE er.workspace_id = :ws AND er.experiment_id = :exp
  AND (CAST(:runs AS uuid[]) IS NULL OR er.run_id = ANY(CAST(:runs AS uuid[])))
GROUP BY er.run_id, ev.id, er.added_at
ORDER BY er.added_at, er.run_id
"""
)


class ExperimentRepository:
    def __init__(
        self, conn: AsyncConnection, tenant: TenantContext, project_scope: uuid.UUID | None = None
    ) -> None:
        self._conn = conn
        self._tenant = tenant
        self._scope = project_scope

    # ----------------------------------------------------------------- locks and counts

    async def _lock(self, key: str) -> None:
        await self._conn.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0))))

    async def lock_workspace(self) -> None:
        await self._lock(f"ws:{self._tenant.workspace_id}:experiments")

    async def lock_experiment(self, experiment_id: uuid.UUID) -> None:
        await self._lock(f"exp:{experiment_id}")

    async def count_active(self) -> int:
        return int(
            (
                await self._conn.execute(
                    select(func.count()).where(
                        _X.workspace_id == self._tenant.workspace_id, _X.archived_at.is_(None)
                    )
                )
            ).scalar_one()
        )

    async def count_variants(self, experiment_id: uuid.UUID) -> int:
        return int(
            (
                await self._conn.execute(
                    select(func.count()).where(
                        _V.workspace_id == self._tenant.workspace_id,
                        _V.experiment_id == experiment_id,
                    )
                )
            ).scalar_one()
        )

    async def count_runs(self, experiment_id: uuid.UUID) -> int:
        return int(
            (
                await self._conn.execute(
                    select(func.count()).where(
                        _R.workspace_id == self._tenant.workspace_id,
                        _R.experiment_id == experiment_id,
                    )
                )
            ).scalar_one()
        )

    # ----------------------------------------------------------------- experiments

    def _base(self) -> Any:
        statement = select(*_EXPERIMENT_COLUMNS).where(_X.workspace_id == self._tenant.workspace_id)
        if self._scope is not None:
            statement = statement.where(_X.project_id == self._scope)
        return statement

    async def create(
        self,
        experiment_id: uuid.UUID,
        *,
        project_id: uuid.UUID,
        name: str,
        description: str | None,
        dataset_label: str | None,
        evaluator_ids: list[str],
        created_by: uuid.UUID | None,
    ) -> ExperimentRecord:
        await self._conn.execute(
            t.experiments.insert().values(
                workspace_id=self._tenant.workspace_id,
                id=experiment_id,
                project_id=project_id,
                name=name,
                description=description,
                dataset_label=dataset_label,
                evaluator_ids=evaluator_ids,
                created_by=created_by,
            )
        )
        created = await self.get(experiment_id)
        assert created is not None
        return created

    async def get(self, experiment_id: uuid.UUID) -> ExperimentRecord | None:
        row = (await self._conn.execute(self._base().where(_X.id == experiment_id))).first()
        return ExperimentRecord(**row._mapping) if row else None

    async def listing(
        self, *, project_id: uuid.UUID | None, include_archived: bool, limit: int
    ) -> list[ExperimentRecord]:
        statement = self._base()
        if project_id is not None:
            statement = statement.where(_X.project_id == project_id)
        if not include_archived:
            statement = statement.where(_X.archived_at.is_(None))
        rows = await self._conn.execute(
            statement.order_by(_X.created_at.desc(), _X.id.desc()).limit(limit)
        )
        return [ExperimentRecord(**r._mapping) for r in rows]

    async def archive(self, experiment_id: uuid.UUID) -> None:
        await self._conn.execute(
            update(t.experiments)
            .where(
                _X.workspace_id == self._tenant.workspace_id,
                _X.id == experiment_id,
                _X.archived_at.is_(None),
            )
            .values(archived_at=func.now())
        )

    async def counts(self, experiment_ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[int, int]]:
        """(variants, runs) per experiment, for the listing."""
        if not experiment_ids:
            return {}
        variants = await self._conn.execute(
            select(_V.experiment_id, func.count())
            .where(
                _V.workspace_id == self._tenant.workspace_id, _V.experiment_id.in_(experiment_ids)
            )
            .group_by(_V.experiment_id)
        )
        runs = await self._conn.execute(
            select(_R.experiment_id, func.count())
            .where(
                _R.workspace_id == self._tenant.workspace_id, _R.experiment_id.in_(experiment_ids)
            )
            .group_by(_R.experiment_id)
        )
        found = {i: (0, 0) for i in experiment_ids}
        for experiment_id, n in variants:
            found[experiment_id] = (int(n), found[experiment_id][1])
        for experiment_id, n in runs:
            found[experiment_id] = (found[experiment_id][0], int(n))
        return found

    # ----------------------------------------------------------------- variants

    async def variants(self, experiment_id: uuid.UUID) -> list[VariantRecord]:
        rows = await self._conn.execute(
            select(*_VARIANT_COLUMNS)
            .where(_V.workspace_id == self._tenant.workspace_id, _V.experiment_id == experiment_id)
            .order_by(_V.created_at, _V.id)
        )
        return [
            VariantRecord(r.id, r.experiment_id, r.name, r._mapping["role"], r.config, r.created_at)
            for r in rows
        ]

    async def create_variant(
        self,
        variant_id: uuid.UUID,
        *,
        experiment_id: uuid.UUID,
        name: str,
        role: str,
        config: dict[str, str],
    ) -> None:
        await self._conn.execute(
            t.experiment_variants.insert().values(
                workspace_id=self._tenant.workspace_id,
                id=variant_id,
                experiment_id=experiment_id,
                name=name,
                role=role,
                config=config,
            )
        )

    # ----------------------------------------------------------------- attached runs

    async def attached(self, experiment_id: uuid.UUID) -> list[AttachedRun]:
        rows = await self._conn.execute(
            select(_R.run_id, _R.variant_id, _R.added_at, t.runs.c.status, t.runs.c.started_at,
                   t.runs.c.agent_slug)
            .join(
                t.runs,
                (t.runs.c.workspace_id == _R.workspace_id) & (t.runs.c.id == _R.run_id),
            )
            .where(_R.workspace_id == self._tenant.workspace_id, _R.experiment_id == experiment_id)
            .order_by(_R.added_at, _R.run_id)
        )  # fmt: skip
        return [AttachedRun(**r._mapping) for r in rows]

    async def attachment(self, experiment_id: uuid.UUID, run_id: uuid.UUID) -> uuid.UUID | None:
        """The variant the run is attached to in this experiment, if any."""
        return (
            await self._conn.execute(
                select(_R.variant_id).where(
                    _R.workspace_id == self._tenant.workspace_id,
                    _R.experiment_id == experiment_id,
                    _R.run_id == run_id,
                )
            )
        ).scalar_one_or_none()

    async def attach(
        self,
        experiment_id: uuid.UUID,
        run_id: uuid.UUID,
        variant_id: uuid.UUID,
        added_by: uuid.UUID | None,
    ) -> None:
        await self._conn.execute(
            t.experiment_runs.insert().values(
                workspace_id=self._tenant.workspace_id,
                experiment_id=experiment_id,
                run_id=run_id,
                variant_id=variant_id,
                added_by=added_by,
            )
        )

    async def detach(self, experiment_id: uuid.UUID, run_id: uuid.UUID) -> bool:
        result = await self._conn.execute(
            delete(t.experiment_runs).where(
                _R.workspace_id == self._tenant.workspace_id,
                _R.experiment_id == experiment_id,
                _R.run_id == run_id,
            )
        )
        return bool(result.rowcount == 1)

    # ----------------------------------------------------------------- read-model

    async def metrics(
        self, experiment_id: uuid.UUID, evaluator_ids: list[uuid.UUID], timeout_ms: int
    ) -> list[VariantMetrics]:
        """The read-model statement under a statement timeout (the analytics bound)."""
        await self._conn.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
        rows = await self._conn.execute(
            _METRICS,
            {
                "ws": self._tenant.workspace_id,
                "exp": experiment_id,
                "terminal": list(TERMINAL),
                "evaluators": evaluator_ids,
            },
        )
        found = []
        for r in rows:
            found.append(
                VariantMetrics(
                    variant_id=r.variant_id,
                    runs=int(r.runs),
                    finished=int(r.finished),
                    success_rate=r.success_rate,
                    cost_mean=r.cost_mean,
                    cost_median=r.cost_median,
                    cost_p95=r.cost_p95,
                    duration_mean=r.duration_mean,
                    duration_median=r.duration_median,
                    duration_p95=r.duration_p95,
                    retry_rate=r.retry_rate,
                    tool_calls_mean=r.tool_calls_mean,
                    failure_categories=dict(r.categories or {}),
                    models_distinct=int(r.models_distinct),
                    agents_distinct=int(r.agents_distinct),
                    evaluations=list(r.evaluations or []),
                )
            )
        return found

    async def pair_states(
        self,
        experiment_id: uuid.UUID,
        evaluator_ids: list[uuid.UUID],
        run_ids: list[uuid.UUID] | None = None,
    ) -> list[PairState]:
        """Per (run, evaluator) of the experiment: rows in all, non-terminal and completed."""
        rows = await self._conn.execute(
            _PAIRS,
            {
                "ws": self._tenant.workspace_id,
                "exp": experiment_id,
                "evaluators": evaluator_ids,
                "runs": run_ids,
            },
        )
        return [
            PairState(r.run_id, r.evaluator_id, int(r.total), int(r.open), int(r.completed))
            for r in rows
        ]

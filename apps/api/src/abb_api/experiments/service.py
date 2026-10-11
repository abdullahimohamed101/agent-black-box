"""Experiment use cases: bounds under advisory locks, the single-variable check, metrics,
evaluate-all (ADR-090, spec §86).

Bounds (all `409 LIMIT_REACHED` with `details.limit`): 200 experiments per workspace, 10 variants,
500 runs and 20 evaluators per experiment. Each count-then-insert runs under an advisory lock keyed
by workspace or experiment, never the run row lock the summarizer holds. The variant configuration
is declared by the user and visible to every reader.
"""

import uuid
from datetime import timedelta
from typing import Any

from abb_event_schema.ids import IdKind
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from abb_api.analytics.postgres import analytics_timeout
from abb_api.authz.principal import Principal
from abb_api.core.config import Settings
from abb_api.core.errors import AppError, ErrorCategory
from abb_api.evaluations.repository import (
    EvaluationReads,
    EvaluationRepository,
    EvaluatorRecord,
    EvaluatorRepository,
)
from abb_api.evaluations.runs import MAX_EVALUATIONS_PER_EVALUATOR
from abb_api.evaluations.service import evaluator_not_found
from abb_api.experiments.repository import (
    ExperimentRecord,
    ExperimentRepository,
    VariantMetrics,
    VariantRecord,
)
from abb_api.experiments.schemas import (
    AttachedRunOut,
    AttachIn,
    Distribution,
    EvaluateOut,
    EvaluatorInfoOut,
    EvaluatorMetricsOut,
    ExperimentIn,
    ExperimentOut,
    ExperimentPage,
    ExperimentSummaryOut,
    SkippedEvaluatorOut,
    SkipReason,
    VariantIn,
    VariantMetricsOut,
    VariantOut,
    WarningOut,
)
from abb_api.ids import new_uuid, parse_public_id, public_id
from abb_api.jobs.outbox import EVALUATE_RUN, OutboxRepository, evaluate_key
from abb_api.projects.access import authorise_project
from abb_api.runs.queries import RunQueries
from abb_api.runs.service import run_not_found

MAX_EXPERIMENTS = 200
MAX_VARIANTS = 10
MAX_RUNS = 500
MAX_NEW_EVALUATIONS = 500  # new pending rows per `evaluate` call
LIST_LIMIT = 200


def _error(code: str, message: str, status: int, category: ErrorCategory, **d: Any) -> AppError:
    return AppError(code, message, category=category, status_code=status, details=d)


def _conflict(code: str, message: str, **details: Any) -> AppError:
    return _error(code, message, 409, ErrorCategory.CONFLICT, **details)


def _unprocessable(code: str, message: str) -> AppError:
    return _error(code, message, 422, ErrorCategory.VALIDATION)


def _not_found(code: str, what: str) -> AppError:
    return _error(code, f"{what} not found.", 404, ErrorCategory.NOT_FOUND)


def experiment_not_found() -> AppError:
    return _not_found("EXPERIMENT_NOT_FOUND", "Experiment")


def variant_not_found() -> AppError:
    return _not_found("VARIANT_NOT_FOUND", "Variant")


def _limit(name: str, text: str) -> AppError:
    return _conflict("LIMIT_REACHED", text, limit=name)


def changed_variables(baseline: dict[str, str], candidate: dict[str, str]) -> list[str]:
    """Keys whose value differs from the baseline's; a key missing on either side counts."""
    return sorted(
        k for k in baseline.keys() | candidate.keys() if baseline.get(k) != candidate.get(k)
    )


def _distribution(mean: float | None, median: float | None, p95: float | None) -> Distribution:
    return Distribution(mean=mean, median=median, p95=p95)


def _metrics_out(m: VariantMetrics) -> VariantMetricsOut:
    return VariantMetricsOut(
        variant_id=public_id(IdKind.VARIANT, m.variant_id),
        runs=m.runs,
        finished=m.finished,
        success_rate=m.success_rate,
        cost_usd=_distribution(m.cost_mean, m.cost_median, m.cost_p95),
        duration_ms=_distribution(m.duration_mean, m.duration_median, m.duration_p95),
        retry_rate=m.retry_rate,
        tool_calls_mean=m.tool_calls_mean,
        failure_categories=dict(sorted(m.failure_categories.items())),
        evaluations=[
            EvaluatorMetricsOut(
                evaluator_id=public_id(IdKind.EVALUATOR, uuid.UUID(str(e["evaluator_id"]))),
                completed=int(e["completed"]),
                failed=int(e["failed"]),
                mean_score=e["mean_score"],
                pass_rate=e["pass_rate"],
            )
            for e in m.evaluations
        ],
    )


def _warnings(
    variants: list[VariantRecord],
    changed: dict[uuid.UUID, list[str]],
    metrics: dict[uuid.UUID, VariantMetrics],
    evaluators: int,
) -> list[WarningOut]:
    found: list[WarningOut] = []
    for v in variants:
        if v.variant_role == "candidate" and len(changed[v.id]) != 1:
            found.append(
                WarningOut(
                    code="not_single_variable",
                    variant_id=public_id(IdKind.VARIANT, v.id),
                    keys=changed[v.id],
                )
            )
    for v in variants:
        m = metrics.get(v.id)
        if m is not None and m.models_distinct > 1:
            found.append(
                WarningOut(code="variant_models_vary", variant_id=public_id(IdKind.VARIANT, v.id))
            )
        if m is not None and m.agents_distinct > 1:
            found.append(
                WarningOut(code="variant_agents_vary", variant_id=public_id(IdKind.VARIANT, v.id))
            )
    missing = 0
    for m in metrics.values():
        done = sum(int(e["completed"]) + int(e["failed"]) for e in m.evaluations)
        missing += max(0, m.runs * evaluators - done)
    if missing:
        found.append(WarningOut(code="unevaluated_runs", variant_id=None, count=missing))
    return found


class ExperimentService:
    def __init__(self, engine: AsyncEngine, settings: Settings) -> None:
        self._engine = engine
        self._settings = settings

    # ---------------------------------------------------------------- helpers

    @staticmethod
    async def _experiment(
        conn: AsyncConnection,
        principal: Principal,
        experiment_id: str,
        *,
        writable: bool = False,
    ) -> ExperimentRecord:
        parsed = parse_public_id(IdKind.EXPERIMENT, experiment_id)
        record = (
            await ExperimentRepository(conn, principal.tenant, principal.project_id).get(parsed)
            if parsed
            else None
        )
        if record is None:
            raise experiment_not_found()
        if writable and record.archived_at is not None:
            raise _conflict("EXPERIMENT_ARCHIVED", "The experiment is archived.")
        return record

    @staticmethod
    def _repo(conn: AsyncConnection, principal: Principal) -> ExperimentRepository:
        return ExperimentRepository(conn, principal.tenant, principal.project_id)

    async def _metrics(
        self, repo: ExperimentRepository, experiment_id: uuid.UUID, evaluators: list[uuid.UUID]
    ) -> list[VariantMetrics]:
        timeout_ms = int(self._settings.analytics_timeout_seconds * 1000)
        try:
            return await repo.metrics(experiment_id, evaluators, timeout_ms)
        except DBAPIError as exc:
            if "statement timeout" in str(exc.orig) or "canceling statement" in str(exc.orig):
                raise analytics_timeout() from None
            raise

    # ---------------------------------------------------------------- experiments

    async def create(self, principal: Principal, body: ExperimentIn) -> ExperimentOut:
        async with self._engine.begin() as conn:
            project = await authorise_project(conn, principal, body.project_id)
            assert project is not None
            evaluators = EvaluatorRepository(conn, principal.tenant, principal.project_id)
            for ref in body.evaluator_ids:
                parsed = parse_public_id(IdKind.EVALUATOR, ref)
                record = await evaluators.get(parsed) if parsed else None
                if record is None:
                    raise evaluator_not_found()
                if record.archived_at is not None:
                    raise _conflict("EVALUATOR_ARCHIVED", "An evaluator is archived.")
                if record.project_id is not None and record.project_id != project:
                    raise _unprocessable(
                        "EVALUATOR_PROJECT_MISMATCH", "An evaluator belongs to another project."
                    )
            repo = self._repo(conn, principal)
            await repo.lock_workspace()
            if await repo.count_active() >= MAX_EXPERIMENTS:
                raise _limit(
                    "experiments", f"A workspace holds at most {MAX_EXPERIMENTS} experiments."
                )
            experiment_id = new_uuid(IdKind.EXPERIMENT)
            await repo.create(
                experiment_id,
                project_id=project,
                name=body.name,
                description=body.description,
                dataset_label=body.dataset_label,
                evaluator_ids=body.evaluator_ids,
                created_by=principal.user_id,
            )
        return await self.get(principal, public_id(IdKind.EXPERIMENT, experiment_id))

    async def list_experiments(
        self, principal: Principal, *, project_id: str | None, include_archived: bool
    ) -> ExperimentPage:
        async with self._engine.connect() as conn:
            scope = await authorise_project(conn, principal, project_id)
            repo = self._repo(conn, principal)
            records = await repo.listing(
                project_id=scope, include_archived=include_archived, limit=LIST_LIMIT
            )
            counts = await repo.counts([r.id for r in records])
        return ExperimentPage(
            items=[
                ExperimentSummaryOut(
                    id=public_id(IdKind.EXPERIMENT, r.id),
                    project_id=public_id(IdKind.PROJECT, r.project_id),
                    name=r.name,
                    description=r.description,
                    dataset_label=r.dataset_label,
                    variant_count=counts[r.id][0],
                    run_count=counts[r.id][1],
                    created_at=r.created_at,
                    archived_at=r.archived_at,
                )
                for r in records
            ]
        )

    async def get(self, principal: Principal, experiment_id: str) -> ExperimentOut:
        async with self._engine.connect() as conn:
            record = await self._experiment(conn, principal, experiment_id)
            repo = self._repo(conn, principal)
            variants = await repo.variants(record.id)
            attached = await repo.attached(record.id)
            refs = [parse_public_id(IdKind.EVALUATOR, e) for e in record.evaluator_ids]
            evaluator_ids = [r for r in refs if r is not None]
            evaluators = EvaluatorRepository(conn, principal.tenant, principal.project_id)
            known = [await evaluators.get(i) for i in evaluator_ids]
            metrics = await self._metrics(repo, record.id, evaluator_ids)
        by_variant = {m.variant_id: m for m in metrics}
        baseline = next((v for v in variants if v.variant_role == "baseline"), None)
        changed = {
            v.id: changed_variables(baseline.config, v.config)
            if baseline is not None and v.variant_role == "candidate"
            else []
            for v in variants
        }
        counts = {v.id: sum(1 for a in attached if a.variant_id == v.id) for v in variants}
        return ExperimentOut(
            id=public_id(IdKind.EXPERIMENT, record.id),
            project_id=public_id(IdKind.PROJECT, record.project_id),
            name=record.name,
            description=record.description,
            dataset_label=record.dataset_label,
            evaluators=[
                EvaluatorInfoOut(
                    id=public_id(IdKind.EVALUATOR, e.id),
                    name=e.name,
                    kind=e.kind,  # type: ignore[arg-type]
                    ground_truth=e.kind != "llm_judge",
                    archived=e.archived_at is not None,
                )
                for e in known
                if e is not None
            ],
            variants=[
                VariantOut(
                    id=public_id(IdKind.VARIANT, v.id),
                    name=v.name,
                    variant_role=v.variant_role,  # type: ignore[arg-type]
                    config=v.config,
                    run_count=counts[v.id],
                    changed_variables=changed[v.id] if v.variant_role == "candidate" else None,
                    controlled=len(changed[v.id]) == 1 if v.variant_role == "candidate" else None,
                )
                for v in variants
            ],
            warnings=_warnings(variants, changed, by_variant, len(evaluator_ids)),
            metrics=[_metrics_out(m) for m in metrics],
            runs=[
                AttachedRunOut(
                    run_id=public_id(IdKind.RUN, a.run_id),
                    variant_id=public_id(IdKind.VARIANT, a.variant_id),
                    status=a.status,
                    started_at=a.started_at,
                    added_at=a.added_at,
                )
                for a in attached
            ],
            created_at=record.created_at,
            archived_at=record.archived_at,
        )

    async def archive(self, principal: Principal, experiment_id: str) -> ExperimentRecord:
        async with self._engine.begin() as conn:
            record = await self._experiment(conn, principal, experiment_id)
            await self._repo(conn, principal).archive(record.id)
        return record

    # ---------------------------------------------------------------- variants

    async def add_variant(
        self, principal: Principal, experiment_id: str, body: VariantIn
    ) -> VariantOut:
        variant_id = new_uuid(IdKind.VARIANT)
        async with self._engine.begin() as conn:
            record = await self._experiment(conn, principal, experiment_id, writable=True)
            repo = self._repo(conn, principal)
            await repo.lock_experiment(record.id)
            existing = await repo.variants(record.id)
            if len(existing) >= MAX_VARIANTS:
                raise _limit("variants", f"An experiment holds at most {MAX_VARIANTS} variants.")
            baseline = next((v for v in existing if v.variant_role == "baseline"), None)
            if body.variant_role == "baseline" and baseline is not None:
                raise _conflict("BASELINE_EXISTS", "The experiment already has a baseline.")
            if body.variant_role == "candidate" and baseline is None:
                raise _conflict("BASELINE_REQUIRED", "Add the baseline variant first.")
            if any(v.name == body.name for v in existing):
                raise _conflict("VARIANT_NAME_TAKEN", "A variant with this name exists.")
            try:
                async with conn.begin_nested():
                    await repo.create_variant(
                        variant_id,
                        experiment_id=record.id,
                        name=body.name,
                        role=body.variant_role,
                        config=dict(body.config),
                    )
            except IntegrityError:
                raise _conflict("VARIANT_NAME_TAKEN", "A variant with this name exists.") from None
        changed = (
            changed_variables(baseline.config, dict(body.config))
            if baseline is not None and body.variant_role == "candidate"
            else None
        )
        return VariantOut(
            id=public_id(IdKind.VARIANT, variant_id),
            name=body.name,
            variant_role=body.variant_role,
            config=dict(body.config),
            run_count=0,
            changed_variables=changed,
            controlled=len(changed) == 1 if changed is not None else None,
        )

    # ---------------------------------------------------------------- runs

    async def attach(
        self, principal: Principal, experiment_id: str, body: AttachIn
    ) -> tuple[AttachedRunOut, bool]:
        async with self._engine.begin() as conn:
            record = await self._experiment(conn, principal, experiment_id, writable=True)
            repo = self._repo(conn, principal)
            variants = {v.id: v for v in await repo.variants(record.id)}
            variant_uuid = parse_public_id(IdKind.VARIANT, body.variant_id)
            if variant_uuid is None or variant_uuid not in variants:
                raise variant_not_found()
            run_uuid = parse_public_id(IdKind.RUN, body.run_id)
            run = (
                await RunQueries(conn, principal.tenant, principal.project_id).get(run_uuid)
                if run_uuid
                else None
            )
            if run is None:
                raise run_not_found()
            if run.project_id != record.project_id:
                raise _unprocessable(
                    "RUN_PROJECT_MISMATCH",
                    "The run belongs to another project than the experiment.",
                )
            await repo.lock_experiment(record.id)
            current = await repo.attachment(record.id, run.id)
            created = current is None
            if current is not None and current != variant_uuid:
                raise _conflict("RUN_ALREADY_ATTACHED", "The run is attached to another variant.")
            if created:
                if await repo.count_runs(record.id) >= MAX_RUNS:
                    raise _limit("runs", f"An experiment holds at most {MAX_RUNS} runs.")
                await repo.attach(record.id, run.id, variant_uuid, principal.user_id)
            attached = next(a for a in await repo.attached(record.id) if a.run_id == run.id)
        return (
            AttachedRunOut(
                run_id=public_id(IdKind.RUN, attached.run_id),
                variant_id=public_id(IdKind.VARIANT, attached.variant_id),
                status=attached.status,
                started_at=attached.started_at,
                added_at=attached.added_at,
            ),
            created,
        )

    async def detach(self, principal: Principal, experiment_id: str, run_id: str) -> uuid.UUID:
        async with self._engine.begin() as conn:
            record = await self._experiment(conn, principal, experiment_id, writable=True)
            parsed = parse_public_id(IdKind.RUN, run_id)
            repo = self._repo(conn, principal)
            if parsed is None or not await repo.detach(record.id, parsed):
                raise run_not_found()
        return parsed

    # ---------------------------------------------------------------- evaluate all

    def _skip_reason(
        self, found: EvaluatorRecord | None, record: ExperimentRecord
    ) -> SkipReason | None:
        if found is None:
            return "missing"
        if found.archived_at is not None:
            return "archived"
        if found.project_id is not None and found.project_id != record.project_id:
            return "project_mismatch"
        if found.kind == "human":
            return "human"
        if found.kind == "llm_judge" and self._settings.judge_provider == "none":
            return "judge_not_configured"
        return None

    async def _usable(
        self, conn: AsyncConnection, principal: Principal, record: ExperimentRecord
    ) -> tuple[list[EvaluatorRecord], list[SkippedEvaluatorOut]]:
        evaluators = EvaluatorRepository(conn, principal.tenant, principal.project_id)
        usable: list[EvaluatorRecord] = []
        skipped: list[SkippedEvaluatorOut] = []
        for ref in record.evaluator_ids:
            parsed = parse_public_id(IdKind.EVALUATOR, ref)
            found = await evaluators.get(parsed) if parsed else None
            reason = self._skip_reason(found, record)
            if reason is not None or found is None:
                skipped.append(SkippedEvaluatorOut(evaluator_id=ref, reason=reason or "missing"))
            else:
                usable.append(found)
        return usable, skipped

    async def evaluate(self, principal: Principal, experiment_id: str) -> EvaluateOut:
        """Start the evaluators of the experiment on every run that lacks a result.

        A (run, evaluator) pair is started when it has no non-terminal row and no completed row
        (a failed one is retried) and fewer than 20 rows. At most 500 pairs per call; one job per
        run, delayed so summaries of live runs go first; a repeat while rows are pending creates
        nothing."""
        async with self._engine.begin() as conn:
            record = await self._experiment(conn, principal, experiment_id, writable=True)
            repo = self._repo(conn, principal)
            await repo.lock_experiment(record.id)
            usable, skipped = await self._usable(conn, principal, record)
            if not usable:
                return EvaluateOut(
                    enqueued=0, remaining=0, jobs=0, at_limit=0, skipped_evaluators=skipped
                )
            order = {e.id: n for n, e in enumerate(usable)}
            ids = [e.id for e in usable]
            by_id = {e.id: e for e in usable}

            def wanted(states: list[Any]) -> tuple[list[Any], int]:
                free = [s for s in states if s.open == 0 and s.completed == 0]
                eligible = [s for s in free if s.total < MAX_EVALUATIONS_PER_EVALUATOR]
                return eligible, len(free) - len(eligible)

            first, at_limit = wanted(await repo.pair_states(record.id, ids))
            chosen_runs = sorted(
                {s.run_id for s in first[:MAX_NEW_EVALUATIONS]}, key=lambda u: u.bytes
            )
            reads = EvaluationReads(conn, principal.tenant)
            for run_id in chosen_runs:  # sorted: concurrent evaluate-all calls cannot deadlock
                await reads.lock_run(run_id)
            fresh, _ = wanted(await repo.pair_states(record.id, ids, chosen_runs))
            # Order: the runs in the order attached, evaluators in the experiment's order.
            fresh.sort(key=lambda s: (chosen_runs.index(s.run_id), order[s.evaluator_id]))
            batch = fresh[:MAX_NEW_EVALUATIONS]
            evaluations = EvaluationRepository(conn, principal.tenant)
            outbox = OutboxRepository(conn, principal.tenant)
            delay = (
                timedelta(seconds=self._settings.eval_batch_delay_seconds)
                if self._settings.eval_batch_delay_seconds > 0
                else None
            )
            for state in batch:
                await evaluations.insert_pending(
                    new_uuid(IdKind.EVALUATION),
                    run_id=state.run_id,
                    evaluator=by_id[state.evaluator_id],
                    requested_by=principal.user_id,
                )
            jobs = sorted({s.run_id for s in batch}, key=lambda u: u.bytes)
            for run_id in jobs:
                await outbox.enqueue(
                    job_type=EVALUATE_RUN,
                    dedupe_key=evaluate_key(principal.workspace_id, run_id),
                    payload={"run_id": str(run_id)},
                    delay=delay,
                )
            remaining = len(first) - len(batch)
        return EvaluateOut(
            enqueued=len(batch),
            remaining=max(0, remaining),
            jobs=len(jobs),
            at_limit=at_limit,
            skipped_evaluators=skipped,
        )

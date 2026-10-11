"""Evaluations of one run: trigger, human verdict, listing (D3, D7, D10).

A trigger inserts a `pending` row and the run's coalesced `evaluate_run` job in one transaction;
a repeat while a live row exists returns that row. Count-then-insert bounds run under a per-run
advisory lock (never the run row lock the summarizer holds). Free text of human and judge rows is
withheld from actors without `payload.read` (the explanation, `metadata.raw`, the failure text).
"""

from dataclasses import replace
from typing import Any

from abb_event_schema.ids import IdKind
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from abb_api.authz import actions
from abb_api.authz.principal import Principal
from abb_api.authz.service import holds
from abb_api.core.errors import AppError, ErrorCategory
from abb_api.evaluations.builtin import DEFAULT_LABELS
from abb_api.evaluations.repository import (
    EvaluationReads,
    EvaluationRecord,
    EvaluationRepository,
    EvaluatorRecord,
    EvaluatorRepository,
)
from abb_api.evaluations.schemas import EvaluationOut, EvaluationPage, TriggerIn, VerdictIn
from abb_api.evaluations.service import evaluator_not_found, judge_not_configured
from abb_api.ids import new_uuid, parse_public_id, public_id
from abb_api.jobs.outbox import EVALUATE_RUN, OutboxRepository, evaluate_key
from abb_api.runs.queries import RunQueries, RunRecord
from abb_api.runs.service import run_not_found

MAX_EVALUATIONS_PER_EVALUATOR = 20  # rows per (run, evaluator), D10
MAX_VERDICTS_PER_RUN = 50
LIST_LIMIT = 200
FREE_TEXT_KINDS = frozenset({"human", "llm_judge"})
# What a reader without `payload.read` keeps of a judge's metadata: scalars about the call.
VISIBLE_METADATA = frozenset(
    {
        "judge_model",
        "prompt_template_version",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "provider",
    }
)


def _fail(
    code: str, message: str, status: int, category: ErrorCategory, **details: Any
) -> AppError:
    return AppError(code, message, category=category, status_code=status, details=details)


def _conflict(code: str, message: str, **details: Any) -> AppError:
    return _fail(code, message, 409, ErrorCategory.CONFLICT, **details)


def _unprocessable(code: str, message: str, **details: Any) -> AppError:
    return _fail(code, message, 422, ErrorCategory.VALIDATION, **details)


def _derived(record: EvaluationRecord, job_state: str) -> EvaluationRecord:
    """A `pending` row whose job was dead-lettered, or that outlived the stale limit with no job,
    reads as failed. Nothing is written: the handler still owns the row (ADR-090)."""
    if record.state == "pending" and (
        job_state == "failed" or (job_state == "current" and record.stale)
    ):
        return replace(
            record,
            state="failed",
            error_code="JOB_FAILED",
            error="The evaluation job did not run.",
        )
    return record


def evaluation_out(record: EvaluationRecord, principal: Principal) -> EvaluationOut:
    reads = holds(principal, actions.PAYLOAD_READ)
    text_is_free = record.kind in FREE_TEXT_KINDS
    hide_text = text_is_free and not reads
    if reads:
        metadata, hidden = dict(record.metadata), []
    else:
        metadata = {
            k: v
            for k, v in record.metadata.items()
            if k in VISIBLE_METADATA and isinstance(v, str | int | float | bool | None)
        }
        hidden = sorted(k for k in record.metadata if k not in metadata)
    return EvaluationOut(
        id=public_id(IdKind.EVALUATION, record.id),
        run_id=public_id(IdKind.RUN, record.run_id),
        evaluator_id=public_id(IdKind.EVALUATOR, record.evaluator_id)
        if record.evaluator_id
        else None,
        evaluator_name=record.evaluator_name,
        evaluator_version=record.evaluator_version,
        kind=record.kind,  # type: ignore[arg-type]
        implementation=record.implementation,
        ground_truth=record.kind != "llm_judge",
        state=record.state,  # type: ignore[arg-type]
        latest=record.latest,
        score=record.score,
        label=record.label,
        explanation=None if hide_text else record.explanation,
        explanation_withheld=hide_text,
        metrics=record.metrics,
        evidence_event_ids=list(record.evidence_event_ids),
        metadata=metadata,
        metadata_withheld=hidden,
        error_code=record.error_code,
        error=record.error if reads else None,
        created_at=record.created_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
    )


class RunEvaluationService:
    def __init__(self, engine: AsyncEngine, *, stale_seconds: float, judge_provider: str) -> None:
        self._engine = engine
        self._stale = stale_seconds
        self._judge_provider = judge_provider

    # ---------------------------------------------------------------- helpers

    async def _run(self, conn: AsyncConnection, principal: Principal, run_id: str) -> RunRecord:
        parsed = parse_public_id(IdKind.RUN, run_id)
        record = (
            await RunQueries(conn, principal.tenant, principal.project_id).get(parsed)
            if parsed
            else None
        )
        if record is None:
            raise run_not_found()
        return record

    async def _evaluator(
        self, conn: AsyncConnection, principal: Principal, evaluator_id: str, run: RunRecord
    ) -> EvaluatorRecord:
        parsed = parse_public_id(IdKind.EVALUATOR, evaluator_id)
        record = (
            await EvaluatorRepository(conn, principal.tenant, principal.project_id).get(parsed)
            if parsed
            else None
        )
        if record is None:
            raise evaluator_not_found()
        if record.archived_at is not None:
            raise _conflict("EVALUATOR_ARCHIVED", "The evaluator is archived.")
        if record.project_id is not None and record.project_id != run.project_id:
            raise _unprocessable(
                "EVALUATOR_PROJECT_MISMATCH", "The evaluator belongs to another project."
            )
        return record

    def _reads(self, conn: AsyncConnection, principal: Principal) -> EvaluationReads:
        return EvaluationReads(conn, principal.tenant, self._stale)

    # ---------------------------------------------------------------- use cases

    async def trigger(
        self, principal: Principal, run_id: str, body: TriggerIn
    ) -> tuple[EvaluationOut, bool]:
        """(the evaluation, created). `created` is False for the existing live row of a repeat."""
        async with self._engine.begin() as conn:
            run = await self._run(conn, principal, run_id)
            evaluator = await self._evaluator(conn, principal, body.evaluator_id, run)
            if evaluator.kind == "human":
                raise _unprocessable(
                    "EVALUATOR_KIND_MISMATCH",
                    "Human evaluators take verdicts, not runs.",
                    expected="deterministic|metric|llm_judge",
                )
            if evaluator.kind == "llm_judge" and self._judge_provider == "none":
                raise judge_not_configured()
            reads = self._reads(conn, principal)
            await reads.lock_run(run.id)
            job_state = (
                await RunQueries(conn, principal.tenant, principal.project_id).job_states(
                    [run.id], EVALUATE_RUN
                )
            )[run.id]
            for row in await reads.open_rows(run.id, evaluator.id):
                live = _derived(row, job_state)
                if live.state != "failed":
                    return evaluation_out(live, principal), False
            if (
                await reads.count_for_evaluator(run.id, evaluator.id)
                >= MAX_EVALUATIONS_PER_EVALUATOR
            ):
                raise _conflict(
                    "LIMIT_REACHED",
                    f"A run holds at most {MAX_EVALUATIONS_PER_EVALUATOR} results per evaluator.",
                    limit="evaluations_per_evaluator",
                )
            evaluation_id = new_uuid(IdKind.EVALUATION)
            await EvaluationRepository(conn, principal.tenant).insert_pending(
                evaluation_id, run_id=run.id, evaluator=evaluator, requested_by=principal.user_id
            )
            await OutboxRepository(conn, principal.tenant).enqueue(
                job_type=EVALUATE_RUN,
                dedupe_key=evaluate_key(principal.workspace_id, run.id),
                payload={"run_id": str(run.id)},
            )
            created = await reads.get_in_run(run.id, evaluation_id)
        assert created is not None
        return evaluation_out(created, principal), True

    async def verdict(self, principal: Principal, run_id: str, body: VerdictIn) -> EvaluationOut:
        async with self._engine.begin() as conn:
            run = await self._run(conn, principal, run_id)
            evaluator = await self._evaluator(conn, principal, body.evaluator_id, run)
            if evaluator.kind != "human":
                raise _unprocessable(
                    "EVALUATOR_KIND_MISMATCH",
                    "Verdicts are recorded against human evaluators.",
                    expected="human",
                )
            allowed = evaluator.config.get("allowed_labels") or list(DEFAULT_LABELS)
            if body.label not in allowed:
                raise _unprocessable(
                    "VERDICT_LABEL_INVALID", "The label is not one of the evaluator's labels."
                )
            reads = self._reads(conn, principal)
            await reads.lock_run(run.id)
            if await reads.count_human(run.id) >= MAX_VERDICTS_PER_RUN:
                raise _conflict(
                    "LIMIT_REACHED",
                    f"A run holds at most {MAX_VERDICTS_PER_RUN} verdicts.",
                    limit="verdicts",
                )
            evaluation_id = new_uuid(IdKind.EVALUATION)
            await reads.insert_verdict(
                evaluation_id,
                run_id=run.id,
                evaluator=evaluator,
                label=body.label,
                score=body.score,
                explanation=body.explanation,
                requested_by=principal.user_id,
            )
            record = await reads.get_in_run(run.id, evaluation_id)
        assert record is not None
        return evaluation_out(record, principal)

    async def list(self, principal: Principal, run_id: str) -> EvaluationPage:
        async with self._engine.connect() as conn:
            run = await self._run(conn, principal, run_id)
            found = await self._reads(conn, principal).list_for_run(run.id, LIST_LIMIT + 1)
            job_state = (
                await RunQueries(conn, principal.tenant, principal.project_id).job_states(
                    [run.id], EVALUATE_RUN
                )
            )[run.id]
        page = found[:LIST_LIMIT]
        return EvaluationPage(
            items=[evaluation_out(_derived(r, job_state), principal) for r in page],
            truncated=len(found) > LIST_LIMIT,
        )

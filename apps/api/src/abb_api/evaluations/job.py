"""The `evaluate_run` job body (ADR-090): run a run's pending evaluations, contain every failure.

What this function never does: take the run row lock, write `runs`, `spans`, `events` or cost
lines, or enqueue a summary. It reads committed events (a capped, canonically ordered load) and
writes only `evaluations` rows, each through `pending -> running -> completed|failed`.

Failure model. An evaluator that raises, returns garbage, exceeds its budget or meets a run that is
too large produces a `failed` *result* with a code and a fixed message; the job still succeeds, so a
bad evaluator can neither retry forever nor dead-letter anything. Only an infrastructure error
(the database) fails the job, and then with a fixed message: the exception text may quote values
(INV-5). Evaluators run in a thread under `wait_for`; Python cannot kill a thread, so the budget is
cooperative and the event cap and glob bounds are the real limits (the residual in ADR-090).
"""

import asyncio
import logging
import time
import uuid

from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.core.config import Settings
from abb_api.evaluations import builtin
from abb_api.evaluations.protocol import (
    Budget,
    BudgetExceeded,
    EvaluatorError,
    InvalidResult,
    RunSnapshot,
    clean_result,
)
from abb_api.evaluations.repository import EvaluationRepository, PendingEvaluation
from abb_api.evaluations.snapshot import build_snapshot
from abb_api.ingestion.store import PgEventStore
from abb_api.runs.queries import RunQueries
from abb_api.tenancy import TenantContext

logger = logging.getLogger(__name__)

PER_JOB = 20  # evaluations one job runs before it hands the rest to a follow-up job
MAX_ERROR = 500


class EvaluationJobError(Exception):
    """The job failed for an infrastructure reason. The message is fixed; raise it `from None`."""

    def __init__(self) -> None:
        super().__init__("evaluate_run failed")


def _failure(exc: BaseException) -> tuple[str, str]:
    """(error_code, stored message). Only a built-in's own `EvaluatorError` text is stored; for
    anything else just the class, because pydantic and `KeyError` messages embed values."""
    if isinstance(exc, BudgetExceeded | TimeoutError):
        return "TIMEOUT", "The evaluator exceeded its time budget."
    if isinstance(exc, EvaluatorError):
        return "EVALUATOR_ERROR", f"EvaluatorError: {exc}"[:MAX_ERROR]
    if isinstance(exc, InvalidResult):
        return "INVALID_RESULT", f"InvalidResult: {exc}"[:MAX_ERROR]
    return "EVALUATOR_ERROR", type(exc).__name__[:64]


async def _record_failure(
    repo: EvaluationRepository, row: PendingEvaluation, code: str, error: str
) -> None:
    await repo.fail(row.id, code=code, error=error)
    logger.info(
        "evaluation failed",
        extra={
            "evaluation_id": str(row.id),
            "evaluator": row.evaluator_name,
            "implementation": row.implementation,
            "state": "failed",
            "error_code": code,
        },
    )


async def _evaluate_one(
    repo: EvaluationRepository,
    row: PendingEvaluation,
    snapshot: RunSnapshot,
    event_ids: frozenset[str],
    settings: Settings,
) -> None:
    if not await repo.start(row.id):
        return  # not pending any more: a terminal row is never touched
    impl = builtin.REGISTRY.get(row.implementation or "")
    if impl is None:
        code = "JUDGE_UNAVAILABLE" if row.kind == "llm_judge" else "EVALUATOR_ERROR"
        return await _record_failure(repo, row, code, "No such evaluator implementation.")
    try:
        config = impl.config_model.model_validate(row.config_snapshot)
        budget = Budget(settings.eval_timeout_seconds)
        raw = await asyncio.wait_for(
            asyncio.to_thread(impl.evaluate, snapshot, config, budget),
            timeout=settings.eval_timeout_seconds,
        )
        result = clean_result(raw, impl.metric_keys, event_ids)
    except Exception as exc:  # contained: an evaluator failure is a result, never a job failure
        code, error = _failure(exc)
        return await _record_failure(repo, row, code, error)
    try:
        async with repo.savepoint():
            stored = await repo.complete(
                row.id,
                score=result.score,
                label=result.label,
                explanation=result.explanation,
                metrics=dict(result.metrics),
                evidence_event_ids=list(result.evidence_event_ids),
                metadata=dict(result.metadata),
            )
    except (DataError, IntegrityError):
        # A value the database refuses (a backstop CHECK): a result problem, not a retry.
        return await _record_failure(repo, row, "INVALID_RESULT", "The result could not be stored.")
    if stored:
        logger.info(
            "evaluation completed",
            extra={
                "evaluation_id": str(row.id),
                "evaluator": row.evaluator_name,
                "implementation": row.implementation,
                "state": "completed",
                "error_code": None,
            },
        )


async def evaluate_run(
    conn: AsyncConnection, tenant: TenantContext, run_id: uuid.UUID, settings: Settings
) -> bool:
    """Run the run's pending evaluations. True when pending rows remain for a follow-up job."""
    try:
        return await _evaluate_run(conn, tenant, run_id, settings)
    except Exception as exc:
        logger.warning("evaluate_run failed", extra={"error_type": type(exc).__name__})
        raise EvaluationJobError from None


async def _evaluate_run(
    conn: AsyncConnection, tenant: TenantContext, run_id: uuid.UUID, settings: Settings
) -> bool:
    started = time.perf_counter()
    repo = EvaluationRepository(conn, tenant)
    pending = await repo.claim_pending(run_id, PER_JOB)
    if not pending:
        return False  # nothing to do: a redelivered or requeued job finds only terminal rows
    record = await RunQueries(conn, tenant).get(run_id)
    if record is None:
        for row in pending:
            await _record_failure(repo, row, "RUN_NOT_FOUND", "The run no longer exists.")
        return False
    limit = settings.eval_max_events
    # One extra row tells whether the run was cut: never evaluate a partial run.
    loaded = await PgEventStore(conn, tenant).load_run(
        run_id, limit=limit + 1, ordering_mode=record.ordering_mode
    )
    if len(loaded) > limit:
        for row in pending:
            await _record_failure(repo, row, "RUN_TOO_LARGE", "The run has too many events.")
        return await _more_pending(repo, run_id)
    snapshot = await asyncio.to_thread(build_snapshot, record, loaded)
    event_ids = frozenset(e.event_id for e in snapshot.events)
    for row in pending:
        await _evaluate_one(repo, row, snapshot, event_ids, settings)
    logger.info(
        "evaluations processed",
        extra={
            "run_id": str(run_id),
            "evaluations": len(pending),
            "event_count": len(loaded),
            "duration_ms": round((time.perf_counter() - started) * 1000),
        },
    )
    return await _more_pending(repo, run_id)


async def _more_pending(repo: EvaluationRepository, run_id: uuid.UUID) -> bool:
    return await repo.count_pending(run_id) > 0

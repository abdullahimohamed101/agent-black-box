"""Compare two visible runs: load capped events, derive the actor's projection, align (ADR-092).

Extraction and alignment run in a worker thread so one comparison never stalls other requests;
the database connection is released before they start.
"""

import asyncio
import uuid
from collections.abc import Mapping
from decimal import Decimal

from abb_event_schema.ids import IdKind
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from abb_api.authz import actions
from abb_api.authz.principal import Principal
from abb_api.authz.service import holds
from abb_api.comparison.compare import Comparison, Row, SideInput, compare_runs
from abb_api.comparison.repository import CostRepository
from abb_api.comparison.schemas import (
    ComparisonOut,
    CostModelOut,
    DivergenceOut,
    EvaluationDiffOut,
    FilesOut,
    MetricOut,
    RowOut,
    ScoreOut,
    SideOut,
    StepOut,
)
from abb_api.comparison.steps import Step
from abb_api.core.config import Settings
from abb_api.evaluations.repository import EvaluationReads, EvaluationRecord
from abb_api.ids import parse_public_id, public_id
from abb_api.ingestion.store import PgEventStore
from abb_api.runs.queries import RunQueries, RunRecord
from abb_api.runs.service import run_not_found


def _step_out(step: Step | None) -> StepOut | None:
    if step is None:
        return None
    return StepOut(
        event_id=step.event_id,
        span_id=step.span_id,
        family=step.family,
        event_type=step.event_type,
        status=step.status,
        duration_ms=step.duration_ms,
        offset_ms=step.offset_ms,
        label=step.label,
        label_withheld=step.label_withheld,
    )


def _row_out(row: Row) -> RowOut:
    return RowOut(
        relation=row.relation,  # type: ignore[arg-type]
        left=_step_out(row.left),
        right=_step_out(row.right),
    )


def _money(value: Decimal | None) -> float:
    return float(round(value, 9)) if value is not None else 0.0


def cost_rows(left: Mapping[str, Decimal], right: Mapping[str, Decimal]) -> list[CostModelOut]:
    out = []
    for model in sorted(set(left) | set(right)):
        a, b = _money(left.get(model)), _money(right.get(model))
        out.append(CostModelOut(model=model, left=a, right=b, delta=round(b - a, 9)))
    return out


def _score(record: EvaluationRecord | None) -> ScoreOut | None:
    return ScoreOut(score=record.score, label=record.label) if record else None


def evaluation_rows(
    left: list[EvaluationRecord], right: list[EvaluationRecord]
) -> list[EvaluationDiffOut]:
    """Latest completed result per evaluator on each side. Explanations are never included."""

    def index(records: list[EvaluationRecord]) -> dict[str, EvaluationRecord]:
        return {
            (str(r.evaluator_id) if r.evaluator_id else f"name:{r.evaluator_name}"): r
            for r in records
        }

    a, b = index(left), index(right)
    out = []
    for key in sorted(set(a) | set(b), key=lambda k: ((a.get(k) or b[k]).evaluator_name, k)):
        one, two = a.get(key), b.get(key)
        first = one or two
        assert first is not None
        delta = (
            round(two.score - one.score, 9)
            if one and two and one.score is not None and two.score is not None
            else None
        )
        out.append(
            EvaluationDiffOut(
                evaluator_id=public_id(IdKind.EVALUATOR, first.evaluator_id)
                if first.evaluator_id
                else None,
                evaluator_name=first.evaluator_name,
                kind=first.kind,
                ground_truth=first.kind != "llm_judge",
                left=_score(one),
                right=_score(two),
                delta=delta,
            )
        )
    return out


def to_out(
    result: Comparison,
    costs: tuple[Mapping[str, Decimal], Mapping[str, Decimal]],
    evaluations: tuple[list[EvaluationRecord], list[EvaluationRecord]],
) -> ComparisonOut:
    files = result.files
    divergence = None
    if result.divergence is not None:
        divergence = DivergenceOut(
            index=result.divergence,
            relation=result.rows[result.divergence].relation,  # type: ignore[arg-type]
        )
    return ComparisonOut(
        left=SideOut(**result.left.__dict__),
        right=SideOut(**result.right.__dict__),
        alignment=result.alignment,  # type: ignore[arg-type]
        alignment_quality=result.quality,  # type: ignore[arg-type]
        summary=[MetricOut(**m.__dict__) for m in result.summary],
        steps=[_row_out(r) for r in result.rows],
        divergence=divergence,
        first_outcome_difference=result.first_outcome_difference,
        cost_by_model=cost_rows(*costs),
        files=FilesOut(
            paths_withheld=files.withheld,
            left_count=files.left_count,
            right_count=files.right_count,
            only_left=list(files.only_left),
            only_right=list(files.only_right),
            both=list(files.both),
            truncated=files.truncated,
        ),
        evaluations=evaluation_rows(*evaluations),
    )


class ComparisonService:
    def __init__(self, engine: AsyncEngine, settings: Settings) -> None:
        self._engine = engine
        self._settings = settings

    @staticmethod
    async def _visible(conn: AsyncConnection, principal: Principal, run_id: str) -> RunRecord:
        parsed = parse_public_id(IdKind.RUN, run_id)
        record = (
            await RunQueries(conn, principal.tenant, principal.project_id).get(parsed)
            if parsed
            else None
        )
        if record is None:
            raise run_not_found()
        return record

    async def compare(self, principal: Principal, run_id: str, with_run_id: str) -> ComparisonOut:
        content = holds(principal, actions.PAYLOAD_READ)
        cap = self._settings.compare_max_events
        async with self._engine.connect() as conn:
            left = await self._visible(conn, principal, run_id)
            right = (
                left if with_run_id == run_id else await self._visible(conn, principal, with_run_id)
            )
            store = PgEventStore(conn, principal.tenant)
            left_events = await store.load_run(left.id, limit=cap, ordering_mode=left.ordering_mode)
            right_events = (
                left_events
                if right.id == left.id
                else await store.load_run(right.id, limit=cap, ordering_mode=right.ordering_mode)
            )
            ids = sorted({left.id, right.id}, key=lambda u: u.bytes)
            costs = await CostRepository(conn, principal.tenant).by_model(ids)
            latest = await EvaluationReads(
                conn, principal.tenant, self._settings.eval_stale_seconds
            ).latest_per_evaluator(ids)
        sides = [
            SideInput(
                run_id=public_id(IdKind.RUN, record.id),
                status=record.status,
                started_at=record.started_at,
                summary=record.summary,
                events=events,
                event_count=int(record.summary.get("event_count") or len(events)),
            )
            for record, events in ((left, left_events), (right, right_events))
        ]
        result = await asyncio.to_thread(
            compare_runs,
            sides[0],
            sides[1],
            content=content,
            max_steps=self._settings.compare_max_steps,
            max_edits=self._settings.compare_max_edits,
        )
        by_run: dict[uuid.UUID, list[EvaluationRecord]] = {i: [] for i in ids}
        for record in latest:
            by_run[record.run_id].append(record)
        return to_out(
            result,
            (costs.get(left.id, {}), costs.get(right.id, {})),
            (by_run[left.id], by_run[right.id]),
        )

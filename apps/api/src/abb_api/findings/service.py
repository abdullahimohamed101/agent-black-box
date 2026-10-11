"""Findings read use case: visibility through the run service, then derived rows and health."""

from decimal import Decimal

from abb_event_schema.enums import RunStatus
from abb_event_schema.ids import IdKind, from_uuid
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.authz.principal import Principal
from abb_api.findings.describe import describe
from abb_api.findings.detectors import DETECTORS_VERSION
from abb_api.findings.health import FindingRef, health
from abb_api.findings.model import RunFacts, Severity
from abb_api.findings.repository import FindingRecord, FindingsRepository, RunFindingsState
from abb_api.findings.schemas import (
    DimensionOut,
    FindingOut,
    FindingsState,
    FindingsStateName,
    HealthOut,
    RunFindingsOut,
)
from abb_api.jobs.outbox import DETECT_FINDINGS
from abb_api.runs.queries import RunQueries, RunRecord
from abb_api.runs.service import RunService


def _int(summary: dict[str, object], name: str) -> int:
    value = summary.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _facts(record: RunRecord, cost: Decimal, retry_cost: Decimal) -> RunFacts:
    """Health reads the run's summary, never the findings (a failed analysis cannot hide a failed
    run)."""
    s = record.summary
    return RunFacts(
        status=RunStatus(record.status),
        terminal_event_id=None,
        started_at=record.started_at,
        event_count=_int(s, "event_count"),
        error_count=_int(s, "error_count"),
        retry_count=_int(s, "retry_count"),
        retries_unattributed=_int(s, "retries_unattributed"),
        tool_spans_finished=_int(s, "tool_spans_finished"),
        tool_spans_ok=_int(s, "tool_spans_ok"),
        ordering_mode=record.ordering_mode,
        agent_slug=record.agent_slug or "",
        cost_usd=cost,
        retry_cost_usd=retry_cost,
    )


def _finding_out(record: FindingRecord) -> FindingOut:
    title, message = describe(record)
    return FindingOut(
        id=from_uuid(IdKind.FINDING, record.id),
        run_id=from_uuid(IdKind.RUN, record.run_id),
        detector=record.detector,
        detector_version=record.detector_version,
        finding_type=record.finding_type,
        kind=record.kind,  # type: ignore[arg-type]
        severity=record.severity,  # type: ignore[arg-type]
        confidence=record.confidence,
        title=title,
        message=message,
        start_event_id=from_uuid(IdKind.EVENT, record.start_event_id)
        if record.start_event_id
        else None,
        end_event_id=from_uuid(IdKind.EVENT, record.end_event_id) if record.end_event_id else None,
        evidence_event_ids=[from_uuid(IdKind.EVENT, i) for i in record.evidence_event_ids],
        fingerprint=record.fingerprint,
        evidence=record.evidence,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _status(stored: RunFindingsState | None, job_state: str) -> FindingsStateName:
    if job_state == "processing":
        return "processing"
    if job_state == "failed":
        return "failed"
    if stored is None:
        return "none"
    return "outdated" if stored.detectors_version < DETECTORS_VERSION else "current"


class FindingsService:
    def __init__(self, engine: AsyncEngine, runs: RunService) -> None:
        self._engine = engine
        self._runs = runs

    async def get(self, principal: Principal, run_id: str) -> RunFindingsOut:
        async with self._engine.connect() as conn:
            record = await self._runs.visible_run(conn, principal, run_id)  # the usual 404s
            queries = RunQueries(conn, principal.tenant, principal.project_id)
            stored, rows = await FindingsRepository(conn, principal.tenant).for_run(record.id)
            job_state = (await queries.job_states([record.id], DETECT_FINDINGS))[record.id]
            inputs = await queries.findings_inputs(record.id)
        status = _status(stored, job_state)
        items = [_finding_out(r) for r in rows]
        report = health(
            _facts(
                record,
                inputs.cost_usd if inputs else Decimal(0),
                inputs.retry_cost_usd if inputs else Decimal(0),
            ),
            [FindingRef(i.id, i.finding_type, Severity(i.severity)) for i in items],
            status,
        )
        return RunFindingsOut(
            run_id=from_uuid(IdKind.RUN, record.id),
            state=FindingsState(
                status=status,
                detectors_version=stored.detectors_version if stored else None,
                summary_version_seen=stored.summary_version_seen if stored else None,
                computed_at=stored.computed_at if stored else None,
                event_count_seen=stored.event_count_seen if stored else None,
                truncated=stored.truncated if stored else False,
            ),
            health=HealthOut(
                overall=report.overall,
                dimensions=[
                    DimensionOut(
                        name=d.name,
                        status=d.status,
                        inputs=dict(d.inputs),
                        rule=d.rule,
                        finding_ids=list(d.finding_ids),
                    )
                    for d in report.dimensions
                ],
            ),
            items=items,
        )

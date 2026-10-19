"""The `detect_findings` job body (ADR-080): derive a run's findings from its events.

Order of reads matters for a single snapshot: the run row's ordering mode first (it decides how the
capped load is ordered), then the events, then the row again for cost and versions. Status,
completion and counters are folded from the events, so facts and events always agree; only the cost
and the baseline come from outside the event set.
"""

import logging
import time
import uuid
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Literal

from abb_event_schema.ordering import sort_events
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.alerts.jobs import enqueue_event
from abb_api.alerts.model import SECURITY_FINDING
from abb_api.core.config import Settings
from abb_api.findings.build import build_context, make_baseline
from abb_api.findings.detectors import DETECTORS_VERSION, run_all
from abb_api.findings.detectors.cost_spike import FINISHED
from abb_api.findings.model import (
    SECURITY_PREFIX,
    DetectorConfig,
    DetectorError,
    Finding,
    finding_uuid,
)
from abb_api.findings.repository import AnalysisState, FindingsRepository
from abb_api.findings.security_inputs import load_security_inputs
from abb_api.ingestion.store import PgEventStore
from abb_api.jobs.outbox import OutboxRepository
from abb_api.runs.queries import RunQueries
from abb_api.tenancy import TenantContext

logger = logging.getLogger(__name__)

MAX_DELAY_SECONDS = 60.0
EVENTS_PER_DELAY_STEP = 10_000
BASELINE_RUNS = 200

Outcome = Literal["analysed", "busy", "missing", "empty"]


def detector_config(settings: Settings) -> DetectorConfig:
    return DetectorConfig(
        loop_min_repetitions=settings.findings_loop_min_repetitions,
        loop_min_repetitions_single=settings.findings_loop_min_repetitions_single,
        identical_min_count=settings.findings_identical_min_count,
        retry_storm_min=settings.findings_retry_storm_min,
        retry_burst_window_seconds=settings.findings_retry_burst_window_seconds,
        context_growth_min_delta_tokens=settings.findings_context_growth_min_delta_tokens,
        context_growth_factor=settings.findings_context_growth_factor,
        context_growth_warning_tokens=settings.findings_context_growth_warning_tokens,
        cascade_min_failures=settings.findings_cascade_min_failures,
        cascade_window_calls=settings.findings_cascade_window_calls,
        cost_spike_min_baseline=settings.findings_cost_spike_min_baseline,
        cost_spike_window_days=settings.findings_cost_spike_window_days,
    )


def detect_delay(base_seconds: float, event_count: int) -> timedelta:
    """How long a run waits before its findings are (re)computed: the base delay, growing with the
    run's size (a 30,000-event run is re-analysed at most every 3 x base), capped at 60 s."""
    grown = base_seconds * max(1.0, event_count / EVENTS_PER_DELAY_STEP)
    return timedelta(seconds=min(grown, max(base_seconds, MAX_DELAY_SECONDS)))


def detect_enqueue_delay(
    *,
    base_seconds: float,
    event_count: int,
    min_interval_seconds: float,
    computed_at: datetime | None,
    db_now: datetime,
    terminal: bool,
) -> timedelta:
    """The delay of a run's next analysis job (KI-097, ADR-174).

    The normal coalescing delay, raised to the time left of the per-run minimum interval since the
    last completed analysis, both read on the database clock. Jobs coalesce per run, so events that
    arrive meanwhile join the one pending job: it is the trailing analysis. A run that has ended is
    never throttled: its final analysis is computed promptly. `0` disables the floor."""
    normal = detect_delay(base_seconds, event_count)
    if terminal or min_interval_seconds <= 0 or computed_at is None:
        return normal
    age = max((db_now - computed_at).total_seconds(), 0.0)
    return max(normal, timedelta(seconds=max(min_interval_seconds - age, 0.0)))


async def try_lock_run(conn: AsyncConnection, tenant: TenantContext, run_id: uuid.UUID) -> bool:
    """One writer of findings per run. Released at the end of the transaction. The advisory
    keyspace is shared with the analytics refresh (`analytics:` prefix); a hash collision would only
    serialise two unrelated jobs."""
    key = f"findings:{tenant.workspace_id}:{run_id}"
    return bool(
        await conn.scalar(select(func.pg_try_advisory_xact_lock(func.hashtextextended(key, 0))))
    )


def _has_security(findings: Sequence[Finding]) -> bool:
    return any(f.detector.startswith(SECURITY_PREFIX) for f in findings)


async def _announce_security(
    conn: AsyncConnection,
    tenant: TenantContext,
    run_id: uuid.UUID,
    findings: Sequence[Finding],
    previous: dict[uuid.UUID, int],
) -> None:
    """One small job per new or raised security finding; no matching happens here."""
    outbox = OutboxRepository(conn, tenant)
    seen: dict[uuid.UUID, Finding] = {}
    for f in findings:
        if f.detector.startswith(SECURITY_PREFIX):
            fid = finding_uuid(tenant.workspace_id, run_id, f.fingerprint)
            kept = seen.get(fid)
            if kept is None or f.severity.rank > kept.severity.rank:
                seen[fid] = f
    for fid in sorted(seen, key=lambda i: i.bytes):
        before = previous.get(fid)
        if before is None or seen[fid].severity.rank > before:
            await enqueue_event(outbox, SECURITY_FINDING, fid)


async def detect_run(
    conn: AsyncConnection, tenant: TenantContext, run_id: uuid.UUID, settings: Settings
) -> Outcome:
    """Every failure inside the job, whichever statement raised it, leaves as a `DetectorError`
    with fixed text and no cause: a stored event that no longer validates, a driver error quoting
    bind parameters or a detector bug could otherwise reach `outbox_jobs.last_error` and the
    dead-letter log with attribute values in them (INV-5; security review P2-1)."""
    try:
        return await _detect_run(conn, tenant, run_id, settings)
    except DetectorError:
        raise
    except Exception as exc:
        # Only the class is logged: the message may quote attribute values.
        logger.warning("findings job failed", extra={"error_type": type(exc).__name__})
        raise DetectorError("job", 0) from None


async def _detect_run(
    conn: AsyncConnection, tenant: TenantContext, run_id: uuid.UUID, settings: Settings
) -> Outcome:
    started = time.perf_counter()
    if not await try_lock_run(conn, tenant, run_id):
        return "busy"
    queries = RunQueries(conn, tenant)
    first = await queries.findings_inputs(run_id)
    if first is None:
        return "missing"  # the run is gone (retention)
    limit = settings.findings_max_events
    # One extra row tells us whether the run was cut.
    loaded = await PgEventStore(conn, tenant).load_run(
        run_id, limit=limit + 1, ordering_mode=first.ordering_mode
    )
    row = await queries.findings_inputs(run_id)
    if row is None or not loaded:
        return "empty" if row is not None else "missing"
    truncated = len(loaded) > limit
    events = sort_events(loaded)[:limit]

    config = detector_config(settings)
    security = await load_security_inputs(conn, tenant, row.project_id, run_id)
    try:
        ctx = build_context(
            events, cost_usd=row.cost_usd, retry_cost_usd=row.retry_cost_usd, config=config
        )
        ctx = replace(ctx, security=security)
        if ctx.facts.status in FINISHED and row.agent_slug is not None:
            rows = await queries.cost_baseline(
                row.project_id,
                row.agent_slug,
                row.started_at,
                window_days=config.cost_spike_window_days,
                limit=BASELINE_RUNS,
            )
            baseline = make_baseline(
                rows,
                agent_slug=row.agent_slug,
                window_days=config.cost_spike_window_days,
                min_runs=config.cost_spike_min_baseline,
            )
            ctx = replace(ctx, baseline=baseline)
        findings = run_all(ctx)
    except DetectorError:
        raise
    except Exception:
        # Whatever it was, its message may quote attribute values: the job fails with fixed text.
        raise DetectorError("analysis", len(events)) from None

    repository = FindingsRepository(conn, tenant)
    # Security findings that are new or got worse are announced to the alert rules (ADR-150). The
    # comparison is with what was stored before this pass, so an unchanged finding that a rebuild
    # rewrites is not announced again; the alert's own dedupe key backs that up.
    previous = await repository.security_severities(run_id) if _has_security(findings) else {}
    await repository.replace_run(
        run_id,
        project_id=row.project_id,
        agent_slug=row.agent_slug,
        run_started_at=row.started_at,
        findings=findings,
        state=AnalysisState(
            detectors_version=DETECTORS_VERSION,
            summary_version_seen=row.summary_version,
            ordering_mode_seen=first.ordering_mode,
            event_count_seen=len(events),
            truncated=truncated,
        ),
    )
    await _announce_security(conn, tenant, run_id, findings, previous)
    logger.info(
        "findings computed",
        extra={
            "run_id": str(run_id),
            "event_count": len(events),
            "findings": len(findings),
            "truncated": truncated,
            "duration_ms": round((time.perf_counter() - started) * 1000),
        },
    )
    return "analysed"

"""Persistence of findings: every method is bound to one tenant (INV-3).

Findings are derived (INV-2): `replace_run` makes a run's rows equal to a detector pass, upserting
by fingerprint (so ids and `created_at` survive rewrites) and deleting the rest. It writes only
`findings` and `run_findings`; the run row is not touched.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from abb_event_schema.ids import to_uuid
from sqlalchemy import case, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.db import tables as t
from abb_api.findings.model import SECURITY_PREFIX, Finding, Severity, finding_uuid
from abb_api.tenancy import TenantContext

ROW_CHUNK = 100  # 19 columns per finding; far below the bind-parameter limit


@dataclass(frozen=True)
class AnalysisState:
    """What one detector pass looked at (becomes the run's `run_findings` row)."""

    detectors_version: int
    summary_version_seen: int
    ordering_mode_seen: str
    event_count_seen: int
    truncated: bool
    computed_at: datetime | None = None  # the database clock when omitted


@dataclass(frozen=True)
class RunFindingsState:
    run_id: uuid.UUID
    project_id: uuid.UUID
    detectors_version: int
    summary_version_seen: int
    ordering_mode_seen: str
    computed_at: datetime
    event_count_seen: int
    truncated: bool
    finding_count: int
    max_severity: str | None


@dataclass(frozen=True)
class FindingRecord:
    id: uuid.UUID
    run_id: uuid.UUID
    project_id: uuid.UUID
    agent_slug: str | None
    detector: str
    detector_version: str
    finding_type: str
    kind: str
    severity: str
    confidence: float
    start_event_id: uuid.UUID | None
    end_event_id: uuid.UUID | None
    evidence_event_ids: tuple[uuid.UUID, ...]
    fingerprint: str
    evidence: dict[str, Any]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Badge:
    """`count` and `max_severity` are the reliability findings (the run list's badge predates the
    security category); `security_*` are the security findings, counted apart (ADR-131)."""

    count: int
    max_severity: str | None
    detectors_version: int
    security_count: int = 0
    security_max_severity: str | None = None


_RANK = case(
    {"critical": 4, "high": 3, "medium": 2, "warning": 1},
    value=t.findings.c.severity,
    else_=0,
)


class FindingsRepository:
    def __init__(self, conn: AsyncConnection, tenant: TenantContext) -> None:
        self._conn = conn
        self._tenant = tenant

    async def replace_run(
        self,
        run_id: uuid.UUID,
        *,
        project_id: uuid.UUID,
        agent_slug: str | None,
        run_started_at: datetime,
        findings: Sequence[Finding],
        state: AnalysisState,
    ) -> None:
        """Make the run's stored findings exactly `findings` (idempotent)."""
        ws = self._tenant.workspace_id
        unique: dict[str, Finding] = {}
        for f in findings:  # a duplicate fingerprint must not trip the unique index
            kept = unique.get(f.fingerprint)
            if kept is None or f.severity.rank > kept.severity.rank:
                unique[f.fingerprint] = f
        findings = list(unique.values())
        rows = [
            {
                "workspace_id": ws,
                "id": finding_uuid(ws, run_id, f.fingerprint),
                "run_id": run_id,
                "project_id": project_id,
                "agent_slug": agent_slug,
                "run_started_at": run_started_at,
                "detector": f.detector,
                "detector_version": f.detector_version,
                "finding_type": f.finding_type,
                "kind": f.kind.value,
                "severity": f.severity.value,
                "confidence": f.confidence,
                "start_event_id": to_uuid(f.start_event_id) if f.start_event_id else None,
                "end_event_id": to_uuid(f.end_event_id) if f.end_event_id else None,
                "evidence_event_ids": [to_uuid(i) for i in f.evidence_event_ids],
                "fingerprint": f.fingerprint,
                "evidence": f.evidence,
            }
            for f in findings
        ]
        for start in range(0, len(rows), ROW_CHUNK):
            statement = insert(t.findings).values(rows[start : start + ROW_CHUNK])
            await self._conn.execute(
                statement.on_conflict_do_update(
                    constraint="uq_findings_run_fingerprint",
                    # id and created_at are first-seen and never move; everything else follows.
                    set_={
                        **{
                            name: getattr(statement.excluded, name)
                            for name in (
                                "project_id",
                                "agent_slug",
                                "run_started_at",
                                "detector",
                                "detector_version",
                                "finding_type",
                                "kind",
                                "severity",
                                "confidence",
                                "start_event_id",
                                "end_event_id",
                                "evidence_event_ids",
                                "evidence",
                            )
                        },
                        "updated_at": func.now(),
                    },
                )
            )
        keep = [r["fingerprint"] for r in rows]
        stale = delete(t.findings).where(
            t.findings.c.workspace_id == ws, t.findings.c.run_id == run_id
        )
        if keep:
            stale = stale.where(t.findings.c.fingerprint.notin_(keep))
        await self._conn.execute(stale)

        reliability = [f for f in findings if not f.detector.startswith(SECURITY_PREFIX)]
        security = [f for f in findings if f.detector.startswith(SECURITY_PREFIX)]
        top = max((f.severity for f in reliability), key=lambda s: s.rank, default=None)
        security_top = max((f.severity for f in security), key=lambda s: s.rank, default=None)
        values = {
            "workspace_id": ws,
            "run_id": run_id,
            "project_id": project_id,
            "detectors_version": state.detectors_version,
            "summary_version_seen": state.summary_version_seen,
            "ordering_mode_seen": state.ordering_mode_seen,
            "computed_at": state.computed_at if state.computed_at is not None else func.now(),
            "event_count_seen": state.event_count_seen,
            "truncated": state.truncated,
            "finding_count": len(reliability),
            "max_severity": top.value if isinstance(top, Severity) else None,
            "security_count": len(security),
            "security_max_severity": (
                security_top.value if isinstance(security_top, Severity) else None
            ),
        }
        upsert = insert(t.run_findings).values(**values)
        await self._conn.execute(
            upsert.on_conflict_do_update(
                index_elements=["workspace_id", "run_id"],
                set_={
                    k: getattr(upsert.excluded, k)
                    for k in values
                    if k not in ("workspace_id", "run_id")
                },
            )
        )

    async def security_severities(self, run_id: uuid.UUID) -> dict[uuid.UUID, int]:
        """The stored security findings of a run as `{id: severity rank}` (before a rewrite)."""
        rows = await self._conn.execute(
            select(t.findings.c.id, t.findings.c.severity).where(
                t.findings.c.workspace_id == self._tenant.workspace_id,
                t.findings.c.run_id == run_id,
                t.findings.c.detector.like(f"{SECURITY_PREFIX}%"),
            )
        )
        return {r.id: Severity(r.severity).rank for r in rows}

    async def for_run(
        self, run_id: uuid.UUID
    ) -> tuple[RunFindingsState | None, list[FindingRecord]]:
        ws = self._tenant.workspace_id
        state_row = (
            await self._conn.execute(
                select(t.run_findings).where(
                    t.run_findings.c.workspace_id == ws, t.run_findings.c.run_id == run_id
                )
            )
        ).first()
        rows = await self._conn.execute(
            select(t.findings)
            .where(t.findings.c.workspace_id == ws, t.findings.c.run_id == run_id)
            .order_by(
                _RANK.desc(),
                t.findings.c.detector,
                t.findings.c.finding_type,
                t.findings.c.fingerprint,
            )
        )
        state = (
            RunFindingsState(
                **{name: getattr(state_row, name) for name in RunFindingsState.__dataclass_fields__}
            )
            if state_row
            else None
        )
        records = [
            FindingRecord(
                **{
                    name: getattr(row, name)
                    for name in FindingRecord.__dataclass_fields__
                    if name != "evidence_event_ids"
                },
                evidence_event_ids=tuple(row.evidence_event_ids),
            )
            for row in rows
        ]
        return state, records

    async def badges(self, run_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Badge]:
        """Count, worst severity and rule version per run: one query on `run_findings` only."""
        if not run_ids:
            return {}
        rows = await self._conn.execute(
            select(
                t.run_findings.c.run_id,
                t.run_findings.c.finding_count,
                t.run_findings.c.max_severity,
                t.run_findings.c.detectors_version,
                t.run_findings.c.security_count,
                t.run_findings.c.security_max_severity,
            ).where(
                t.run_findings.c.workspace_id == self._tenant.workspace_id,
                t.run_findings.c.run_id.in_(list(run_ids)),
            )
        )
        return {
            r.run_id: Badge(
                r.finding_count,
                r.max_severity,
                r.detectors_version,
                r.security_count,
                r.security_max_severity,
            )
            for r in rows
        }

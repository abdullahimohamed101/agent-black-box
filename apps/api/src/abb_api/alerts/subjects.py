"""Loading the facts an alert points at (ADR-150). Reads stored metadata only: ids, names,
statuses, severities, times. No query here touches `events`, `attributes`, evidence, error text or
a run's name."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.alerts import model as m
from abb_api.db import tables as t
from abb_api.tenancy import TenantContext

_R = t.runs.c
_P = t.projects.c
_A = t.agents.c
_F = t.findings.c
_AP = t.approvals.c


class SubjectLoader:
    def __init__(self, conn: AsyncConnection, tenant: TenantContext) -> None:
        self._conn = conn
        self._ws = tenant.workspace_id

    async def workspace_slug(self) -> str:
        slug = await self._conn.scalar(
            select(t.workspaces.c.slug).where(t.workspaces.c.id == self._ws)
        )
        return str(slug) if slug is not None else ""

    async def run_failed(
        self, run_id: uuid.UUID, workspace_slug: str, *, not_before: datetime | None = None
    ) -> m.Subject | None:
        """A failed run. A run that ended before `not_before` is history, not news: the dedupe row
        of such a run may have been pruned, and a rebuild must not announce it again."""
        row = (
            await self._conn.execute(
                select(
                    _R.id,
                    _R.status,
                    _R.project_id,
                    _R.agent_slug,
                    func.coalesce(_R.completed_at, _R.started_at).label("ended_at"),
                    _P.slug.label("project_slug"),
                    _P.name.label("project_name"),
                    _A.name.label("agent_name"),
                )
                .select_from(
                    t.runs.join(
                        t.projects, and_(_P.workspace_id == _R.workspace_id, _P.id == _R.project_id)
                    ).outerjoin(
                        t.agents,
                        and_(
                            _A.workspace_id == _R.workspace_id,
                            _A.project_id == _R.project_id,
                            _A.slug == _R.agent_slug,
                        ),
                    )
                )
                .where(_R.workspace_id == self._ws, _R.id == run_id)
            )
        ).first()
        if row is None or row.status not in m.RUN_FAIL_STATUSES:
            return None
        if not_before is not None and row.ended_at is not None and row.ended_at < not_before:
            return None
        return m.Subject(
            trigger=m.RUN_FAILED,
            dedupe_key=f"{row.id}:{row.status}",
            severity="warning" if row.status == "BLOCKED" else "high",
            workspace_id=self._ws,
            workspace_slug=workspace_slug,
            project_id=row.project_id,
            project_slug=row.project_slug,
            project_name=row.project_name,
            agent_slug=row.agent_slug,
            agent_name=row.agent_name,
            run_id=row.id,
            run_status=row.status,
            details={"run_status": row.status},
        )

    async def security_finding(
        self, finding_id: uuid.UUID, workspace_slug: str
    ) -> m.Subject | None:
        row = (
            await self._conn.execute(
                select(
                    _F.id,
                    _F.run_id,
                    _F.project_id,
                    _F.agent_slug,
                    _F.detector,
                    _F.detector_version,
                    _F.severity,
                    _P.slug.label("project_slug"),
                    _P.name.label("project_name"),
                    _A.name.label("agent_name"),
                )
                .select_from(
                    t.findings.join(
                        t.projects, and_(_P.workspace_id == _F.workspace_id, _P.id == _F.project_id)
                    ).outerjoin(
                        t.agents,
                        and_(
                            _A.workspace_id == _F.workspace_id,
                            _A.project_id == _F.project_id,
                            _A.slug == _F.agent_slug,
                        ),
                    )
                )
                .where(_F.workspace_id == self._ws, _F.id == finding_id, _F.detector.like("sec.%"))
            )
        ).first()
        if row is None:
            return None
        return m.Subject(
            trigger=m.SECURITY_FINDING,
            # A finding fires once per (id, rule version, severity): a rebuild that rewrites the
            # same finding changes none of the three, and a raised severity is a new alert.
            dedupe_key=f"{row.id}:{row.detector_version}:{row.severity}",
            severity=row.severity,
            workspace_id=self._ws,
            workspace_slug=workspace_slug,
            project_id=row.project_id,
            project_slug=row.project_slug,
            project_name=row.project_name,
            agent_slug=row.agent_slug,
            agent_name=row.agent_name,
            run_id=row.run_id,
            finding_id=row.id,
            finding_severity=row.severity,
            details={"detector": row.detector, "finding_severity": row.severity},
        )

    async def approval(
        self, approval_id: uuid.UUID, workspace_slug: str, *, expiring_now: datetime | None = None
    ) -> m.Subject | None:
        """A waiting approval. With `expiring_now`, the subject of the "about to expire" alert."""
        row = (
            await self._conn.execute(self._approvals_select().where(_AP.id == approval_id))
        ).first()
        if row is None or row.state != "REQUESTED":
            return None
        return self._approval_subject(row, workspace_slug, expiring_now)

    async def expiring_approvals(
        self, workspace_slug: str, *, now: datetime, until: datetime, limit: int
    ) -> list[m.Subject]:
        rows = await self._conn.execute(
            self._approvals_select()
            .where(_AP.state == "REQUESTED", _AP.expires_at > now, _AP.expires_at <= until)
            .order_by(_AP.expires_at, _AP.id)
            .limit(limit)
        )
        return [self._approval_subject(r, workspace_slug, now) for r in rows]

    def _approvals_select(self) -> Any:
        return (
            select(
                _AP.id,
                _AP.run_id,
                _AP.project_id,
                _AP.agent_id,
                _AP.state,
                _AP.expires_at,
                _P.slug.label("project_slug"),
                _P.name.label("project_name"),
                _A.name.label("agent_name"),
            )
            .select_from(
                t.approvals.join(
                    t.projects, and_(_P.workspace_id == _AP.workspace_id, _P.id == _AP.project_id)
                ).outerjoin(
                    t.agents,
                    and_(
                        _A.workspace_id == _AP.workspace_id,
                        _A.project_id == _AP.project_id,
                        _A.slug == _AP.agent_id,
                    ),
                )
            )
            .where(_AP.workspace_id == self._ws)
        )

    def _approval_subject(
        self, row: Any, workspace_slug: str, expiring_now: datetime | None
    ) -> m.Subject:
        expiring = expiring_now is not None
        details: dict[str, Any] = {"expires_at": row.expires_at.isoformat()}
        seconds: float | None = None
        if expiring_now is not None:
            seconds = max((row.expires_at - expiring_now).total_seconds(), 0.0)
            details["seconds_to_expiry"] = int(seconds)
        return m.Subject(
            trigger=m.APPROVAL_EXPIRING if expiring else m.APPROVAL_REQUESTED,
            dedupe_key=f"{row.id}:{'expiring' if expiring else 'requested'}",
            severity="warning" if expiring else "info",
            workspace_id=self._ws,
            workspace_slug=workspace_slug,
            project_id=row.project_id,
            project_slug=row.project_slug,
            project_name=row.project_name,
            agent_slug=row.agent_id,
            agent_name=row.agent_name,
            run_id=row.run_id,
            approval_id=row.id,
            seconds_to_expiry=seconds,
            details=details,
        )

    async def project(self, project_id: uuid.UUID) -> tuple[str, str] | None:
        row = (
            await self._conn.execute(
                select(_P.slug, _P.name).where(_P.workspace_id == self._ws, _P.id == project_id)
            )
        ).first()
        return (row.slug, row.name) if row else None

    async def agent_name(self, project_id: uuid.UUID | None, slug: str) -> str | None:
        statement = select(_A.name).where(_A.workspace_id == self._ws, _A.slug == slug)
        if project_id is not None:
            statement = statement.where(_A.project_id == project_id)
        return await self._conn.scalar(statement.limit(1))

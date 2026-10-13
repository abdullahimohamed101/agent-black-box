"""Workspace-wide listing of security findings (ADR-131): every statement carries the workspace
(INV-3) and, for a project-bound key, its project. The rows hold no captured content: the
evidence column is read only for its fixed `rule` and `count`."""

import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.db import tables as t
from abb_api.findings.model import SECURITY_PREFIX
from abb_api.tenancy import TenantContext

_F = t.findings.c
_R = t.runs.c
_P = t.projects.c
MAX_FACET_PROJECTS = 200


@dataclass(frozen=True)
class SecurityFilter:
    project_ids: tuple[uuid.UUID, ...] = ()  # a project-bound key's project, or the one asked for
    severities: tuple[str, ...] = ()
    detectors: tuple[str, ...] = ()
    run_statuses: tuple[str, ...] = ()
    since: datetime | None = None  # on the run's start time
    until: datetime | None = None

    def without(self, dimension: str) -> "SecurityFilter":
        """The filter minus one dimension, for that dimension's facet counts."""
        empty: dict[str, Any] = {
            "severity": {"severities": ()},
            "detector": {"detectors": ()},
            "project": {"project_ids": ()},
        }
        return replace(self, **empty.get(dimension, {}))


@dataclass(frozen=True)
class SecurityRow:
    id: uuid.UUID
    run_id: uuid.UUID
    run_name: str | None
    run_status: str
    run_started_at: datetime
    project_id: uuid.UUID
    project_slug: str
    project_name: str
    detector: str
    detector_version: str
    finding_type: str
    kind: str
    severity: str
    start_event_id: uuid.UUID | None
    evidence_event_ids: tuple[uuid.UUID, ...]
    rule: str | None
    count: int | None
    kinds: tuple[str, ...]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class FacetCount:
    value: str
    label: str | None
    count: int


@dataclass(frozen=True)
class SecurityFacets:
    severity: list[FacetCount]
    detector: list[FacetCount]
    project: list[FacetCount]


class SecurityFindingsRepository:
    def __init__(self, conn: AsyncConnection, tenant: TenantContext) -> None:
        self._conn = conn
        self._tenant = tenant

    def _conditions(self, flt: SecurityFilter) -> list[ColumnElement[bool]]:
        conditions: list[ColumnElement[bool]] = [
            _F.workspace_id == self._tenant.workspace_id,
            _F.detector.like(f"{SECURITY_PREFIX}%"),
        ]
        if flt.project_ids:
            conditions.append(_F.project_id.in_(flt.project_ids))
        if flt.severities:
            conditions.append(_F.severity.in_(flt.severities))
        if flt.detectors:
            conditions.append(_F.detector.in_(flt.detectors))
        if flt.run_statuses:
            conditions.append(_R.status.in_(flt.run_statuses))
        if flt.since is not None:
            conditions.append(_F.run_started_at >= flt.since)
        if flt.until is not None:
            conditions.append(_F.run_started_at <= flt.until)
        return conditions

    def _joined(self, statement: Select[Any]) -> Select[Any]:
        return statement.select_from(
            t.findings.join(
                t.runs,
                (_R.workspace_id == _F.workspace_id) & (_R.id == _F.run_id),
            ).join(
                t.projects,
                (_P.workspace_id == _F.workspace_id) & (_P.id == _F.project_id),
            )
        )

    async def page(
        self, flt: SecurityFilter, *, limit: int, after: tuple[datetime, uuid.UUID] | None
    ) -> list[SecurityRow]:
        statement = self._joined(
            select(
                _F.id,
                _F.run_id,
                _R.name.label("run_name"),
                _R.status.label("run_status"),
                _F.run_started_at,
                _F.project_id,
                _P.slug.label("project_slug"),
                _P.name.label("project_name"),
                _F.detector,
                _F.detector_version,
                _F.finding_type,
                _F.kind,
                _F.severity,
                _F.start_event_id,
                _F.evidence_event_ids,
                _F.evidence["rule"].astext.label("rule"),
                _F.evidence["count"].astext.label("count"),
                _F.evidence["kinds"].label("kinds"),
                _F.created_at,
                _F.updated_at,
            )
        ).where(*self._conditions(flt))
        if after is not None:
            statement = statement.where(tuple_(_F.run_started_at, _F.id) < tuple_(*after))
        statement = statement.order_by(_F.run_started_at.desc(), _F.id.desc()).limit(limit)
        rows = await self._conn.execute(statement)
        out: list[SecurityRow] = []
        for r in rows:
            count = int(r.count) if r.count is not None and r.count.isdigit() else None
            out.append(
                SecurityRow(
                    id=r.id,
                    run_id=r.run_id,
                    run_name=r.run_name,
                    run_status=r.run_status,
                    run_started_at=r.run_started_at,
                    project_id=r.project_id,
                    project_slug=r.project_slug,
                    project_name=r.project_name,
                    detector=r.detector,
                    detector_version=r.detector_version,
                    finding_type=r.finding_type,
                    kind=r.kind,
                    severity=r.severity,
                    start_event_id=r.start_event_id,
                    evidence_event_ids=tuple(r.evidence_event_ids),
                    rule=r.rule,
                    count=count,
                    kinds=tuple(k for k in (r.kinds or []) if isinstance(k, str)),
                    created_at=r.created_at,
                    updated_at=r.updated_at,
                )
            )
        return out

    async def _facet(
        self, flt: SecurityFilter, dimension: str, column: Any, label: Any = None
    ) -> list[FacetCount]:
        columns = [column.label("value"), func.count().label("n")]
        if label is not None:
            columns.insert(1, label.label("label"))
        statement = self._joined(select(*columns)).where(*self._conditions(flt.without(dimension)))
        statement = statement.group_by(column, *([label] if label is not None else []))
        statement = statement.order_by(func.count().desc(), column).limit(MAX_FACET_PROJECTS)
        rows = await self._conn.execute(statement)
        return [FacetCount(str(r.value), getattr(r, "label", None), r.n) for r in rows]

    async def facets(self, flt: SecurityFilter) -> SecurityFacets:
        """Counts per severity, detector and project. Each dimension is counted under the other
        filters but not its own, so choosing a value never makes its siblings disappear."""
        return SecurityFacets(
            severity=await self._facet(flt, "severity", _F.severity),
            detector=await self._facet(flt, "detector", _F.detector),
            project=await self._facet(flt, "project", _F.project_id, _P.name),
        )

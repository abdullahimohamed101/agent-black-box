"""Use cases behind the security routes: project resolution, detector catalogue, workspace list,
settings. Content never enters: the list reads fixed fields of findings, settings are configuration.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from abb_event_schema.ids import IdKind, from_uuid
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.authz.principal import Principal
from abb_api.core.domain import NotFoundError
from abb_api.core.errors import AppError, ErrorCategory
from abb_api.findings.describe import describe
from abb_api.findings.detectors import DETECTORS
from abb_api.findings.security_repository import (
    FacetCount,
    SecurityFilter,
    SecurityFindingsRepository,
    SecurityRow,
)
from abb_api.findings.security_words import SECURITY_CATALOG, SECURITY_DETECTOR_IDS
from abb_api.ids import parse_public_id
from abb_api.projects.access import authorise_project, project_not_found
from abb_api.projects.repository import ProjectRepository
from abb_api.runs import cursors
from abb_api.security import schemas as s
from abb_api.security.settings import (
    Actor,
    CaptureMode,
    InvalidSettingsError,
    PgSecuritySettingsStore,
    SecuritySettings,
    update_settings,
)

UNKNOWN_DETECTOR = "unknown detector id"
VERSIONS = {d.name: d.version for d in DETECTORS}


def settings_invalid(message: str) -> AppError:
    """422 with the domain message (written never to echo a value) and a stable code."""
    return AppError(
        "SETTINGS_INVALID",
        message,
        category=ErrorCategory.VALIDATION,
        status_code=422,
    )


def detector_list(
    muted: frozenset[str], *, project_known: bool, allowlist_configured: bool
) -> list[s.DetectorOut]:
    items: list[s.DetectorOut] = []
    for info in SECURITY_CATALOG:
        is_muted = info.id in muted
        needs_allowlist = info.finding_type == "unexpected_destination"
        if is_muted:
            active: bool | None = False
        elif needs_allowlist:
            active = allowlist_configured if project_known else None
        else:
            active = True
        items.append(
            s.DetectorOut(
                id=info.id,
                title=info.title,
                description=info.description,
                severities=list(info.severities),  # type: ignore[arg-type]
                version=VERSIONS[info.id],
                muted=is_muted,
                active=active,
            )
        )
    return items


@dataclass(frozen=True)
class _Words:
    """What `describe` reads of a finding; the fixed sentence needs no more than the type, the
    kind and (for credentials) the closed-vocabulary kind names."""

    finding_type: str
    kind: str
    evidence: dict[str, Any]


def _finding_out(row: SecurityRow) -> s.SecurityFindingOut:
    title, message = describe(_Words(row.finding_type, row.kind, {"kinds": list(row.kinds)}))
    return s.SecurityFindingOut(
        id=from_uuid(IdKind.FINDING, row.id),
        run_id=from_uuid(IdKind.RUN, row.run_id),
        run_name=row.run_name,
        run_status=row.run_status,
        project_id=from_uuid(IdKind.PROJECT, row.project_id),
        project_slug=row.project_slug,
        project_name=row.project_name,
        detector=row.detector,
        detector_version=row.detector_version,
        finding_type=row.finding_type,
        kind="probable",
        severity=row.severity,  # type: ignore[arg-type]
        title=title,
        message=message,
        rule=row.rule,
        count=row.count,
        start_event_id=from_uuid(IdKind.EVENT, row.start_event_id) if row.start_event_id else None,
        evidence_event_ids=[from_uuid(IdKind.EVENT, i) for i in row.evidence_event_ids],
        run_started_at=row.run_started_at,
        detected_at=row.created_at,
        updated_at=row.updated_at,
    )


def _facet(values: list[FacetCount], *, project: bool = False) -> list[s.FacetValue]:
    return [
        s.FacetValue(
            value=from_uuid(IdKind.PROJECT, uuid.UUID(v.value)) if project else v.value,
            label=v.label,
            count=v.count,
        )
        for v in values
    ]


class SecurityApiService:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def detectors(self, principal: Principal, project_id: str | None) -> s.DetectorList:
        async with self._engine.connect() as conn:
            scope = await authorise_project(conn, principal, project_id)
            if scope is None:
                return s.DetectorList(
                    items=detector_list(
                        frozenset(), project_known=False, allowlist_configured=False
                    )
                )
            current = await PgSecuritySettingsStore(conn, principal.tenant).get(scope)
        return s.DetectorList(
            items=detector_list(
                frozenset(current.muted_detectors),
                project_known=True,
                allowlist_configured=bool(current.destination_allowlist),
            )
        )

    async def list_findings(
        self,
        principal: Principal,
        *,
        project_id: str | None,
        severities: list[str],
        detectors: list[str],
        run_statuses: list[str],
        since: datetime | None,
        until: datetime | None,
        limit: int,
        cursor: str | None,
    ) -> s.SecurityFindingPage:
        after: tuple[datetime, uuid.UUID] | None = None
        if cursor is not None:
            key = cursors.decode(cursor, "security", width=2).key
            after = (cursors.as_datetime(key[0]), cursors.as_uuid(key[1]))
        async with self._engine.connect() as conn:
            scope = await authorise_project(conn, principal, project_id)
            flt = SecurityFilter(
                project_ids=(scope,) if scope is not None else (),
                severities=tuple(severities),
                detectors=tuple(detectors),
                run_statuses=tuple(run_statuses),
                since=since,
                until=until,
            )
            repository = SecurityFindingsRepository(conn, principal.tenant)
            rows = await repository.page(flt, limit=limit + 1, after=after)
            facets = await repository.facets(flt)
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit:
            last = page[-1]
            next_cursor = cursors.encode(
                cursors.Cursor("security", [last.run_started_at.isoformat(), str(last.id)])
            )
        return s.SecurityFindingPage(
            items=[_finding_out(r) for r in page],
            next_cursor=next_cursor,
            facets=s.SecurityFacets(
                severity=_facet(facets.severity),
                detector=_facet(facets.detector),
                project=_facet(facets.project, project=True),
            ),
        )

    async def get_settings(self, principal: Principal, project_id: str) -> s.SecuritySettingsOut:
        parsed = parse_public_id(IdKind.PROJECT, project_id)
        if parsed is None:
            raise project_not_found()
        async with self._engine.connect() as conn:
            if await ProjectRepository(conn, principal.tenant).get(parsed) is None:
                raise project_not_found()
            current = await PgSecuritySettingsStore(conn, principal.tenant).get(parsed)
        return _settings_out(current)

    async def put_settings(
        self, principal: Principal, project_id: str, body: s.SecuritySettingsIn
    ) -> s.SecuritySettingsOut:
        parsed = parse_public_id(IdKind.PROJECT, project_id)
        if parsed is None:
            raise project_not_found()
        if (
            body.muted_detectors is not None
            and not set(body.muted_detectors) <= SECURITY_DETECTOR_IDS
        ):
            raise settings_invalid(UNKNOWN_DETECTOR)
        try:
            async with self._engine.begin() as conn:
                if await ProjectRepository(conn, principal.tenant).get(parsed) is None:
                    raise project_not_found()
                updated = await update_settings(
                    conn,
                    principal.tenant,
                    parsed,
                    Actor.of(principal),
                    capture_mode=CaptureMode(body.capture_mode) if body.capture_mode else None,
                    muted_detectors=body.muted_detectors,
                    destination_allowlist=body.destination_allowlist,
                )
        except InvalidSettingsError as error:
            raise settings_invalid(str(error)) from None
        except NotFoundError:
            raise project_not_found() from None
        return _settings_out(updated)


def _settings_out(current: SecuritySettings) -> s.SecuritySettingsOut:
    return s.SecuritySettingsOut(
        project_id=from_uuid(IdKind.PROJECT, current.project_id),
        capture_mode=current.capture_mode.value,
        muted_detectors=list(current.muted_detectors),
        destination_allowlist=list(current.destination_allowlist),
        updated_at=current.updated_at,
        updated_by=current.updated_by,
        detectors=detector_list(
            frozenset(current.muted_detectors),
            project_known=True,
            allowlist_configured=bool(current.destination_allowlist),
        ),
    )

"""Security routes (Phase 13, ADR-131): the workspace findings list, the detector catalogue and
per-project settings. Metadata only: no route accepts or returns captured content."""

from datetime import datetime
from typing import Annotated, Any

from abb_event_schema.ids import IdKind, id_pattern
from fastapi import APIRouter, Depends, Query, Request

from abb_api.authz import actions
from abb_api.authz.dependencies import require
from abb_api.authz.principal import Principal
from abb_api.core.errors import AppError, ErrorCategory, ErrorEnvelope
from abb_api.runs.schemas import RunStatusName
from abb_api.security import schemas as s
from abb_api.security.api_service import SecurityApiService

router = APIRouter(tags=["security"])

Reader = Annotated[Principal, Depends(require(actions.RUN_READ))]
Admin = Annotated[Principal, Depends(require(actions.PROJECT_WRITE))]

_ERRORS: dict[int | str, dict[str, Any]] = {
    code: {"description": text, "model": ErrorEnvelope}
    for code, text in {
        400: "The cursor is not valid.",
        401: "Missing or invalid credential.",
        403: "The caller's role or key may not use this route.",
        404: "Not found, or not visible to this caller (never reveals other tenants).",
        422: "A parameter or the body is invalid; the message never repeats a value.",
        503: "A dependency is unavailable; retry with backoff.",
    }.items()
}
DetectorId = Annotated[str, Query(pattern=r"^sec\.[a-z][a-z0-9_]{0,59}$")]
SeverityParam = Annotated[str, Query(pattern=r"^(info|warning|medium|high|critical)$")]


def _service(request: Request) -> SecurityApiService:
    service: SecurityApiService = request.app.state.security
    return service


@router.get(
    "/v1/security/findings",
    response_model=s.SecurityFindingPage,
    responses=_ERRORS,
    summary="List security findings across the workspace",
    description=(
        "Newest run first (`run_started_at`), keyset-paged. Every role that may read runs sees "
        "the same rows: a finding states a category, severity, count and event links, never a "
        "path, command, host or secret. Filters combine with AND; `facets` count under the "
        "other filters. A project-bound key sees only its project."
    ),
)
async def list_security_findings(
    request: Request,
    principal: Reader,
    severity: Annotated[list[SeverityParam] | None, Query()] = None,
    detector: Annotated[list[DetectorId] | None, Query()] = None,
    project_id: Annotated[str | None, Query(pattern=id_pattern(IdKind.PROJECT))] = None,
    run_status: Annotated[list[RunStatusName] | None, Query()] = None,
    since: Annotated[datetime | None, Query(description="Runs started at or after.")] = None,
    until: Annotated[datetime | None, Query(description="Runs started at or before.")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> s.SecurityFindingPage:
    for name, value in (("since", since), ("until", until)):
        if value is not None and value.tzinfo is None:
            raise AppError(
                "INVALID_TIME",
                f"`{name}` needs a timezone, for example 2026-01-01T00:00:00Z.",
                category=ErrorCategory.VALIDATION,
                status_code=422,
            )
    return await _service(request).list_findings(
        principal,
        project_id=project_id,
        severities=list(severity or []),
        detectors=list(detector or []),
        run_statuses=list(run_status or []),
        since=since,
        until=until,
        limit=limit,
        cursor=cursor,
    )


@router.get(
    "/v1/security/detectors",
    response_model=s.DetectorList,
    responses=_ERRORS,
    summary="List the security detectors",
    description=(
        "Id, title, fixed description, severities and rule version of each detector. With "
        "`project_id`, also whether it is muted and whether it can report for that project (the "
        "destination detector needs an allowlist)."
    ),
)
async def list_security_detectors(
    request: Request,
    principal: Reader,
    project_id: Annotated[str | None, Query(pattern=id_pattern(IdKind.PROJECT))] = None,
) -> s.DetectorList:
    return await _service(request).detectors(principal, project_id)


@router.get(
    "/v1/projects/{project_id}/security-settings",
    response_model=s.SecuritySettingsOut,
    responses=_ERRORS,
    summary="Read a project's security settings",
    description="Capture mode, muted detectors and destination allowlist. Owners and admins only.",
)
async def get_security_settings(
    project_id: str, request: Request, principal: Admin
) -> s.SecuritySettingsOut:
    return await _service(request).get_settings(principal, project_id)


@router.put(
    "/v1/projects/{project_id}/security-settings",
    response_model=s.SecuritySettingsOut,
    responses=_ERRORS,
    summary="Change a project's security settings",
    description=(
        "Fields left out stay as they are; a list given replaces the stored list. Owners and "
        "admins only. Every change is audit-logged (`security.settings.update`). Mutes and "
        "allowlist entries apply to a run when it is next analysed (`rebuild-findings`); the "
        "capture mode applies to the next accepted batch."
    ),
)
async def put_security_settings(
    project_id: str, body: s.SecuritySettingsIn, request: Request, principal: Admin
) -> s.SecuritySettingsOut:
    return await _service(request).put_settings(principal, project_id, body)

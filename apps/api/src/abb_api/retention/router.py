"""Retention routes (Phase 19b, ADR-159): a workspace's policy, a dry-run preview and the reports
of the retention job. Policy changes are audited and for owners and admins; reading is for every
member. Nothing here deletes anything or can start the retention job."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from abb_api.authz import actions
from abb_api.authz.dependencies import require
from abb_api.authz.principal import Principal
from abb_api.core.errors import AppError, ErrorCategory, ErrorEnvelope
from abb_api.retention import model as m
from abb_api.retention import schemas as s
from abb_api.retention.service import RetentionApiService

router = APIRouter(tags=["retention"])

Reader = Annotated[Principal, Depends(require(actions.RETENTION_READ))]
Admin = Annotated[Principal, Depends(require(actions.RETENTION_WRITE))]

_ERRORS: dict[int | str, dict[str, Any]] = {
    code: {"description": text, "model": ErrorEnvelope}
    for code, text in {
        401: "Missing or invalid credential.",
        403: "The caller's role may not use this route.",
        404: "Workspace not found, or not the caller's (never reveals other tenants).",
        422: "A value is outside its range; the message never repeats the value.",
        429: "Too many requests for this workspace; honour Retry-After.",
        503: "A dependency is unavailable; retry with backoff.",
    }.items()
}
_PREVIEW_ERRORS: dict[int | str, dict[str, Any]] = {
    **_ERRORS,
    504: {"description": "The preview took too long; retry later.", "model": ErrorEnvelope},
}


def _service(request: Request) -> RetentionApiService:
    service: RetentionApiService = request.app.state.retention
    return service


def _invalid(field: str) -> AppError:
    return AppError(
        "RETENTION_POLICY_INVALID",
        "A retention value must be a whole number of days or 'forever'.",
        category=ErrorCategory.VALIDATION,
        status_code=422,
        details={"tier": field, "reason": "malformed"},
    )


def _proposed(values: dict[str, str | None]) -> dict[m.Tier, m.TierSetting]:
    result: dict[m.Tier, m.TierSetting] = {}
    for name, raw in values.items():
        if raw is None:
            continue
        if raw == m.FOREVER:
            result[m.Tier(name)] = m.FOREVER
        elif raw.isascii() and raw.isdigit() and len(raw) <= 6:
            result[m.Tier(name)] = int(raw)
        else:
            raise _invalid(name)
    return result


@router.get(
    "/v1/workspaces/{workspace_id}/retention",
    response_model=s.RetentionPolicyOut,
    responses=_ERRORS,
    summary="Read a workspace's retention policy",
    description=(
        "For each tier (artifacts, trace detail, run record, audit log): the days in force "
        "(null: kept forever), whether the workspace chose it and the environment default. Every "
        "member may read it."
    ),
)
async def get_retention(
    workspace_id: str, request: Request, principal: Reader
) -> s.RetentionPolicyOut:
    return await _service(request).get(principal, workspace_id)


@router.put(
    "/v1/workspaces/{workspace_id}/retention",
    response_model=s.RetentionPolicyOut,
    responses=_ERRORS,
    summary="Change a workspace's retention policy",
    description=(
        "Tiers left out stay as they are; a whole number of days (at least 7) sets that tier; "
        '`"forever"` keeps it for good; `null` returns it to the environment default. Artifacts '
        "cannot outlast trace detail, nor trace detail the run record. Owners and admins only. "
        "Each changed tier writes one audit entry (`retention.policy.update`: the tier, the old "
        "and the new value, and when it takes effect). A shortening takes effect after the grace "
        "period (`grace_hours`); until then the longer period is enforced, and saving the "
        "previous value again cancels it. Lengthening is immediate. Only an owner may change the "
        "audit tier (403 for an admin), whose minimum is `audit_floor_days`. Shortening deletes "
        "nothing by itself: the retention job applies the policy, and in dry-run mode (the "
        "default) only reports what it would delete."
    ),
)
async def put_retention(
    workspace_id: str, body: s.RetentionPolicyIn, request: Request, principal: Admin
) -> s.RetentionPolicyOut:
    return await _service(request).update(principal, workspace_id, body, request)


@router.get(
    "/v1/workspaces/{workspace_id}/retention/preview",
    response_model=s.RetentionPreviewOut,
    responses=_PREVIEW_ERRORS,
    summary="Preview what a retention policy would delete",
    description=(
        "Counts, per tier, the runs, events, artifact bytes and other rows that would be deleted "
        "now under the saved policy or a proposed one (`artifacts_days`, `trace_days`, "
        "`run_days`, `audit_days`: a number or `forever`; tiers left out use the saved value). "
        "Nothing is saved or deleted. Bounded in time and size: `truncated` marks a lower bound. "
        "`tiers` limits the tiers measured (comma-separated)."
    ),
)
async def preview_retention(
    workspace_id: str,
    request: Request,
    principal: Reader,
    tiers: Annotated[str | None, Query(max_length=64)] = None,
    artifacts_days: Annotated[str | None, Query(max_length=8)] = None,
    trace_days: Annotated[str | None, Query(max_length=8)] = None,
    run_days: Annotated[str | None, Query(max_length=8)] = None,
    audit_days: Annotated[str | None, Query(max_length=8)] = None,
) -> s.RetentionPreviewOut:
    wanted: list[m.Tier] = []
    for name in tiers.split(",") if tiers else [t.value for t in m.TIERS]:
        try:
            tier = m.Tier(name.strip())
        except ValueError:
            raise _invalid("tiers") from None
        if tier not in wanted:
            wanted.append(tier)
    proposed = _proposed(
        {
            "artifacts": artifacts_days,
            "trace": trace_days,
            "run": run_days,
            "audit": audit_days,
        }
    )
    return await _service(request).preview(principal, workspace_id, wanted, proposed)


@router.get(
    "/v1/workspaces/{workspace_id}/retention/reports",
    response_model=s.RetentionReportsOut,
    responses=_ERRORS,
    summary="Read the retention job's latest reports",
    description=(
        "What the retention job found (dry run) or did (enforce) for this workspace, newest "
        "first: counts per tier and tick, never content. Every member may read them."
    ),
)
async def retention_reports(
    workspace_id: str,
    request: Request,
    principal: Reader,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> s.RetentionReportsOut:
    return await _service(request).reports(principal, workspace_id, limit)

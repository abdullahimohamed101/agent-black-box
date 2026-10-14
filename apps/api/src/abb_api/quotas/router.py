"""Quota routes (Phase 19a, ADR-154): a project's usage, and its audited overrides."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from abb_api.authz import actions
from abb_api.authz.dependencies import require
from abb_api.authz.principal import Principal
from abb_api.core.errors import ErrorEnvelope
from abb_api.quotas import schemas as s
from abb_api.quotas.service import QuotaApiService

router = APIRouter(tags=["quotas"])

Reader = Annotated[Principal, Depends(require(actions.PROJECT_READ))]
Admin = Annotated[Principal, Depends(require(actions.PROJECT_WRITE))]

_ERRORS: dict[int | str, dict[str, Any]] = {
    code: {"description": text, "model": ErrorEnvelope}
    for code, text in {
        401: "Missing or invalid credential.",
        403: "The caller's role or key may not use this route.",
        404: "Not found, or not visible to this caller (never reveals other tenants).",
        422: "A value is outside its range; the message never repeats the value.",
        503: "A dependency is unavailable; retry with backoff.",
    }.items()
}


def _service(request: Request) -> QuotaApiService:
    service: QuotaApiService = request.app.state.quotas
    return service


@router.get(
    "/v1/projects/{project_id}/quotas",
    response_model=s.ProjectQuotasOut,
    responses=_ERRORS,
    summary="Read a project's quota limits and usage",
    description=(
        "For each quota: the limit in force, the environment default, whether it is overridden, "
        "what is used now and the percentage. `status` is `near_limit` from 80 percent and "
        "`at_limit` once new items are being refused. Daily quotas count the current UTC day. "
        "Any role that may read projects; a project-bound key sees only its own project."
    ),
)
async def get_project_quotas(
    project_id: str, request: Request, principal: Reader
) -> s.ProjectQuotasOut:
    return await _service(request).usage(principal, project_id)


@router.put(
    "/v1/projects/{project_id}/quotas",
    response_model=s.ProjectQuotasOut,
    responses=_ERRORS,
    summary="Override a project's quotas",
    description=(
        "Fields left out stay as they are; a whole number sets that quota's override; `null` "
        "removes it (the environment default applies again). Owners and admins only. Each "
        "changed quota writes one audit entry (`project.quota.update`, details: the quota's name, "
        "old and new value). Lowering a limit below current use refuses new items only; nothing "
        "already stored is touched."
    ),
)
async def put_project_quotas(
    project_id: str, body: s.QuotaOverridesIn, request: Request, principal: Admin
) -> s.ProjectQuotasOut:
    return await _service(request).update(principal, project_id, body)

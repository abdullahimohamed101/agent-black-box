"""Policy management routes (Phase 14, ADR-140): read the governing policy and its history, diff
two versions, validate a draft, publish a version and dry-run a draft against recorded events.

Reading needs `policy.read` (owner, admin, developer, security); validating, publishing and the
dry run need `policy.write` (owner, admin), which an API key can never hold. Nothing here accepts
or returns captured content; the dry run returns counts, rule ids and ids.
"""

from typing import Annotated, Any

from abb_event_schema.ids import IdKind, id_pattern
from fastapi import APIRouter, Depends, Query, Request

from abb_api.authz import actions
from abb_api.authz.dependencies import require
from abb_api.authz.principal import Principal
from abb_api.core.errors import ErrorEnvelope
from abb_api.policies import schemas as s
from abb_api.policies.api_service import PolicyApiService

router = APIRouter(tags=["policies"])

Reader = Annotated[Principal, Depends(require(actions.POLICY_READ))]
Writer = Annotated[Principal, Depends(require(actions.POLICY_WRITE))]

_ERRORS: dict[int | str, dict[str, Any]] = {
    code: {"description": text, "model": ErrorEnvelope}
    for code, text in {
        401: "Missing or invalid credential.",
        403: "The caller's role or key may not use this route.",
        404: "Not found, or not visible to this caller (never reveals other tenants).",
        409: "The scope has a newer version than `base_version`.",
        422: "The document or a parameter is invalid; `details.issues` lists line-level problems.",
        429: "Too many policy changes; see `Retry-After`.",
        504: "The dry run took too long.",
    }.items()
}
ProjectParam = Annotated[
    str | None,
    Query(
        pattern=id_pattern(IdKind.PROJECT),
        description="The project; omit for the workspace-wide base policy.",
    ),
]


def _service(request: Request) -> PolicyApiService:
    service: PolicyApiService = request.app.state.policies
    return service


@router.get(
    "/v1/policies/current",
    response_model=s.CurrentPolicyOut,
    responses=_ERRORS,
    summary="Read the policy that governs a project",
    description=(
        "With `project_id`, the effective policy: the project's own latest version, else the "
        "workspace base, else `source: default` (nothing published; every action is allowed). "
        "Without it, the workspace base. Enforcement is cooperative: it applies to agents that "
        "ask the policy service through the SDK."
    ),
)
async def current_policy(
    request: Request, principal: Reader, project_id: ProjectParam = None
) -> s.CurrentPolicyOut:
    return await _service(request).current(principal, project_id)


@router.get(
    "/v1/policies/versions",
    response_model=s.PolicyHistory,
    responses=_ERRORS,
    summary="List the published versions of a scope",
    description=(
        "Newest first, without document text. With `project_id` only that project's own "
        "versions are listed (not the inherited base)."
    ),
)
async def policy_versions(
    request: Request,
    principal: Reader,
    project_id: ProjectParam = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before_version: Annotated[int | None, Query(ge=1)] = None,
) -> s.PolicyHistory:
    return await _service(request).history(
        principal, project_id, limit=limit, before_version=before_version
    )


@router.get(
    "/v1/policies/versions/{version}",
    response_model=s.PolicyVersionDetail,
    responses=_ERRORS,
    summary="Read one published version with its text",
)
async def policy_version(
    version: int, request: Request, principal: Reader, project_id: ProjectParam = None
) -> s.PolicyVersionDetail:
    return await _service(request).version(principal, project_id, version)


@router.get(
    "/v1/policies/diff",
    response_model=s.DiffOut,
    responses=_ERRORS,
    summary="Compare two versions of a scope",
    description="Unified diff text (at most 64 KiB; `truncated` says if it was cut).",
)
async def policy_diff(
    request: Request,
    principal: Reader,
    from_version: Annotated[int, Query(ge=1)],
    to_version: Annotated[int, Query(ge=1)],
    project_id: ProjectParam = None,
) -> s.DiffOut:
    return await _service(request).diff(principal, project_id, from_version, to_version)


@router.post(
    "/v1/policies/validate",
    response_model=s.ValidateOut,
    responses=_ERRORS,
    summary="Validate a draft policy",
    description=(
        "No side effects and no audit row. Problems come back as `issues` with line, column, "
        "code and a fixed message; the response is 200 either way."
    ),
)
async def validate_policy(body: s.ValidateIn, request: Request, principal: Writer) -> s.ValidateOut:
    return _service(request).validate(principal, body)


@router.post(
    "/v1/policies/publish",
    response_model=s.PolicyVersionDetail,
    status_code=201,
    responses=_ERRORS,
    summary="Publish a new policy version",
    description=(
        "Validates, then stores version N+1 of the scope (a project, or the workspace base) and "
        "appends the `policy.publish` audit row in one transaction. An invalid document is a "
        "422 with `details.issues` and stores nothing. Existing versions are never changed."
    ),
)
async def publish_policy(
    body: s.PublishIn, request: Request, principal: Writer
) -> s.PolicyVersionDetail:
    return await _service(request).publish(principal, body)


@router.post(
    "/v1/policies/dry-run",
    response_model=s.DryRunOut,
    responses=_ERRORS,
    summary="Evaluate a draft against a project's recorded events",
    description=(
        "Replays the project's newest runs (bounded by runs, events and time) through the draft "
        "and returns counts by decision and rule plus a few sample hits made of ids, capability, "
        "risk class, decision and rule id. No command, path or argument is returned."
    ),
)
async def dry_run_policy(body: s.DryRunIn, request: Request, principal: Writer) -> s.DryRunOut:
    return await _service(request).dry_run(principal, body)

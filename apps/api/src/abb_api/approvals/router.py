"""Decision and approvals routes (Phase 14 step 2, ADR-141).

Agent side, API key only (`policy:check` or `events:write`, a key bound to a project):
`POST /v1/policy/decide`, `GET /v1/approvals/{id}/wait`, `POST /v1/approvals/{id}/consume`,
`DELETE /v1/approvals/{id}`. Person side, session only (a role that holds `approval.read` or
`approval.decide`; an API key can never hold either): the list, the count, one approval and the
decision. A request body on the agent routes is read with a hard size limit before it is parsed.
"""

import copy
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ValidationError

from abb_api.approvals import schemas as s
from abb_api.approvals.intent import IntentIn
from abb_api.approvals.service import ApprovalService
from abb_api.authz import actions
from abb_api.authz.audit import note_denial
from abb_api.authz.dependencies import require
from abb_api.authz.principal import Principal
from abb_api.core.errors import AppError, ErrorCategory, ErrorEnvelope
from abb_api.ingestion.body import read_body, require_json_content_type

router = APIRouter(tags=["approvals"])

Agent = Annotated[Principal, Depends(require(actions.APPROVAL_USE))]
Asker = Annotated[Principal, Depends(require(actions.POLICY_DECIDE))]
Reader = Annotated[Principal, Depends(require(actions.APPROVAL_READ))]
Decider = Annotated[Principal, Depends(require(actions.APPROVAL_DECIDE))]

_ERRORS: dict[int | str, dict[str, Any]] = {
    code: {"description": text, "model": ErrorEnvelope}
    for code, text in {
        401: "Missing or invalid credential.",
        403: "The credential may not use this route (people only, or keys only).",
        404: "Not found, or not visible to this caller (never reveals other tenants).",
        409: "The approval is not in a state that allows this; `code` says which.",
        413: "The request body is too large.",
        422: "The intent or a parameter is invalid; no value is echoed.",
        429: "Too many requests or waiters; see `Retry-After`.",
        503: "A dependency is unavailable; apply the fail mode.",
    }.items()
}


def _service(request: Request) -> ApprovalService:
    service: ApprovalService = request.app.state.approvals
    return service


def _inline(model: type[BaseModel]) -> dict[str, Any]:
    """The model's JSON schema with its `$defs` inlined, for a body that is parsed by hand."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                return walk(copy.deepcopy(defs[ref.rsplit("/", 1)[1]]))
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return dict(walk(schema))


def _body_schema(model: type[BaseModel]) -> dict[str, Any]:
    return {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": _inline(model)}},
        }
    }


async def _read[M: BaseModel](request: Request, model: type[M]) -> M:
    """Parse a JSON body of at most `approval_max_body_bytes`; an error names fields, not values."""
    require_json_content_type(request)
    raw = await read_body(request, request.app.state.settings.approval_max_body_bytes)
    try:
        return model.model_validate_json(raw)
    except ValidationError as error:
        fields = [
            {"loc": [str(p) for p in e["loc"]], "msg": e["msg"], "type": e["type"]}
            for e in error.errors(include_input=False, include_url=False)
        ]
        raise AppError(
            "REQUEST_INVALID",
            "Request validation failed.",
            category=ErrorCategory.VALIDATION,
            status_code=422,
            details={"errors": fields},
        ) from None


# ----------------------------------------------------------------------------- agent side


@router.post(
    "/v1/policy/decide",
    response_model=s.DecideOut,
    responses=_ERRORS,
    summary="Ask the policy whether an action may happen",
    description=(
        "The SDK posts the action it is about to take and gets `allow`, `deny` or "
        "`require_approval`. For `require_approval` an approval request is created (or the "
        "caller's open one for the same intent is returned) and the caller waits on "
        "`GET /v1/approvals/{id}/wait`. The server writes the `policy.decision`, "
        "`policy.action.blocked` and `approval.requested` events itself. Needs a key bound to a "
        "project with scope `policy:check` or `events:write`. Enforcement is cooperative: it "
        "applies to agents that ask."
    ),
    openapi_extra=_body_schema(IntentIn),
)
async def decide(request: Request, principal: Asker) -> s.DecideOut:
    return await _service(request).decide(principal, await _read(request, IntentIn))


@router.get(
    "/v1/approvals/pending-count",
    response_model=s.PendingCount,
    responses=_ERRORS,
    summary="How many requests are waiting for a decision",
    description="For the top bar. People with `approval.read` only.",
)
async def pending_count(request: Request, principal: Reader) -> s.PendingCount:
    return await _service(request).pending_count(principal)


@router.get(
    "/v1/approvals/{approval_id}/wait",
    response_model=s.WaitOut,
    responses=_ERRORS,
    summary="Wait for an approval to be decided",
    description=(
        "Long poll for the key that asked. Returns at once when the approval is no longer "
        "waiting; otherwise at the first change or after `timeout` seconds (at most 25) with the "
        "current state. No database connection is held while waiting; concurrent waiters are "
        "capped per workspace and per process (429 `TOO_MANY_WAITERS`). Another key's, "
        "project's or workspace's approval is the same 404 as an unknown one."
    ),
)
async def wait(
    approval_id: str,
    request: Request,
    principal: Agent,
    seconds: Annotated[float, Query(alias="timeout", ge=0, le=25)] = 25,
) -> s.WaitOut:
    return await _service(request).wait(principal, approval_id, seconds)


@router.post(
    "/v1/approvals/{approval_id}/consume",
    response_model=s.ConsumeOut,
    responses=_ERRORS,
    summary="Spend an approval for exactly this action, once",
    description=(
        "Present the whole intent again. The server recomputes its hash and spends the approval "
        "only if it is approved, unexpired, unspent, the caller's own and bound to this very "
        "intent, in one atomic statement. Refusals are `APPROVAL_NOT_FOUND` (404), "
        "`APPROVAL_NOT_APPROVED`, `APPROVAL_EXPIRED`, `APPROVAL_CONSUMED` and "
        "`APPROVAL_INTENT_MISMATCH` (409); none echoes the intent. A lost response cannot be "
        "retried: the second call is `APPROVAL_CONSUMED`."
    ),
    openapi_extra=_body_schema(s.ConsumeIn),
)
async def consume(approval_id: str, request: Request, principal: Agent) -> s.ConsumeOut:
    return await _service(request).consume(
        principal, approval_id, await _read(request, s.ConsumeIn)
    )


@router.delete(
    "/v1/approvals/{approval_id}",
    status_code=204,
    responses=_ERRORS,
    summary="Withdraw a waiting approval request",
    description="Only the key that asked, while the request is still waiting (409 otherwise).",
)
async def cancel(approval_id: str, request: Request, principal: Agent) -> Response:
    await _service(request).cancel(principal, approval_id)
    return Response(status_code=204)


# ----------------------------------------------------------------------------- person side


@router.get(
    "/v1/approvals",
    response_model=s.ApprovalList,
    responses=_ERRORS,
    summary="List approval requests",
    description=(
        "Newest first. `state=pending` is requests still waiting; `decided` is everything else "
        "(approved, denied, expired, cancelled). Content is secret-scrubbed. People with "
        "`approval.read` only."
    ),
)
async def list_approvals(
    request: Request,
    principal: Reader,
    state: Annotated[Literal["pending", "decided", "all"], Query()] = "pending",
    project_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> s.ApprovalList:
    return await _service(request).list_approvals(
        principal, state=state, project_id=project_id, limit=limit, cursor=cursor
    )


@router.get(
    "/v1/approvals/{approval_id}",
    response_model=s.ApprovalDetail,
    responses=_ERRORS,
    summary="Read one approval request",
    description="What the approver is asked to approve, after secret scrubbing. Never the hash.",
)
async def approval_detail(
    approval_id: str, request: Request, principal: Reader
) -> s.ApprovalDetail:
    return await _service(request).detail(principal, approval_id)


@router.post(
    "/v1/approvals/{approval_id}/decision",
    response_model=s.ApprovalDetail,
    responses=_ERRORS,
    summary="Approve or deny a request",
    description=(
        "A signed-in person whose role satisfies the rule's `approver_role` (owner; admin or "
        "owner; security, admin or owner; developer, security, admin or owner; unset: owner or "
        "admin) and who holds `payload.read`. The requester cannot approve a request made with "
        "their own key unless the rule allows it. One atomic transition: a concurrent decision, "
        "cancellation or expiry leaves exactly one winner and the loser gets "
        "409 `APPROVAL_NOT_REQUESTED`."
    ),
)
async def decide_approval(
    approval_id: str, body: s.DecisionIn, request: Request, principal: Decider
) -> s.ApprovalDetail:
    try:
        return await _service(request).decide_as_person(principal, approval_id, body)
    except AppError as error:
        if error.code in ("APPROVER_ROLE_REQUIRED", "SELF_APPROVAL_FORBIDDEN"):
            await note_denial(request, principal, actions.APPROVAL_DECIDE)
        raise

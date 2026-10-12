"""Evaluator definitions: `GET/POST /v1/evaluators`, `DELETE /v1/evaluators/{evaluator_id}`."""

from typing import Annotated, Any

from abb_event_schema.ids import IdKind
from fastapi import APIRouter, Depends, Query, Request, Response

from abb_api.authz import actions
from abb_api.authz.audit import audit_allowed
from abb_api.authz.dependencies import require
from abb_api.authz.principal import Principal
from abb_api.core.errors import ErrorEnvelope
from abb_api.evaluations.schemas import EvaluatorIn, EvaluatorOut, EvaluatorPage
from abb_api.evaluations.service import EvaluatorService
from abb_api.ids import public_id

router = APIRouter(prefix="/v1/evaluators", tags=["evaluations"])

Reader = Annotated[Principal, Depends(require(actions.EVALUATION_READ))]
Writer = Annotated[Principal, Depends(require(actions.EVALUATOR_WRITE))]

_ERROR_TEXT: dict[int | str, str] = {
    401: "Missing, malformed, unknown, revoked or expired credentials.",
    403: "The caller lacks the required permission.",
    404: "Not found, or not visible to this caller (never reveals other tenants).",
    409: "A bound, a name or the judge configuration conflicts with the request.",
    422: "The request is invalid.",
    503: "A dependency is unavailable; retry with backoff.",
}
_ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"description": text, "model": ErrorEnvelope} for status, text in _ERROR_TEXT.items()
}


def _service(request: Request) -> EvaluatorService:
    service: EvaluatorService = request.app.state.evaluators
    return service


@router.get(
    "",
    response_model=EvaluatorPage,
    responses=_ERRORS,
    summary="List evaluators",
    description=(
        "Evaluator definitions visible to the caller (a project key sees its project's and the "
        "workspace-wide ones). Path globs and rubrics are null without `payload.read`."
    ),
)
async def list_evaluators(
    request: Request,
    principal: Reader,
    include_archived: Annotated[bool, Query()] = False,
) -> EvaluatorPage:
    return await _service(request).list(principal, include_archived=include_archived)


@router.post(
    "",
    status_code=201,
    response_model=EvaluatorOut,
    responses=_ERRORS,
    summary="Define an evaluator",
    description=(
        "A built-in implementation plus validated configuration. Model-judge evaluators answer "
        "`409 JUDGE_NOT_CONFIGURED` unless a judge provider is configured. At most 100 active "
        "evaluators per workspace."
    ),
)
async def create_evaluator(body: EvaluatorIn, request: Request, principal: Writer) -> EvaluatorOut:
    out = await _service(request).create(principal, body)
    await audit_allowed(
        request, principal, "evaluator.create", resource_kind="evaluator", resource_id=out.id,
        name=out.name, implementation=out.implementation,
    )  # fmt: skip
    return out


@router.delete(
    "/{evaluator_id}",
    status_code=204,
    responses=_ERRORS,
    summary="Archive an evaluator",
    description=(
        "Archives the definition (it is never deleted: existing results keep their snapshot). "
        "Archiving twice is allowed."
    ),
)
async def archive_evaluator(evaluator_id: str, request: Request, principal: Writer) -> Response:
    record = await _service(request).archive(principal, evaluator_id)
    await audit_allowed(
        request, principal, "evaluator.archive", resource_kind="evaluator",
        resource_id=public_id(IdKind.EVALUATOR, record.id), name=record.name,
    )  # fmt: skip
    return Response(status_code=204)

"""Evaluations of a run: `GET/POST /v1/runs/{run_id}/evaluations`, `POST .../verdicts`."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response

from abb_api.authz import actions
from abb_api.authz.audit import audit_allowed
from abb_api.authz.dependencies import require
from abb_api.authz.principal import Principal
from abb_api.core.errors import ErrorEnvelope
from abb_api.evaluations.runs import RunEvaluationService
from abb_api.evaluations.schemas import EvaluationOut, EvaluationPage, TriggerIn, VerdictIn

router = APIRouter(prefix="/v1/runs", tags=["evaluations"])

Reader = Annotated[Principal, Depends(require(actions.EVALUATION_READ))]
Runner = Annotated[Principal, Depends(require(actions.EVALUATION_RUN))]
Labeller = Annotated[Principal, Depends(require(actions.EVALUATION_LABEL))]

_ERROR_TEXT: dict[int | str, str] = {
    401: "Missing, malformed, unknown, revoked or expired credentials.",
    403: "The caller lacks the required permission.",
    404: "Not found, or not visible to this caller (never reveals other tenants).",
    409: (
        "A bound, an archived evaluator, the judge configuration or an expired trace "
        "(`TRACE_EXPIRED`) conflicts with the request."
    ),
    422: "The request is invalid, or the evaluator does not fit the run or the call.",
    503: "A dependency is unavailable; retry with backoff.",
}
_ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"description": text, "model": ErrorEnvelope} for status, text in _ERROR_TEXT.items()
}


def _service(request: Request) -> RunEvaluationService:
    service: RunEvaluationService = request.app.state.run_evaluations
    return service


@router.get(
    "/{run_id}/evaluations",
    response_model=EvaluationPage,
    responses=_ERRORS,
    summary="List a run's evaluations",
    description=(
        "Newest first; `latest` marks the newest row of each evaluator. Scores, labels and "
        "states are visible to every reader; explanations of human and model-judge rows, "
        "`metadata.raw` and failure text need `payload.read`. Model-judge rows carry "
        "`ground_truth: false`."
    ),
)
async def list_evaluations(run_id: str, request: Request, principal: Reader) -> EvaluationPage:
    return await _service(request).list(principal, run_id)


@router.post(
    "/{run_id}/evaluations",
    status_code=202,
    response_model=EvaluationOut,
    responses={
        **_ERRORS,
        200: {
            "description": "A pending evaluation of this evaluator already exists; returned as is.",
            "model": EvaluationOut,
        },
    },
    summary="Start an evaluator on a run",
    description=(
        "Inserts a `pending` evaluation and queues one `evaluate_run` job for the run. While a "
        "live evaluation of the same evaluator exists it is returned (200) and nothing is "
        "created. At most 20 results per evaluator and run (`409 LIMIT_REACHED`). The run is "
        "never changed."
    ),
)
async def trigger_evaluation(
    run_id: str, body: TriggerIn, request: Request, response: Response, principal: Runner
) -> EvaluationOut:
    out, created = await _service(request).trigger(principal, run_id, body)
    response.status_code = 202 if created else 200
    return out


@router.post(
    "/{run_id}/verdicts",
    status_code=201,
    response_model=EvaluationOut,
    responses=_ERRORS,
    summary="Record a human verdict",
    description=(
        "A person's verdict against a `human` evaluator: terminal at insert, never edited (a "
        "correction is a newer row). The label must be one of the evaluator's `allowed_labels`. "
        "At most 50 verdicts per run. Audited without the explanation."
    ),
)
async def record_verdict(
    run_id: str, body: VerdictIn, request: Request, principal: Labeller
) -> EvaluationOut:
    out = await _service(request).verdict(principal, run_id, body)
    await audit_allowed(
        request, principal, "evaluation.label", resource_kind="evaluation", resource_id=out.id,
        run_id=run_id, evaluator_id=body.evaluator_id, label=out.label,
    )  # fmt: skip
    return out

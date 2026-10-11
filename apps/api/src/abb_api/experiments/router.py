"""Experiments: `/v1/experiments` and its sub-resources (variants, runs, evaluate)."""

from typing import Annotated, Any

from abb_event_schema.ids import IdKind
from fastapi import APIRouter, Depends, Query, Request, Response

from abb_api.authz import actions
from abb_api.authz.audit import audit_allowed
from abb_api.authz.dependencies import require
from abb_api.authz.principal import Principal
from abb_api.core.errors import ErrorEnvelope
from abb_api.experiments.schemas import (
    AttachedRunOut,
    AttachIn,
    EvaluateOut,
    ExperimentIn,
    ExperimentOut,
    ExperimentPage,
    VariantIn,
    VariantOut,
)
from abb_api.experiments.service import ExperimentService
from abb_api.ids import public_id

router = APIRouter(prefix="/v1/experiments", tags=["experiments"])

Reader = Annotated[Principal, Depends(require(actions.EXPERIMENT_READ))]
Writer = Annotated[Principal, Depends(require(actions.EXPERIMENT_WRITE))]
Runner = Annotated[Principal, Depends(require(actions.EVALUATION_RUN))]

_ERROR_TEXT: dict[int | str, str] = {
    401: "Missing, malformed, unknown, revoked or expired credentials.",
    403: "The caller lacks the required permission.",
    404: "Not found, or not visible to this caller (never reveals other tenants).",
    409: "A bound, a name, the baseline or an archived resource conflicts with the request.",
    422: "The request is invalid, or a run or evaluator does not belong to the project.",
    503: "A dependency is unavailable; retry with backoff.",
}
_ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"description": text, "model": ErrorEnvelope} for status, text in _ERROR_TEXT.items()
}


def _service(request: Request) -> ExperimentService:
    service: ExperimentService = request.app.state.experiments
    return service


@router.get(
    "",
    response_model=ExperimentPage,
    responses=_ERRORS,
    summary="List experiments",
    description="Newest first, at most 200. A project-bound key sees its project's experiments.",
)
async def list_experiments(
    request: Request,
    principal: Reader,
    project_id: Annotated[str | None, Query()] = None,
    include_archived: Annotated[bool, Query()] = False,
) -> ExperimentPage:
    return await _service(request).list_experiments(
        principal, project_id=project_id, include_archived=include_archived
    )


@router.post(
    "",
    status_code=201,
    response_model=ExperimentOut,
    responses=_ERRORS,
    summary="Create an experiment",
    description=(
        "A named group of runs of one project with a baseline and candidate variants added "
        "afterwards. At most 200 active experiments per workspace and 20 evaluators each."
    ),
)
async def create_experiment(
    body: ExperimentIn, request: Request, principal: Writer
) -> ExperimentOut:
    out = await _service(request).create(principal, body)
    await audit_allowed(
        request, principal, "experiment.create", resource_kind="experiment", resource_id=out.id,
        project_id=out.project_id,
    )  # fmt: skip
    return out


@router.get(
    "/{experiment_id}",
    response_model=ExperimentOut,
    responses=_ERRORS,
    summary="Get an experiment with its variants, control check and metrics",
    description=(
        "Per candidate `changed_variables` and `controlled` (exactly one variable differs from "
        "the baseline, spec §86), observed `warnings` (`not_single_variable`, "
        "`variant_models_vary`, `variant_agents_vary`, `unevaluated_runs`) and per-variant "
        "metrics over finished runs: success rate, cost and duration (mean, median, p95), retry "
        "rate, failure categories and the latest evaluation results per evaluator. Model-judge "
        "scores are opinions (`ground_truth: false` on the evaluator)."
    ),
)
async def get_experiment(experiment_id: str, request: Request, principal: Reader) -> ExperimentOut:
    return await _service(request).get(principal, experiment_id)


@router.delete(
    "/{experiment_id}",
    status_code=204,
    responses=_ERRORS,
    summary="Archive an experiment",
    description="Archived experiments stay readable and accept no further changes. Idempotent.",
)
async def archive_experiment(experiment_id: str, request: Request, principal: Writer) -> Response:
    record = await _service(request).archive(principal, experiment_id)
    await audit_allowed(
        request, principal, "experiment.archive", resource_kind="experiment",
        resource_id=public_id(IdKind.EXPERIMENT, record.id),
    )  # fmt: skip
    return Response(status_code=204)


@router.post(
    "/{experiment_id}/variants",
    status_code=201,
    response_model=VariantOut,
    responses=_ERRORS,
    summary="Add a variant",
    description=(
        "Exactly one baseline, added first, then up to nine candidates. `config` is a flat map "
        "of declared settings visible to every reader: do not put secrets, prompts or paths in it."
    ),
)
async def add_variant(
    experiment_id: str, body: VariantIn, request: Request, principal: Writer
) -> VariantOut:
    out = await _service(request).add_variant(principal, experiment_id, body)
    await audit_allowed(
        request, principal, "experiment.variant.create", resource_kind="experiment",
        resource_id=experiment_id, variant_id=out.id, role=out.variant_role,
    )  # fmt: skip
    return out


@router.post(
    "/{experiment_id}/runs",
    status_code=201,
    response_model=AttachedRunOut,
    responses={
        **_ERRORS,
        200: {
            "description": "The run was already attached to that variant.",
            "model": AttachedRunOut,
        },
    },
    summary="Attach a run to a variant",
    description=(
        "The run must belong to the experiment's project (`422 RUN_PROJECT_MISMATCH`). A run is "
        "in at most one variant of an experiment (`409 RUN_ALREADY_ATTACHED`); at most 500 runs."
    ),
)
async def attach_run(
    experiment_id: str, body: AttachIn, request: Request, response: Response, principal: Writer
) -> AttachedRunOut:
    out, created = await _service(request).attach(principal, experiment_id, body)
    response.status_code = 201 if created else 200
    if created:
        await audit_allowed(
            request, principal, "experiment.run.attach", resource_kind="experiment",
            resource_id=experiment_id, run_id=out.run_id, variant_id=out.variant_id,
        )  # fmt: skip
    return out


@router.delete(
    "/{experiment_id}/runs/{run_id}",
    status_code=204,
    responses=_ERRORS,
    summary="Detach a run",
    description="The run itself and its evaluations are untouched.",
)
async def detach_run(
    experiment_id: str, run_id: str, request: Request, principal: Writer
) -> Response:
    await _service(request).detach(principal, experiment_id, run_id)
    await audit_allowed(
        request, principal, "experiment.run.detach", resource_kind="experiment",
        resource_id=experiment_id, run_id=run_id,
    )  # fmt: skip
    return Response(status_code=204)


@router.post(
    "/{experiment_id}/evaluate",
    status_code=202,
    response_model=EvaluateOut,
    responses=_ERRORS,
    summary="Evaluate every run of the experiment",
    description=(
        "Starts the experiment's evaluators on each (run, evaluator) pair that has no pending "
        "and no completed result (a failed one is retried), at most 500 new evaluations per "
        "call; `remaining` says whether to call again. One job per run. A repeat while "
        "evaluations are pending creates nothing. Archived, human, other-project and "
        "unconfigured judge evaluators are skipped and reported."
    ),
)
async def evaluate_experiment(
    experiment_id: str, request: Request, principal: Runner
) -> EvaluateOut:
    out = await _service(request).evaluate(principal, experiment_id)
    await audit_allowed(
        request, principal, "experiment.evaluate", resource_kind="experiment",
        resource_id=experiment_id, enqueued=out.enqueued, remaining=out.remaining,
    )  # fmt: skip
    return out

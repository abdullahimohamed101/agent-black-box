"""`GET /v1/runs/{run_id}/compare?with_run_id=`: two runs, aligned by what the agent did."""

from typing import Annotated, Any

from abb_event_schema.ids import IdKind, id_pattern
from fastapi import APIRouter, Depends, Query, Request

from abb_api.authz import actions
from abb_api.authz.dependencies import require
from abb_api.authz.principal import Principal
from abb_api.comparison.schemas import ComparisonOut
from abb_api.comparison.service import ComparisonService
from abb_api.core.errors import ErrorEnvelope

router = APIRouter(prefix="/v1/runs", tags=["comparison"])

Reader = Annotated[Principal, Depends(require(actions.RUN_READ))]

_ERROR_TEXT: dict[int | str, str] = {
    401: "Missing, malformed, unknown, revoked or expired credentials.",
    403: "The caller lacks `run.read`.",
    404: "A run was not found, or is not visible to this caller (never reveals other tenants).",
    409: "`TRACE_EXPIRED`: a run's trace was removed by retention, so there is nothing to compare.",
    422: "A parameter is invalid.",
    503: "A dependency is unavailable; retry with backoff.",
}
_ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"description": text, "model": ErrorEnvelope} for status, text in _ERROR_TEXT.items()
}


def _service(request: Request) -> ComparisonService:
    service: ComparisonService = request.app.state.comparison
    return service


@router.get(
    "/{run_id}/compare",
    response_model=ComparisonOut,
    responses=_ERRORS,
    summary="Compare two runs",
    description=(
        "Computed on read from derived data; nothing is stored. Steps are aligned by what the "
        "agent did, with ids, timestamps and temporary paths normalised, and the first "
        "divergence and the first outcome difference are reported. Without `payload.read` the "
        "alignment uses tool, model and span kinds only (`alignment: metadata`), every relation "
        "is computed from that projection, and files are per-side counts. Runs longer than the "
        "event or step caps are cut and flagged; a middle too different to align within the "
        "work budget is reported as unaligned blocks (`alignment_quality: partial`). Comparing a "
        "run with itself is allowed."
    ),
)
async def compare_runs(
    run_id: str,
    request: Request,
    principal: Reader,
    with_run_id: Annotated[
        str,
        Query(
            pattern=id_pattern(IdKind.RUN),
            description="The run to compare against (either order).",
        ),
    ],
) -> ComparisonOut:
    return await _service(request).compare(principal, run_id, with_run_id)

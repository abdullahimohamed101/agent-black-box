"""`GET /v1/runs/{run_id}/findings`: derived reliability findings and run health (spec §80-81)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from abb_api.authz import actions
from abb_api.authz.dependencies import require
from abb_api.authz.principal import Principal
from abb_api.core.errors import ErrorEnvelope
from abb_api.findings.schemas import RunFindingsOut
from abb_api.findings.service import FindingsService

router = APIRouter(prefix="/v1/runs", tags=["findings"])

Reader = Annotated[Principal, Depends(require(actions.RUN_READ))]

_ERROR_TEXT: dict[int | str, dict[str, Any]] = {
    401: {"description": "Missing, malformed, unknown, revoked or expired credentials."},
    403: {"description": "The caller lacks `run.read` / the key lacks `runs:read`."},
    404: {"description": "Not found, or not visible to this caller (never reveals other tenants)."},
    422: {"description": "A parameter is invalid."},
    503: {"description": "A dependency is unavailable; retry with backoff."},
}
_ERRORS: dict[int | str, dict[str, Any]] = {
    status: {**spec, "model": ErrorEnvelope} for status, spec in _ERROR_TEXT.items()
}


def _service(request: Request) -> FindingsService:
    service: FindingsService = request.app.state.findings
    return service


@router.get(
    "/{run_id}/findings",
    response_model=RunFindingsOut,
    responses=_ERRORS,
    summary="List a run's findings and health",
    description=(
        "Derived by the `detect_findings` job; every finding cites events and records its "
        "detector version. Evidence is metadata only, so the body is the same for every caller "
        "who may read the run. `state` says whether the analysis is current."
    ),
)
async def get_findings(run_id: str, request: Request, principal: Reader) -> RunFindingsOut:
    return await _service(request).get(principal, run_id)

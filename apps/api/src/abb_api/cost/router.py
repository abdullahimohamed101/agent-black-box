"""Pricing endpoint: the prices the cost engine uses, so every figure can be checked (§79)."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

from abb_event_schema.ids import IdKind, id_pattern
from fastapi import APIRouter, Depends, Query, Request
from pydantic import AwareDatetime, BaseModel, Field, StringConstraints, field_validator
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.authz import actions
from abb_api.authz.audit import audit_allowed
from abb_api.authz.dependencies import require
from abb_api.authz.principal import Principal
from abb_api.core.errors import AppError, ErrorCategory, ErrorEnvelope
from abb_api.cost.builtin import BUILTIN_ENTRIES
from abb_api.cost.pricing import PriceEntry
from abb_api.cost.repository import CostRepository
from abb_api.ids import public_id
from abb_api.jobs.outbox import OutboxRepository
from abb_api.projects.access import authorise_project
from abb_api.runs.repository import RunRepository

router = APIRouter(prefix="/v1/pricing", tags=["cost"])
rebuild_router = APIRouter(prefix="/v1/cost", tags=["cost"])
Reader = Annotated[Principal, Depends(require(actions.PRICING_READ))]
Writer = Annotated[Principal, Depends(require(actions.PRICING_WRITE))]

MAX_OVERRIDES = 1000  # per workspace; overrides are append-only, so the table needs a bound
MAX_REBUILD_RUNS = 10_000  # the CLI's default; newer runs first
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
LATEST = datetime(2100, 1, 1, tzinfo=UTC)

# Per-million prices: non-negative, finite, within the column (numeric(38, 9)), and sane.
Price = Annotated[Decimal, Field(ge=0, le=1_000_000, decimal_places=9, allow_inf_nan=False)]
Text = Annotated[
    str, StringConstraints(min_length=1, max_length=128, pattern=r"^[^\x00-\x1f\x7f-\x9f]+$")
]


class PriceOut(BaseModel):
    pricing_version: str
    origin: str = Field(description="`builtin` or `override`.")
    provider: str | None
    model_pattern: str
    valid_from: datetime
    valid_to: datetime | None
    input_per_million: Decimal
    output_per_million: Decimal
    cached_input_per_million: Decimal | None
    request_price: Decimal
    currency: str
    source: str
    project_id: str | None = Field(description="Set when an override applies to one project only.")


class PricingOut(BaseModel):
    prices: list[PriceOut]


def _out(entry: PriceEntry) -> PriceOut:
    return PriceOut(
        pricing_version=entry.pricing_version,
        origin=entry.origin,
        provider=entry.provider,
        model_pattern=entry.model_pattern,
        valid_from=entry.valid_from,
        valid_to=entry.valid_to,
        input_per_million=entry.input_per_million,
        output_per_million=entry.output_per_million,
        cached_input_per_million=entry.cached_input_per_million,
        request_price=entry.request_price,
        currency=entry.currency,
        source=entry.source,
        project_id=entry.project_id,
    )


@router.get(
    "",
    response_model=PricingOut,
    responses={
        401: {
            "description": "Missing, malformed, unknown, revoked or expired API key.",
            "model": ErrorEnvelope,
        },
        403: {"description": "The key lacks `runs:read`.", "model": ErrorEnvelope},
        422: {"description": "A parameter is invalid.", "model": ErrorEnvelope},
        404: {
            "description": "Project not found or not visible to this key.",
            "model": ErrorEnvelope,
        },
    },
    summary="List the prices used to compute cost",
    description=(
        "Built-in price entries plus the workspace's overrides (a project-bound key sees "
        "workspace-wide overrides and its own project's). Built-in entries are illustrative "
        "(KI-050)."
    ),
)
async def list_prices(
    request: Request,
    principal: Reader,
    project_id: Annotated[str | None, Query(pattern=id_pattern(IdKind.PROJECT))] = None,
) -> PricingOut:
    engine: AsyncEngine = request.app.state.engine
    async with engine.connect() as conn:
        project = await authorise_project(conn, principal, project_id)
        overrides = await CostRepository(conn, principal.tenant).overrides(project)
    entries = [*BUILTIN_ENTRIES, *(o.entry() for o in overrides)]
    prices = []
    for entry in entries:
        out = _out(entry)
        if entry.project_id is not None:
            out.project_id = public_id(IdKind.PROJECT, uuid.UUID(entry.project_id))
        prices.append(out)
    return PricingOut(prices=prices)


class CreateOverride(BaseModel):
    model_pattern: Text = Field(description="Glob over the model name, e.g. `my-model*`.")
    provider: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    project_id: str | None = Field(
        default=None,
        pattern=id_pattern(IdKind.PROJECT),
        description="Omit for the whole workspace.",
    )
    input_per_million: Price
    output_per_million: Price
    cached_input_per_million: Price | None = None
    request_price: Price = Decimal(0)
    valid_from: AwareDatetime = Field(
        default=EPOCH, description="Applies to calls at or after this time (UTC); default: always."
    )
    note: Annotated[str, StringConstraints(max_length=500)] | None = None

    @field_validator("valid_from")
    @classmethod
    def _within_range(cls, value: datetime) -> datetime:
        if not EPOCH <= value <= LATEST:
            raise ValueError("valid_from must be between 1970 and 2100")
        return value


class RebuildRequest(BaseModel):
    project_id: str | None = Field(default=None, pattern=id_pattern(IdKind.PROJECT))
    since: AwareDatetime | None = Field(
        default=None, description="Only runs started at or after this."
    )
    limit: int = Field(default=MAX_REBUILD_RUNS, ge=1, le=MAX_REBUILD_RUNS)


class RebuildOut(BaseModel):
    matched: int = Field(description="Runs selected (newest first, at most `limit`).")
    queued: int = Field(description="Re-derivation jobs created; already-queued runs add none.")
    truncated: bool = Field(description="True when older runs were left out by `limit`.")


def _write_errors(**extra: str) -> dict[int | str, dict[str, Any]]:
    texts = {
        401: "Missing or invalid credential.",
        403: "The caller may not change prices.",
        404: "Workspace or project not found.",
        422: "The request is invalid.",
        503: "A dependency is unavailable; retry with backoff.",
        **{int(k[1:]): v for k, v in extra.items()},
    }
    return {code: {"description": text, "model": ErrorEnvelope} for code, text in texts.items()}


@router.post(
    "/overrides",
    status_code=201,
    response_model=PriceOut,
    responses=_write_errors(c409="The workspace already has 1000 overrides."),
    summary="Add a price override",
    description=(
        "Owners, admins and billing. Overrides are append-only: a correction is a newer row for "
        "the same pattern. Past runs keep their cost until `POST /v1/cost/rebuild`."
    ),
)
async def create_override(body: CreateOverride, request: Request, principal: Writer) -> PriceOut:
    engine: AsyncEngine = request.app.state.engine
    async with engine.begin() as conn:
        project = await authorise_project(conn, principal, body.project_id)
        costs = CostRepository(conn, principal.tenant)
        await costs.lock_for_create()
        if await costs.count_overrides() >= MAX_OVERRIDES:
            raise AppError(
                "LIMIT_REACHED",
                f"A workspace holds at most {MAX_OVERRIDES} price overrides.",
                category=ErrorCategory.CONFLICT,
                status_code=409,
            )
        override_id = await costs.add_override(
            project_id=project,
            provider=body.provider,
            model_pattern=body.model_pattern,
            input_per_million=body.input_per_million,
            output_per_million=body.output_per_million,
            cached_input_per_million=body.cached_input_per_million,
            request_price=body.request_price,
            valid_from=body.valid_from,
            note=body.note,
        )
        record = next(r for r in await costs.overrides(project) if r.id == override_id)
    out = _out(record.entry())
    if record.project_id is not None:
        out.project_id = public_id(IdKind.PROJECT, record.project_id)
    await audit_allowed(
        request, principal, "pricing_override.create", resource_kind="pricing_override",
        resource_id=str(override_id), model_pattern=body.model_pattern, project=body.project_id,
        provider=body.provider,
    )  # fmt: skip
    return out


@rebuild_router.post(
    "/rebuild",
    status_code=202,
    response_model=RebuildOut,
    responses=_write_errors(),
    summary="Re-derive cost for past runs",
    description=(
        "Queues the newest runs (optionally one project, optionally since a time) to be "
        "re-derived with the current prices. Bounded to 10,000 runs per call; repeat with "
        "`since` for older ones."
    ),
)
async def rebuild_costs(body: RebuildRequest, request: Request, principal: Writer) -> RebuildOut:
    engine: AsyncEngine = request.app.state.engine
    async with engine.begin() as conn:
        project = await authorise_project(conn, principal, body.project_id)
        run_ids = await RunRepository(conn, principal.tenant).run_ids(
            project_id=project, since=body.since, limit=body.limit
        )
        queued = await OutboxRepository(conn, principal.tenant).enqueue_summarize(run_ids)
    await audit_allowed(
        request, principal, "cost.rebuild", resource_kind="workspace",
        resource_id=public_id(IdKind.WORKSPACE, principal.workspace_id),
        matched=len(run_ids), queued=queued, project=body.project_id,
        since=body.since.isoformat() if body.since else None,
    )  # fmt: skip
    return RebuildOut(matched=len(run_ids), queued=queued, truncated=len(run_ids) >= body.limit)

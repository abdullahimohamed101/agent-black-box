"""Pricing endpoint: the prices the cost engine uses, so every figure can be checked (§79)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from abb_event_schema.ids import IdKind, id_pattern
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.auth import scopes
from abb_api.auth.dependencies import require_principal
from abb_api.core.errors import ErrorEnvelope
from abb_api.cost.builtin import BUILTIN_ENTRIES
from abb_api.cost.pricing import PriceEntry
from abb_api.cost.repository import CostRepository
from abb_api.ids import public_id
from abb_api.projects.access import authorise_project
from abb_api.tenancy import Principal

router = APIRouter(prefix="/v1/pricing", tags=["cost"])
Reader = Annotated[Principal, Depends(require_principal(scopes.RUNS_READ))]


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

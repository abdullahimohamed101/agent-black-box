"""Derived figures the comparison reads besides events: cost per model (a derived table)."""

import uuid
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.db import tables as t
from abb_api.tenancy import TenantContext

MAX_MODELS = 50  # per run: a run with more distinct models is cut here


class CostRepository:
    def __init__(self, conn: AsyncConnection, tenant: TenantContext) -> None:
        self._conn = conn
        self._tenant = tenant

    async def by_model(self, run_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, Decimal]]:
        """Total cost per model for each run, from the cost lines the summarizer derived."""
        c = t.cost_calculations.c
        model = func.coalesce(c.model, literal_column("'unknown'"))  # one expression, no binds
        total = func.sum(c.total)
        rows = await self._conn.execute(
            select(c.run_id, model.label("model"), total)
            .where(c.workspace_id == self._tenant.workspace_id, c.run_id.in_(run_ids))
            .group_by(c.run_id, model)
            .order_by(c.run_id, total.desc(), model)
        )
        found: dict[uuid.UUID, dict[str, Decimal]] = defaultdict(dict)
        for run_id, model, total in rows:
            if len(found[run_id]) < MAX_MODELS:
                found[run_id][model] = total
        return dict(found)

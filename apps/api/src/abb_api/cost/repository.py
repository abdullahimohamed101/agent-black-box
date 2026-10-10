"""Persistence for cost lines and price overrides. Every method is bound to one tenant (INV-3)."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from abb_event_schema.ids import to_uuid
from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.cost.builtin import BUILTIN_ENTRIES
from abb_api.cost.engine import CostLine
from abb_api.cost.pricing import PriceBook, PriceEntry
from abb_api.db import tables as t
from abb_api.tenancy import TenantContext
from abb_api.workspaces.repository import lock_workspace

LINE_CHUNK = 1000  # 25 bind parameters per line; one statement allows at most 32767


@dataclass(frozen=True)
class OverrideRecord:
    id: uuid.UUID
    project_id: uuid.UUID | None
    provider: str | None
    model_pattern: str
    input_per_million: Decimal
    output_per_million: Decimal
    cached_input_per_million: Decimal | None
    request_price: Decimal
    valid_from: datetime
    note: str | None
    created_at: datetime

    def entry(self) -> PriceEntry:
        return PriceEntry(
            pricing_version=f"override:{self.id}",
            provider=self.provider,
            model_pattern=self.model_pattern,
            valid_from=self.valid_from,
            input_per_million=self.input_per_million,
            output_per_million=self.output_per_million,
            cached_input_per_million=self.cached_input_per_million,
            request_price=self.request_price,
            source="workspace override",
            origin="override",
            project_id=str(self.project_id) if self.project_id else None,
            created_at=self.created_at,
        )


class CostRepository:
    def __init__(self, conn: AsyncConnection, tenant: TenantContext) -> None:
        self._conn = conn
        self._tenant = tenant

    async def run_project(self, run_id: uuid.UUID) -> uuid.UUID | None:
        row = (
            await self._conn.execute(
                select(t.runs.c.project_id).where(
                    t.runs.c.workspace_id == self._tenant.workspace_id, t.runs.c.id == run_id
                )
            )
        ).first()
        return row.project_id if row else None

    async def overrides(self, project_id: uuid.UUID | None = None) -> list[OverrideRecord]:
        """Overrides that apply to `project_id`: workspace-wide ones, and that project's own."""
        statement = select(t.pricing_overrides).where(
            t.pricing_overrides.c.workspace_id == self._tenant.workspace_id
        )
        if project_id is not None:
            statement = statement.where(
                (t.pricing_overrides.c.project_id.is_(None))
                | (t.pricing_overrides.c.project_id == project_id)
            )
        rows = await self._conn.execute(
            statement.order_by(
                t.pricing_overrides.c.valid_from,
                t.pricing_overrides.c.created_at,
                t.pricing_overrides.c.id,
            )
        )
        return [
            OverrideRecord(**{c: getattr(r, c) for c in OverrideRecord.__dataclass_fields__})
            for r in rows
        ]

    async def count_overrides(self) -> int:
        return int(
            (
                await self._conn.execute(
                    select(func.count())
                    .select_from(t.pricing_overrides)
                    .where(t.pricing_overrides.c.workspace_id == self._tenant.workspace_id)
                )
            ).scalar_one()
        )

    async def lock_for_create(self) -> None:
        """Serialise override creation per workspace, so the bound cannot be raced past."""
        await lock_workspace(self._conn, self._tenant)

    async def price_book(self, project_id: uuid.UUID | None) -> PriceBook:
        records = await self.overrides(project_id)
        return PriceBook([*BUILTIN_ENTRIES, *(r.entry() for r in records)])

    async def add_override(
        self,
        *,
        project_id: uuid.UUID | None,
        provider: str | None,
        model_pattern: str,
        input_per_million: Decimal,
        output_per_million: Decimal,
        cached_input_per_million: Decimal | None,
        request_price: Decimal,
        valid_from: datetime,
        note: str | None,
        override_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        new_id = override_id or uuid.uuid4()
        await self._conn.execute(
            insert(t.pricing_overrides).values(
                workspace_id=self._tenant.workspace_id,
                id=new_id,
                project_id=project_id,
                provider=provider,
                model_pattern=model_pattern,
                input_per_million=input_per_million,
                output_per_million=output_per_million,
                cached_input_per_million=cached_input_per_million,
                request_price=request_price,
                valid_from=valid_from,
                note=note,
                # Python clock, not now(): rows added in one transaction share now(), and the
                # newer of two otherwise equal overrides must win.
                created_at=datetime.now(UTC),
            )
        )
        return new_id

    async def replace_run_lines(
        self,
        run_id: uuid.UUID,
        project_id: uuid.UUID,
        run_started_at: datetime,
        lines: Sequence[CostLine],
    ) -> None:
        """Make the stored lines of a run exactly `lines` (derived: rewritten, never patched)."""
        await self._conn.execute(
            delete(t.cost_calculations).where(
                t.cost_calculations.c.workspace_id == self._tenant.workspace_id,
                t.cost_calculations.c.run_id == run_id,
            )
        )
        rows = [
            {
                "workspace_id": self._tenant.workspace_id,
                "event_id": to_uuid(ln.event_id),
                "run_id": run_id,
                "project_id": project_id,
                "agent_slug": ln.agent_id,
                "span_id": to_uuid(ln.span_id) if ln.span_id else None,
                "occurred_at": ln.occurred_at,
                "run_started_at": run_started_at,
                "provider": ln.provider,
                "model": ln.model,
                "input_tokens": ln.input_tokens,
                "output_tokens": ln.output_tokens,
                "cached_input_tokens": ln.cached_input_tokens,
                "source": ln.source,
                "pricing_version": ln.pricing_version,
                "pricing_origin": ln.pricing_origin,
                "input_cost": ln.input_cost,
                "output_cost": ln.output_cost,
                "cached_cost": ln.cached_cost,
                "request_cost": ln.request_cost,
                "estimated_total": ln.estimated_total,
                "reported_total": ln.reported_total,
                "client_total": ln.client_total,
                "total": ln.total,
                "is_retry": ln.is_retry,
            }
            for ln in lines
        ]
        for start in range(0, len(rows), LINE_CHUNK):
            await self._conn.execute(
                insert(t.cost_calculations).values(rows[start : start + LINE_CHUNK])
            )

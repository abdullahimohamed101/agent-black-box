"""A workspace's retention policy and its reports, as the application's role sees them.

The policy is the one thing here the application writes; reports and tombstones are written only by
the retention job (migration 0101 gives the runtime role SELECT on them and nothing else).
Every statement carries the workspace (INV-3).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.db import tables as t
from abb_api.retention.model import FOREVER, Tier, TierSetting
from abb_api.tenancy import TenantContext


@dataclass(frozen=True)
class StoredPolicy:
    settings: dict[Tier, TierSetting]
    updated_at: datetime | None
    updated_by: str | None


@dataclass(frozen=True)
class Report:
    id: int
    tier: str
    mode: str
    runs: int
    events: int
    artifact_bytes: int
    rows: int
    truncated: bool
    cutoff_at: datetime | None
    started_at: datetime
    finished_at: datetime


def setting_from(days: int | None, forever: bool) -> TierSetting:
    return FOREVER if forever else days


class PolicyRepository:
    def __init__(self, conn: AsyncConnection, tenant: TenantContext) -> None:
        self._conn = conn
        self._ws = tenant.workspace_id

    async def get(self, *, lock: bool = False) -> StoredPolicy:
        """The stored choices; a workspace without a row has the defaults for every tier."""
        query = select(t.retention_policies).where(t.retention_policies.c.workspace_id == self._ws)
        if lock:
            query = query.with_for_update()
        row = (await self._conn.execute(query)).mappings().first()
        if row is None:
            return StoredPolicy({tier: None for tier in Tier}, None, None)
        return StoredPolicy(
            {
                tier: setting_from(row[f"{tier.value}_days"], row[f"{tier.value}_forever"])
                for tier in Tier
            },
            row["updated_at"],
            row["updated_by"],
        )

    async def ensure_row(self, updated_by: str) -> None:
        """Create the all-defaults row if absent, so a read can lock it."""
        await self._conn.execute(
            insert(t.retention_policies)
            .values(workspace_id=self._ws, updated_by=updated_by)
            .on_conflict_do_nothing(index_elements=["workspace_id"])
        )

    async def write(self, settings: dict[Tier, TierSetting], updated_by: str) -> None:
        values: dict[str, object] = {}
        for tier, setting in settings.items():
            values[f"{tier.value}_forever"] = setting == FOREVER
            values[f"{tier.value}_days"] = None if setting in (None, FOREVER) else setting
        await self._conn.execute(
            t.retention_policies.update()
            .where(t.retention_policies.c.workspace_id == self._ws)
            .values(**values, updated_by=updated_by, updated_at=text("now()"))
        )

    async def reports(self, limit: int) -> list[Report]:
        r = t.retention_reports
        rows = await self._conn.execute(
            select(r)
            .where(r.c.workspace_id == self._ws)
            .order_by(r.c.started_at.desc(), r.c.id.desc())
            .limit(limit)
        )
        return [
            Report(
                id=row.id,
                tier=row.tier,
                mode=row.mode,
                runs=row.runs,
                events=row.events,
                artifact_bytes=row.artifact_bytes,
                rows=row.row_count,
                truncated=row.truncated,
                cutoff_at=row.cutoff_at,
                started_at=row.started_at,
                finished_at=row.finished_at,
            )
            for row in rows
        ]


async def policy_of(conn: AsyncConnection, workspace_id: uuid.UUID) -> StoredPolicy:
    return await PolicyRepository(conn, TenantContext(workspace_id)).get()

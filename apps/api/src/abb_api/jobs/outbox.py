"""Transactional outbox (spec §72). Step 4 only enqueues; claiming and retries arrive in step 6."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.db import tables as t
from abb_api.tenancy import TenantContext

SUMMARIZE_RUN = "summarize_run"


class OutboxRepository:
    def __init__(self, conn: AsyncConnection, tenant: TenantContext) -> None:
        self._conn = conn
        self._tenant = tenant

    async def enqueue(
        self,
        *,
        job_type: str,
        dedupe_key: str,
        payload: dict[str, Any],
        available_at: datetime | None = None,
    ) -> bool:
        """Add a pending job unless an identical one is already pending (coalescing).

        Returns True if a new job was created. Written in the caller's transaction, so a job
        exists if and only if the data that needs processing committed.
        """
        values: dict[str, Any] = {
            "id": uuid.uuid4(),
            "job_type": job_type,
            "workspace_id": self._tenant.workspace_id,
            "dedupe_key": dedupe_key,
            "payload": payload,
        }
        if available_at is not None:
            values["available_at"] = available_at
        result = await self._conn.execute(
            insert(t.outbox_jobs)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=["job_type", "dedupe_key"], index_where=text("status = 'pending'")
            )
            .returning(t.outbox_jobs.c.id)
        )
        return result.first() is not None

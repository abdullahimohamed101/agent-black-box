"""The dirty marker: a change a running refresh did not see is never lost (KI-057, ADR-160).

Refresh jobs coalesce: a change that arrives while a refresh job is pending adds nothing, because
that job will read it. The gap is a writer whose transaction is still open when the job is claimed:
the writer sees the job as pending and coalesces into it, the job reads the day without the writer's
uncommitted rows, and the writer commits afterwards. Nobody refreshes that change.

The fix is a version per (workspace, day), bumped by the writer in the same transaction as the
change (so a bump is visible exactly when the change is). The refresh job:

1. makes sure the row exists and reads the version it is about to absorb (`seen`),
2. rebuilds the day,
3. compare-and-sets `refreshed_version = seen WHERE version = seen`.

If the CAS fails, a writer bumped the version after step 1; the job asks for another refresh. A
writer whose transaction is still open holds the row's lock, so step 3 waits for its outcome and
then compares against the committed version: no interleaving loses a change (a deterministic test
drives each one, `tests/test_analytics_dirty.py`).
"""

from collections.abc import Awaitable, Callable
from datetime import date

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.analytics.rollup import refresh_day
from abb_api.db import tables as t
from abb_api.tenancy import TenantContext

D = t.analytics_dirty


async def mark_dirty(conn: AsyncConnection, tenant: TenantContext, day: date) -> None:
    """Record, in the writer's own transaction, that `day`'s rollup needs another look."""
    await conn.execute(
        insert(D)
        .values(workspace_id=tenant.workspace_id, day=day, version=1)
        .on_conflict_do_update(
            index_elements=["workspace_id", "day"], set_={"version": D.c.version + 1}
        )
    )


async def begin_refresh(conn: AsyncConnection, tenant: TenantContext, day: date) -> int:
    """The version this refresh is about to absorb.

    A plain read first: an open writer holds the row's lock, and an upsert (even DO NOTHING) would
    wait for it. Only when no row exists is it created, at version 0; that insert waits for a
    writer that is creating the same row, and then reads the version the writer committed.
    """
    query = select(D.c.version).where(D.c.workspace_id == tenant.workspace_id, D.c.day == day)
    version = (await conn.execute(query)).scalar_one_or_none()
    if version is None:
        await conn.execute(
            insert(D)
            .values(workspace_id=tenant.workspace_id, day=day)
            .on_conflict_do_nothing(index_elements=["workspace_id", "day"])
        )
        version = (await conn.execute(query)).scalar_one()
    return int(version)


async def settle(conn: AsyncConnection, tenant: TenantContext, day: date, seen: int) -> bool:
    """Compare-and-set: True when no writer bumped the version since `seen` (the day is clean)."""
    result = await conn.execute(
        update(D)
        .where(D.c.workspace_id == tenant.workspace_id, D.c.day == day, D.c.version == seen)
        .values(refreshed_version=seen)
    )
    return int(result.rowcount) == 1


async def refresh_with_marker(
    conn: AsyncConnection,
    tenant: TenantContext,
    day: date,
    *,
    after_refresh: Callable[[], Awaitable[None]] | None = None,
) -> bool:
    """Rebuild a day and report whether a change slipped in meanwhile (True: refresh again).

    `after_refresh` is the test seam for a writer that commits between the rebuild and the CAS.
    """
    seen = await begin_refresh(conn, tenant, day)
    await refresh_day(conn, tenant, day)
    if after_refresh is not None:
        await after_refresh()
    return not await settle(conn, tenant, day, seen)


__all__ = ["begin_refresh", "mark_dirty", "refresh_with_marker", "settle"]

"""Helpers for the destructive-path tests: ticks that enforce, fingerprints of what exists."""

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.artifacts.store import LocalFsArtifactStore
from abb_api.db.tables import metadata
from abb_api.retention.batches import Hook, no_hook
from abb_api.retention.runner import RetentionRunnerSettings
from abb_api.retention.tick import TickResult, run_tick
from tests.retention.helpers import NOW

_HASH = "coalesce(md5(string_agg(x::text, '|' ORDER BY x::text)), '')"


def _summary(source: str, where: str = "") -> Any:
    """Count and content hash of `source x` (a table name from this module, never data)."""
    return text(f"SELECT count(*), {_HASH} FROM {source} x {where}")  # noqa: S608


# Every table that holds data of a run, by the column that names the run.
RUN_KEYED = (
    "events", "spans", "cost_calculations", "findings", "run_findings", "run_search",
    "evaluations", "artifacts",
)  # fmt: skip


def make_store(tmp_path: Path) -> LocalFsArtifactStore:
    return LocalFsArtifactStore(tmp_path / "artifacts")


def enforcing(**overrides: Any) -> RetentionRunnerSettings:
    overrides.setdefault("retention_batch_size", 2)  # several batches even for tiny runs
    return RetentionRunnerSettings(retention_mode="enforce", **overrides)


async def enforce(
    engine: AsyncEngine,
    store: LocalFsArtifactStore,
    cfg: RetentionRunnerSettings | None = None,
    *,
    hook: Hook = no_hook,
    mode_probe: Callable[..., Any] | None = None,
    monotonic: Callable[[], float] | None = None,
    stop: Any = None,
    budget: float = 3600.0,
) -> TickResult:
    extra: dict[str, Any] = {"monotonic": monotonic} if monotonic else {}
    return await run_tick(
        engine,
        cfg or enforcing(),
        budget_seconds=budget,
        keep_reports=50,
        now=lambda: NOW,
        store=store,
        hook=hook,
        mode_probe=mode_probe,
        stop=stop,
        **extra,
    )


async def fingerprint(engine: AsyncEngine, run_id: uuid.UUID) -> dict[str, tuple[int, str]]:
    """Row count and content hash of everything a run owns (to prove untouched data is
    byte-identical)."""
    out: dict[str, tuple[int, str]] = {}
    async with engine.connect() as conn:
        for table in RUN_KEYED:
            row = (
                await conn.execute(
                    text(
                        f"SELECT count(*), {_HASH} "  # noqa: S608
                        f"FROM {table} x WHERE run_id = :r"
                    ),
                    {"r": run_id},
                )
            ).one()
            out[table] = (row[0], row[1])
        row = (
            await conn.execute(
                _summary("runs", "WHERE id = :r"),
                {"r": run_id},
            )
        ).one()
        out["runs"] = (row[0], row[1])
        row = (
            await conn.execute(
                _summary("outbox_jobs", "WHERE dedupe_key LIKE '%:' || cast(:r as text)"),
                {"r": str(run_id)},
            )
        ).one()
        out["outbox_jobs"] = (row[0], row[1])
    return out


async def everything(
    engine: AsyncEngine, *, skip: tuple[str, ...] = ()
) -> dict[str, tuple[int, str]]:
    """Count and content hash of every table (dry run must change none of them)."""
    out: dict[str, tuple[int, str]] = {}
    async with engine.connect() as conn:
        for table in metadata.sorted_tables:
            if table.name in skip:
                continue
            row = (
                await conn.execute(
                    text(
                        f"SELECT count(*), {_HASH} "  # noqa: S608
                        f'FROM "{table.name}" x'
                    )
                )
            ).one()
            out[table.name] = (row[0], row[1])
    return out


async def scalar(engine: AsyncEngine, sql: str, **params: Any) -> Any:
    async with engine.connect() as conn:
        return (await conn.execute(text(sql), params)).scalar_one()


async def tombstones(engine: AsyncEngine) -> list[tuple[uuid.UUID, str]]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT run_id, tier FROM retention_tombstones ORDER BY run_id, tier")
        )
        return [(r[0], r[1]) for r in rows]


async def truncate_all(engine: AsyncEngine) -> None:
    names = ", ".join(f'"{t.name}"' for t in metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))

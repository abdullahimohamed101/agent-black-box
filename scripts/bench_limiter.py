"""Overhead of the shared limiter on the ingestion path (Phase 19a step 1, ADR-152).

Starts the API from this checkout in its own process for each configuration against one
throwaway database, drives the same workload at each, and records latency, requests per second and
the counter table's database writes (from `pg_stat_user_tables`):

    cd apps/api && uv run python ../../scripts/bench_limiter.py \
        --admin-url postgresql+asyncpg://USER:PASS@HOST:PORT/postgres --json out.json

Configurations: `off` (in-memory limiter only, the pre-19a behaviour), `blocks` (shared limiter, the
default block divisor 20), `every-request` (shared limiter writing on every request, the worst case).
Rate limits are raised so the workload is never throttled: the cost of the shared check is measured,
not its refusals. Numbers are a regression baseline for one machine, not a capacity claim.
"""

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

sys.path.insert(0, str(Path(__file__).parent))
from bench_ingest import make_events, percentile, post_batch  # noqa: E402
from abb_event_schema.ids import IdKind, new_id  # noqa: E402

API_DIR = Path(__file__).resolve().parents[1] / "apps" / "api"
PORT = 8931

CONFIGS: dict[str, dict[str, str]] = {
    "off": {"ABB_LIMITS_SHARED_ENABLED": "false"},
    "blocks": {"ABB_LIMITS_BLOCK_DIVISOR": "20"},
    # A block of one batch (100 events) per reservation: every request writes the counter.
    "every-request": {"ABB_LIMITS_BLOCK_DIVISOR": "1000"},
}


async def admin(url: str, statement: str) -> None:
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    async with engine.connect() as conn:
        await conn.execute(text(statement))
    await engine.dispose()


async def counter_writes(url: str) -> int:
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.connect() as conn:
        await conn.execute(text("SELECT pg_stat_clear_snapshot()"))
        row = (
            await conn.execute(
                text(
                    "SELECT coalesce(n_tup_ins + n_tup_upd, 0) FROM pg_stat_user_tables "
                    "WHERE relname = 'rate_limit_counters'"
                )
            )
        ).scalar_one_or_none()
    await engine.dispose()
    return int(row or 0)


def start_api(url: str, extra: dict[str, str]) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "ABB_DATABASE_URL": url,
        "ABB_ENVIRONMENT": "development",
        "ABB_LOG_LEVEL": "WARNING",
        # Raised so that nothing is throttled: events/s and bytes/s far above the workload.
        "ABB_RATE_LIMIT_EVENTS_PER_SECOND": "5000",
        "ABB_RATE_LIMIT_BURST_EVENTS": "100000",
        "ABB_RATE_LIMIT_BYTES_PER_SECOND": "100000000",
        "ABB_RATE_LIMIT_BURST_BYTES": "500000000",
        **extra,
    }
    return subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "uvicorn", "abb_api.main:create_app", "--factory",
         "--port", str(PORT), "--log-level", "warning"],
        cwd=API_DIR, env=env,
    )  # fmt: skip


async def wait_ready() -> None:
    async with httpx.AsyncClient() as client:
        for _ in range(100):
            try:
                if (await client.get(f"http://127.0.0.1:{PORT}/readyz")).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.2)
    raise TimeoutError("API did not become ready")


async def drive(key: str, clients: int, batches: int, size: int) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{PORT}", headers=headers, timeout=60
    ) as client:

        async def one() -> tuple[list[float], int]:
            run = {"run_id": new_id(IdKind.RUN), "trace_id": new_id(IdKind.TRACE)}
            out, bad = [], 0
            for i in range(batches):
                ms, status = await post_batch(client, make_events(run, i * size + 1, size))
                out.append(ms)
                bad += status != 202
            return out, bad

        started = time.perf_counter()
        outcomes = await asyncio.gather(*[one() for _ in range(clients)])
        seconds = time.perf_counter() - started
    flat = [ms for out, _ in outcomes for ms in out]
    return {
        "clients": clients,
        "requests": len(flat),
        "non_202": sum(b for _, b in outcomes),
        "p50_ms": round(percentile(flat, 0.5), 1),
        "p95_ms": round(percentile(flat, 0.95), 1),
        "p99_ms": round(percentile(flat, 0.99), 1),
        "req_per_s": round(len(flat) / seconds, 1),
    }


async def micro(url: str, calls: int = 3000) -> list[dict[str, Any]]:
    """The shared check on its own: time `GuardedLimiter.acquire` for a 100-event, 50 KiB request,
    sequentially, against the real database. This is the latency the check adds to a request,
    free of the HTTP stack's noise."""
    sys.path.insert(0, str(API_DIR / "src"))
    from abb_api.core.config import Settings
    from abb_api.limits import Scope, SharedCounters
    from abb_api.limits.factory import default_ingest_bucket, ingest_limiter

    rows: list[dict[str, Any]] = []
    settings = Settings(
        database_url=url,
        rate_limit_events_per_second=5000,
        rate_limit_burst_events=100000,
        rate_limit_bytes_per_second=100_000_000,
        rate_limit_burst_bytes=500_000_000,
    )
    for config, divisor in (("off", None), ("blocks", 20), ("every-request", 1000)):
        engine = create_async_engine(url, pool_size=5)
        counters = SharedCounters(engine) if divisor else None
        guard = ingest_limiter(
            default_ingest_bucket(settings),
            counters,
            settings.model_copy(update={"limits_block_divisor": divisor or 20}),
            events=Scope.INGEST_EVENTS,
            bytes_=Scope.INGEST_BYTES,
        )
        workspace, project = uuid.uuid4(), str(uuid.uuid4())
        for _ in range(50):  # warm the pool and the plan cache
            await guard.acquire(project, events=100, bytes_=51_500, workspace_id=workspace)
        times: list[float] = []
        for _ in range(calls):
            started = time.perf_counter()
            wait = await guard.acquire(project, events=100, bytes_=51_500, workspace_id=workspace)
            times.append((time.perf_counter() - started) * 1000)
            if wait is not None:  # the local bucket ran dry: let it refill, do not time that
                times.pop()
                await asyncio.sleep(wait)
        row = {
            "micro": config,
            "calls": len(times),
            "p50_ms": round(percentile(times, 0.5), 3),
            "p95_ms": round(percentile(times, 0.95), 3),
            "p99_ms": round(percentile(times, 0.99), 3),
            "mean_ms": round(sum(times) / len(times), 3),
            "database_calls": counters.database_calls if counters else 0,
            "fail_open_events": counters.fail_open_events if counters else 0,
        }
        rows.append(row)
        print(json.dumps(row), flush=True)
        await engine.dispose()
    return rows


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--admin-url", required=True, help="a URL on the server, any database")
    parser.add_argument("--batches", type=int, default=400, help="per client")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--rounds", type=int, default=3, help="alternating rounds per config")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--micro-only", action="store_true", help="only time the check itself")
    args = parser.parse_args()

    name = f"abb_p19a_bench_{uuid.uuid4().hex[:8]}"
    url = make_url(args.admin_url).set(database=name).render_as_string(hide_password=False)
    await admin(args.admin_url, f'CREATE DATABASE "{name}"')
    key_file = Path(f"/tmp/{name}.key")  # noqa: S108 - removed below; holds a throwaway dev key
    results: list[dict[str, Any]] = []
    try:
        base_env = {**os.environ, "ABB_DATABASE_URL": url, "ABB_ENVIRONMENT": "development"}
        for command in (
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            [sys.executable, "-m", "abb_api.cli", "seed", "--key-file", str(key_file)],
        ):
            subprocess.run(command, cwd=API_DIR, env=base_env, check=True, capture_output=True)  # noqa: S603
        results += await micro(url)
        if args.micro_only:
            return
        key = key_file.read_text().strip()
        for round_no in range(args.rounds):
            for config, extra in CONFIGS.items():
                server = start_api(url, extra)
                try:
                    await wait_ready()
                    await drive(key, 1, 20, args.batch_size)  # warm-up: pools, caches
                    for clients in (1, 4):
                        before = await counter_writes(url)
                        row = await drive(key, clients, args.batches // clients, args.batch_size)
                        await asyncio.sleep(1.0)  # let pg_stat flush
                        writes = await counter_writes(url) - before
                        row |= {
                            "config": config,
                            "round": round_no,
                            "counter_writes": writes,
                            "counter_writes_per_1000_req": round(1000 * writes / row["requests"], 1),
                        }
                        results.append(row)
                        print(json.dumps(row), flush=True)
                finally:
                    server.send_signal(signal.SIGTERM)
                    server.wait(timeout=30)
    finally:
        key_file.unlink(missing_ok=True)
        await admin(args.admin_url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    if args.json:
        args.json.write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

"""Failed-auth flood against the API: database load and the effect on valid ingestion
(Phase 19a step 2, ADR-153).

Starts the API from this checkout in its own process against one throwaway database and measures,
for the throttle on and off:

* `flood`: bad keys only, from one address (trusted proxy header) and spread over many addresses;
  requests/s, how many were 401 / 429, and database transactions per second
  (`pg_stat_database`, commits + rollbacks) compared with an idle period;
* `valid-only` and `valid+flood`: a stream of valid ingestion batches alone and during a flood;
  latency percentiles and any non-202.

    cd apps/api && uv run python ../../scripts/bench_auth_throttle.py \
        --admin-url postgresql+asyncpg://USER:PASS@HOST:PORT/postgres --json out.json

Numbers are a regression baseline for one machine (the load generator, the API and the database
share it), not a capacity claim.
"""

import argparse
import asyncio
import json
import os
import secrets
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
PORT = 8932
BASE = f"http://127.0.0.1:{PORT}"
ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"

CONFIGS: dict[str, dict[str, str]] = {
    "throttle-off": {"ABB_AUTH_FAIL_ENABLED": "false", "ABB_TRUST_PROXY": "1"},
    "throttle-on": {"ABB_TRUST_PROXY": "1"},
}


async def admin(url: str, statement: str) -> None:
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    async with engine.connect() as conn:
        await conn.execute(text(statement))
    await engine.dispose()


async def transactions(url: str) -> int:
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.connect() as conn:
        await conn.execute(text("SELECT pg_stat_clear_snapshot()"))
        value = (
            await conn.execute(
                text(
                    "SELECT xact_commit + xact_rollback FROM pg_stat_database "
                    "WHERE datname = current_database()"
                )
            )
        ).scalar_one()
    await engine.dispose()
    return int(value)


def start_api(url: str, extra: dict[str, str]) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "ABB_DATABASE_URL": url,
        "ABB_ENVIRONMENT": "development",
        "ABB_LOG_LEVEL": "ERROR",
        "ABB_RATE_LIMIT_EVENTS_PER_SECOND": "5000",
        "ABB_RATE_LIMIT_BURST_EVENTS": "100000",
        "ABB_RATE_LIMIT_BYTES_PER_SECOND": "100000000",
        "ABB_RATE_LIMIT_BURST_BYTES": "500000000",
        **extra,
    }
    return subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "uvicorn", "abb_api.main:create_app", "--factory",
         "--port", str(PORT), "--log-level", "error"],
        cwd=API_DIR, env=env,
    )  # fmt: skip


async def wait_ready() -> None:
    async with httpx.AsyncClient() as client:
        for _ in range(100):
            try:
                if (await client.get(f"{BASE}/readyz")).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.2)
    raise TimeoutError("API did not become ready")


def bad_key() -> str:
    key_id = "".join(secrets.choice(ALPHABET) for _ in range(12))
    return f"abb_live_{key_id}.{secrets.token_urlsafe(32)}"


async def flood(seconds: float, spread: bool, workers: int = 16) -> dict[str, Any]:
    """Bad keys as fast as `workers` connections can send them."""
    counts: dict[int, int] = {}
    deadline = time.perf_counter() + seconds

    async def worker(n: int) -> None:
        async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:
            while time.perf_counter() < deadline:
                address = f"198.51.{n}.{secrets.randbelow(250)}" if spread else "203.0.113.9"
                try:
                    reply = await client.get(
                        "/v1/runs",
                        headers={"Authorization": f"Bearer {bad_key()}", "X-Forwarded-For": address},
                    )
                    code = reply.status_code
                except httpx.HTTPError:  # a dropped connection is a result, not a crash
                    code = 0
                counts[code] = counts.get(code, 0) + 1

    started = time.perf_counter()
    await asyncio.gather(*[worker(n) for n in range(workers)])
    elapsed = time.perf_counter() - started
    total = sum(counts.values())
    return {"requests": total, "req_per_s": round(total / elapsed, 1), "status": counts}


async def valid_stream(key: str, seconds: float, clients: int = 4, size: int = 100) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    deadline = time.perf_counter() + seconds
    async with httpx.AsyncClient(base_url=BASE, headers=headers, timeout=60) as client:

        async def one() -> tuple[list[float], int]:
            run = {"run_id": new_id(IdKind.RUN), "trace_id": new_id(IdKind.TRACE)}
            out, bad, i = [], 0, 0
            while time.perf_counter() < deadline:
                ms, status = await post_batch(client, make_events(run, i * size + 1, size))
                out.append(ms)
                bad += status != 202
                i += 1
            return out, bad

        outcomes = await asyncio.gather(*[one() for _ in range(clients)])
    flat = [ms for out, _ in outcomes for ms in out]
    return {
        "requests": len(flat),
        "non_202": sum(b for _, b in outcomes),
        "p50_ms": round(percentile(flat, 0.5), 1),
        "p95_ms": round(percentile(flat, 0.95), 1),
        "p99_ms": round(percentile(flat, 0.99), 1),
    }


async def measured(url: str, work: Any) -> dict[str, Any]:
    before, started = await transactions(url), time.perf_counter()
    row = await work
    elapsed = time.perf_counter() - started
    await asyncio.sleep(1.0)  # let pg_stat flush
    row["db_tx_per_s"] = round((await transactions(url) - before) / elapsed, 1)
    return row


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--admin-url", required=True)
    parser.add_argument("--seconds", type=float, default=12.0)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    name = f"abb_p19a_throttle_{uuid.uuid4().hex[:8]}"
    url = make_url(args.admin_url).set(database=name).render_as_string(hide_password=False)
    await admin(args.admin_url, f'CREATE DATABASE "{name}"')
    key_file = Path(f"/tmp/{name}.key")  # noqa: S108 - removed below; a throwaway dev key
    results: list[dict[str, Any]] = []

    def record(row: dict[str, Any]) -> None:
        results.append(row)
        print(json.dumps(row), flush=True)

    try:
        env = {**os.environ, "ABB_DATABASE_URL": url, "ABB_ENVIRONMENT": "development"}
        for command in (
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            [sys.executable, "-m", "abb_api.cli", "seed", "--key-file", str(key_file)],
        ):
            subprocess.run(command, cwd=API_DIR, env=env, check=True, capture_output=True)  # noqa: S603
        key = key_file.read_text().strip()
        for round_no in range(args.rounds):
            for config, extra in CONFIGS.items():
                server = start_api(url, extra)
                try:
                    await wait_ready()
                    await valid_stream(key, 2.0)  # warm: pools, plan caches, the key itself
                    tag = {"config": config, "round": round_no}
                    record(tag | {"case": "idle"} | await measured(url, _idle(args.seconds)))
                    record(tag | {"case": "valid-only"}
                           | await measured(url, valid_stream(key, args.seconds)))
                    for spread, label in ((False, "one-address"), (True, "spread")):
                        record(tag | {"case": f"flood-{label}"}
                               | await measured(url, flood(args.seconds, spread)))
                        both = asyncio.gather(
                            valid_stream(key, args.seconds), flood(args.seconds, spread)
                        )
                        row = await measured(url, _pair(both))
                        record(tag | {"case": f"valid+flood-{label}"} | row)
                finally:
                    server.send_signal(signal.SIGTERM)
                    server.wait(timeout=30)
    finally:
        key_file.unlink(missing_ok=True)
        await admin(args.admin_url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    if args.json:
        args.json.write_text(json.dumps(results, indent=2))


async def _idle(seconds: float) -> dict[str, Any]:
    await asyncio.sleep(seconds)
    return {}


async def _pair(both: Any) -> dict[str, Any]:
    valid, bad = await both
    return {"valid": valid, "flood": bad}


if __name__ == "__main__":
    asyncio.run(main())

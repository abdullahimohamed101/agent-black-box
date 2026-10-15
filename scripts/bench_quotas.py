"""Ingestion overhead of project quotas (Phase 19a step 3, ADR-154).

Starts the API from this checkout in its own process against one throwaway database and measures
batch latency for quotas off (`ABB_QUOTAS_ENABLED=false`) and on, alternating over several rounds:

* `1 client x 100`: one SDK exporter's view, batches of 100 new events, one run;
* `4 clients x 100`: four exporters at once, each with its own run (one project: this is where the
  project lock is contended);
* `8 clients x 10`: small batches from eight clients (the lock is taken most often per event);
* `replay x 100`: the same batch of 100 already-stored events posted again (the duplicate path:
  no lock, a lookup only).

    cd apps/api && uv run python ../../scripts/bench_quotas.py \
        --admin-url postgresql+asyncpg://USER:PASS@HOST:PORT/postgres --json out.json

Numbers are a regression baseline for one machine (the load generator, the API and the database
share it), not a capacity claim.
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
PORT = 8933
BASE = f"http://127.0.0.1:{PORT}"
CONFIGS: dict[str, dict[str, str]] = {
    "quotas-off": {"ABB_QUOTAS_ENABLED": "false"},
    "quotas-on": {},
}


async def admin(url: str, statement: str) -> None:
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    async with engine.connect() as conn:
        await conn.execute(text(statement))
    await engine.dispose()


def start_api(url: str, extra: dict[str, str]) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "ABB_DATABASE_URL": url,
        "ABB_ENVIRONMENT": "development",
        "ABB_LOG_LEVEL": "ERROR",
        "ABB_RATE_LIMIT_EVENTS_PER_SECOND": "1000000",
        "ABB_RATE_LIMIT_BURST_EVENTS": "10000000",
        "ABB_RATE_LIMIT_BYTES_PER_SECOND": "1000000000",
        "ABB_RATE_LIMIT_BURST_BYTES": "5000000000",
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


def run_ids() -> dict[str, str]:
    return {"run_id": new_id(IdKind.RUN), "trace_id": new_id(IdKind.TRACE)}


async def stream(key: str, seconds: float, clients: int, size: int) -> dict[str, Any]:
    """New events as fast as `clients` connections can send them (each client has its own run)."""
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    deadline = time.perf_counter() + seconds

    async with httpx.AsyncClient(base_url=BASE, headers=headers, timeout=60) as client:

        async def one() -> tuple[list[float], int]:
            run, out, bad, i = run_ids(), [], 0, 0
            while time.perf_counter() < deadline:
                ms, status = await post_batch(client, make_events(run, i * size + 1, size))
                out.append(ms)
                bad += status != 202
                i += 1
            return out, bad

        started = time.perf_counter()
        outcomes = await asyncio.gather(*[one() for _ in range(clients)])
        elapsed = time.perf_counter() - started
    return summarize([ms for out, _ in outcomes for ms in out], sum(b for _, b in outcomes),
                     size, elapsed)  # fmt: skip


async def replay(key: str, seconds: float, size: int) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    events = make_events(run_ids(), 1, size)
    out, bad = [], 0
    async with httpx.AsyncClient(base_url=BASE, headers=headers, timeout=60) as client:
        await post_batch(client, events)  # the first send stores them
        started, deadline = time.perf_counter(), time.perf_counter() + seconds
        while time.perf_counter() < deadline:
            ms, status = await post_batch(client, events)
            out.append(ms)
            bad += status != 202
        elapsed = time.perf_counter() - started
    return summarize(out, bad, size, elapsed)


def summarize(ms: list[float], bad: int, size: int, elapsed: float) -> dict[str, Any]:
    return {
        "requests": len(ms),
        "non_202": bad,
        "req_per_s": round(len(ms) / elapsed, 1),
        "events_per_s": round(len(ms) * size / elapsed),
        "p50_ms": round(percentile(ms, 0.5), 2),
        "p95_ms": round(percentile(ms, 0.95), 2),
        "p99_ms": round(percentile(ms, 0.99), 2),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--admin-url", required=True)
    parser.add_argument("--seconds", type=float, default=15.0)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    name = f"abb_p19a_quotas_{uuid.uuid4().hex[:8]}"
    url = make_url(args.admin_url).set(database=name).render_as_string(hide_password=False)
    await admin(args.admin_url, f'CREATE DATABASE "{name}"')
    key_file = Path(f"/tmp/{name}.key")  # noqa: S108 - removed below; a throwaway dev key
    results: list[dict[str, Any]] = []
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
                    await stream(key, 2.0, 4, 100)  # warm pools and plan caches
                    cases = {
                        "1 client x 100": stream(key, args.seconds, 1, 100),
                        "4 clients x 100": stream(key, args.seconds, 4, 100),
                        "8 clients x 10": stream(key, args.seconds, 8, 10),
                        "replay x 100": replay(key, args.seconds, 100),
                    }
                    for case, work in cases.items():
                        row = {"config": config, "round": round_no, "case": case} | await work
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

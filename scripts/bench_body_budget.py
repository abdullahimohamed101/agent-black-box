"""Many concurrent large ingestion bodies: memory with and without the bytes-in-flight cap
(Phase 19a step 4, ADR-155, KI-026).

Starts the API from this checkout (one uvicorn process) against a throwaway database and sends
valid ~4.5 MiB ingestion batches from many concurrent clients, once with the cap effectively off
and once with the default (64 MiB), sampling the API process's resident set size every 20 ms. A
second stream of one-event batches runs alongside to show small requests are not starved.

    cd apps/api && uv run python ../../scripts/bench_body_budget.py \
        --admin-url postgresql+asyncpg://USER:PASS@HOST:PORT/postgres --json out.json

Numbers are a regression baseline for one machine (load generator, API and database share it).
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
from abb_event_schema.ids import IdKind, new_id
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

sys.path.insert(0, str(Path(__file__).parent))
from bench_ingest import make_events, percentile  # noqa: E402

API_DIR = Path(__file__).resolve().parents[1] / "apps" / "api"
PORT = 8933
BASE = f"http://127.0.0.1:{PORT}"
MIB = 1024 * 1024
EVENTS = 1000

CONFIGS: dict[str, dict[str, str]] = {
    "cap-off": {"ABB_MAX_BODY_BYTES_IN_FLIGHT": str(8 * 1024 * MIB)},
    "cap-64MiB": {},  # the default
    "cap-16MiB": {"ABB_MAX_BODY_BYTES_IN_FLIGHT": str(16 * MIB)},
}


async def admin(url: str, statement: str) -> None:
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    async with engine.connect() as conn:
        await conn.execute(text(statement))
    await engine.dispose()


def rss_mib(pid: int) -> float:
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True)  # noqa: S603, S607
    return int(out.stdout.strip() or 0) / 1024


def start_api(url: str, extra: dict[str, str]) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "ABB_DATABASE_URL": url,
        "ABB_ENVIRONMENT": "development",
        "ABB_LOG_LEVEL": "ERROR",
        "ABB_QUOTAS_ENABLED": "false",  # the cap is measured, not the quotas
        "ABB_RATE_LIMIT_EVENTS_PER_SECOND": "1000000",
        "ABB_RATE_LIMIT_BURST_EVENTS": "10000000",
        "ABB_RATE_LIMIT_BYTES_PER_SECOND": "10000000000",
        "ABB_RATE_LIMIT_BURST_BYTES": "100000000000",
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


def big_body(run: dict[str, str], n: int) -> bytes:
    events = make_events(run, n * EVENTS + 1, EVENTS)
    for event in events:
        event["payload"] = {"response": "x" * 4300}  # ~4.5 MiB per batch in all
    return json.dumps({"events": events}).encode()


async def sample_rss(pid: int, stop: asyncio.Event) -> list[float]:
    seen: list[float] = []
    while not stop.is_set():
        seen.append(rss_mib(pid))
        await asyncio.sleep(0.02)
    return seen


async def stream(
    key: str, seconds: float, clients: int, size: int, small: bool = False
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    deadline = time.perf_counter() + seconds
    status: dict[str, int] = {}
    latencies: list[float] = []
    async with httpx.AsyncClient(base_url=BASE, headers=headers, timeout=120) as client:

        async def one() -> None:
            run = {"run_id": new_id(IdKind.RUN), "trace_id": new_id(IdKind.TRACE)}
            i = 0
            while time.perf_counter() < deadline:
                body = (
                    json.dumps({"events": make_events(run, i + 1, 1)}).encode()
                    if small
                    else big_body(run, i)
                )
                started = time.perf_counter()
                try:
                    reply = await client.post("/v1/events/batch", content=body)
                    label = str(reply.status_code)
                    if reply.status_code >= 400:  # name the error: BUSY is the cap, others are not
                        label += f" {reply.json().get('error', {}).get('code', '?')}"
                except httpx.HTTPError:
                    label = "connection error"
                if label == "202":
                    latencies.append((time.perf_counter() - started) * 1000)
                status[label] = status.get(label, 0) + 1
                if label.startswith("503"):
                    await asyncio.sleep(0.25)  # what the SDK's backoff does, roughly
                i += 1

        await asyncio.gather(*[one() for _ in range(clients)])
    row: dict[str, Any] = {"clients": clients, "status": status}
    if latencies:
        row |= {
            "p50_ms": round(percentile(latencies, 0.5), 1),
            "p95_ms": round(percentile(latencies, 0.95), 1),
        }
    return row


async def case(pid: int, key: str, seconds: float, clients: int) -> dict[str, Any]:
    baseline = rss_mib(pid)
    stop = asyncio.Event()
    sampler = asyncio.create_task(sample_rss(pid, stop))
    big, small = await asyncio.gather(
        stream(key, seconds, clients, EVENTS), stream(key, seconds, 2, 1, small=True)
    )
    stop.set()
    seen = await sampler
    return {
        "baseline_rss_mib": round(baseline, 1),
        "peak_rss_mib": round(max(seen), 1),
        "large": big,
        "small_alongside": small,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--admin-url", required=True)
    parser.add_argument("--seconds", type=float, default=15.0)
    parser.add_argument("--clients", type=int, default=64)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    name = f"abb_p19a_edge_{uuid.uuid4().hex[:8]}"
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
        for config, extra in CONFIGS.items():
            server = start_api(url, extra)
            try:
                await wait_ready()
                await stream(key, 2.0, 2, EVENTS)  # warm up
                row = {"config": config} | await case(server.pid, key, args.seconds, args.clients)
                results.append(row)
                print(json.dumps(row), flush=True)
            finally:
                server.send_signal(signal.SIGTERM)
                server.wait(timeout=60)
    finally:
        key_file.unlink(missing_ok=True)
        await admin(args.admin_url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    if args.json:
        args.json.write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

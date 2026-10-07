"""Ingestion and read latency against a running stack (spec §61.2, §156).

    cd apps/api && uv run python ../../scripts/bench_ingest.py --key-file ../../.local/dev-api-key

Numbers depend entirely on the machine and the Docker VM; they are a regression baseline for this
repository, not a capacity claim. Record method and environment next to any figure you quote.
"""

import argparse
import asyncio
import json
import statistics
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from abb_event_schema.ids import IdKind, new_id

T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)


def make_events(run: dict[str, str], start: int, count: int) -> list[dict[str, Any]]:
    """A realistic mix: mostly tool calls, some LLM calls with small inline payloads."""
    events = []
    for n in range(start, start + count):
        base: dict[str, Any] = {
            "schema_version": "1.0",
            "event_id": new_id(IdKind.EVENT),
            "run_id": run["run_id"],
            "trace_id": run["trace_id"],
            "span_id": new_id(IdKind.SPAN),
            "agent_id": "coding-agent",
            "occurred_at": (T0 + timedelta(milliseconds=n * 100)).isoformat().replace("+00:00", "Z"),
            "sequence": n,
            "status": "success",
            "duration_ms": 120 + (n % 50),
            "tags": ["bench"],
        }
        if n % 10 == 0:
            base |= {
                "event_type": "llm.request.completed",
                "attributes": {"llm.provider": "demo", "llm.model": "model-x",
                               "llm.input_tokens": 800 + n % 100, "llm.output_tokens": 120,
                               "cost.estimated_usd": 0.004},
                "payload": {"prompt": "Fix the failing test in src/auth/session.ts " * 3,
                            "response": "Changed the expiry check. " * 4},
            }  # fmt: skip
        else:
            base |= {
                "event_type": "tool.call.completed",
                "attributes": {"tool.name": "shell", "tool.operation": "run", "tool.latency_ms": 120},
            }
        events.append(base)
    return events


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def summarize(label: str, ms: list[float], extra: str = "") -> dict[str, Any]:
    row = {
        "case": label,
        "n": len(ms),
        "p50_ms": round(percentile(ms, 0.50), 1),
        "p95_ms": round(percentile(ms, 0.95), 1),
        "p99_ms": round(percentile(ms, 0.99), 1),
        "max_ms": round(max(ms), 1),
        "mean_ms": round(statistics.fmean(ms), 1),
    }
    print(f"{label:46} n={row['n']:<5} p50={row['p50_ms']:>7} p95={row['p95_ms']:>7} "
          f"p99={row['p99_ms']:>7} max={row['max_ms']:>7} {extra}")  # fmt: skip
    return row


async def post_batch(client: httpx.AsyncClient, events: list[dict[str, Any]]) -> tuple[float, int]:
    body = json.dumps({"events": events}).encode()
    started = time.perf_counter()
    response = await client.post("/v1/events/batch", content=body)
    return (time.perf_counter() - started) * 1000, response.status_code


async def timed_get(client: httpx.AsyncClient, path: str) -> float:
    started = time.perf_counter()
    response = await client.get(path)
    response.raise_for_status()
    return (time.perf_counter() - started) * 1000


async def wait_current(client: httpx.AsyncClient, run_id: str, timeout: float = 120) -> float:
    started = time.perf_counter()
    while time.perf_counter() - started < timeout:
        if (await client.get(f"/v1/runs/{run_id}")).json()["summary_state"] == "current":
            return (time.perf_counter() - started) * 1000
        await asyncio.sleep(0.05)
    raise TimeoutError(run_id)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--batches", type=int, default=300)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--json", type=Path, help="also write raw results here")
    args = parser.parse_args()
    key = args.key_file.read_text().strip()
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    results: list[dict[str, Any]] = []
    sample = json.dumps({"events": make_events({"run_id": new_id(IdKind.RUN), "trace_id": new_id(IdKind.TRACE)}, 1, args.batch_size)})
    print(f"batch = {args.batch_size} events, ~{len(sample) / 1024:.1f} KiB uncompressed JSON "
          f"({len(sample) / args.batch_size:.0f} bytes/event)\n")  # fmt: skip

    async with httpx.AsyncClient(base_url=args.url, headers=headers, timeout=60) as client:
        # 1. sequential ingestion: the latency a single SDK exporter sees
        latencies, failures = [], 0
        run = {"run_id": new_id(IdKind.RUN), "trace_id": new_id(IdKind.TRACE)}
        wall = time.perf_counter()
        for i in range(args.batches):
            ms, status = await post_batch(client, make_events(run, i * args.batch_size + 1, args.batch_size))
            latencies.append(ms)
            failures += status != 202
        seconds = time.perf_counter() - wall
        results.append(summarize("ingest: 1 client, sequential", latencies,
                                 f"{args.batches * args.batch_size / seconds:,.0f} ev/s, non-202={failures}"))  # fmt: skip

        # 2. concurrent ingestion: several agents at once, each with its own run
        async def one_client(batches: int) -> tuple[list[float], int]:
            own = {"run_id": new_id(IdKind.RUN), "trace_id": new_id(IdKind.TRACE)}
            out, bad = [], 0
            for i in range(batches):
                ms, status = await post_batch(client, make_events(own, i * args.batch_size + 1, args.batch_size))
                out.append(ms)
                bad += status != 202
            return out, bad

        for clients in (4, 8):
            per = max(1, args.batches // clients // 2)
            wall = time.perf_counter()
            outcomes = await asyncio.gather(*[one_client(per) for _ in range(clients)])
            seconds = time.perf_counter() - wall
            flat = [ms for out, _ in outcomes for ms in out]
            bad = sum(b for _, b in outcomes)
            results.append(summarize(f"ingest: {clients} clients, concurrent", flat,
                                     f"{len(flat) * args.batch_size / seconds:,.0f} ev/s, non-202={bad}"))  # fmt: skip

        # 3. reads on a run with 2,000 events
        big = {"run_id": new_id(IdKind.RUN), "trace_id": new_id(IdKind.TRACE)}
        for i in range(20):
            await post_batch(client, make_events(big, i * 100 + 1, 100))
        fresh_2k = await wait_current(client, big["run_id"])
        run_path = f"/v1/runs/{big['run_id']}"
        results.append(summarize("GET run (2,000 events)", [await timed_get(client, run_path) for _ in range(100)]))
        results.append(summarize("GET events page (limit 500)", [await timed_get(client, f"{run_path}/events?limit=500") for _ in range(100)]))  # fmt: skip
        results.append(summarize("GET spans (limit 500)", [await timed_get(client, f"{run_path}/spans?limit=500") for _ in range(100)]))  # fmt: skip
        results.append(summarize("GET runs list (limit 50)", [await timed_get(client, "/v1/runs?limit=50") for _ in range(100)]))  # fmt: skip

        # 4. freshness: ack -> summary current, for a growing run (the worker recomputes in full)
        for events in (2000, 10000):
            run = {"run_id": new_id(IdKind.RUN), "trace_id": new_id(IdKind.TRACE)}
            for i in range(events // 100):
                await post_batch(client, make_events(run, i * 100 + 1, 100))
            ms = await wait_current(client, run["run_id"])
            print(f"summary freshness after the last ack, {events:>6,} events: {ms:,.0f} ms (includes up to 500 ms poll)")
            results.append({"case": f"freshness {events} events", "ms": round(ms, 1)})
        print(f"(first 2,000-event run was current {fresh_2k:,.0f} ms after its last ack)")
    if args.json:
        args.json.write_text(json.dumps(results, indent=2))
    print(f"\nrun id prefix for cleanup reference: bench-{uuid.uuid4().hex[:6]}")


if __name__ == "__main__":
    asyncio.run(main())

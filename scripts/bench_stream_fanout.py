"""Fan-out benchmark: many viewers of one hot run while it keeps ingesting (docs/benchmarks/phase-5-streaming.md).

Env: BENCH_API_URL, BENCH_WRITE_KEY, BENCH_READ_KEY; optional BENCH_PRELOAD (5000), BENCH_STREAMS (50),
BENCH_LIVE (200 events), BENCH_RATE (20 events/s). Run through `STREAM_E2E_MODE=bench scripts/stream-e2e.sh`
(the API there allows 100 streams per key). Each viewer resumes after the preloaded history, like a browser
that has just loaded a run page, then watches the live events.
Run with: uv run --project apps/api python scripts/bench_stream_fanout.py
"""

import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone

import httpx
from abb_event_schema.ids import IdKind, new_id

API = os.environ["BENCH_API_URL"]
WRITE = {"Authorization": f"Bearer {os.environ['BENCH_WRITE_KEY']}"}
READ = {"Authorization": f"Bearer {os.environ['BENCH_READ_KEY']}"}
PRELOAD = int(os.environ.get("BENCH_PRELOAD", "5000"))
STREAMS = int(os.environ.get("BENCH_STREAMS", "50"))
LIVE = int(os.environ.get("BENCH_LIVE", "200"))
RATE = float(os.environ.get("BENCH_RATE", "20"))

RUN, TRACE = new_id(IdKind.RUN), new_id(IdKind.TRACE)
T0 = datetime.now(timezone.utc)
counter = 0


def event(kind: str = "tool.call.completed") -> dict:
    global counter
    counter += 1
    return {
        "schema_version": "1.0",
        "event_id": new_id(IdKind.EVENT),
        "run_id": RUN,
        "trace_id": TRACE,
        "span_id": new_id(IdKind.SPAN),
        "agent_id": "bench",
        "event_type": kind,
        "sequence": counter,
        "occurred_at": (T0 + timedelta(milliseconds=counter)).isoformat().replace("+00:00", "Z"),
        "attributes": {"tool.name": f"step_{counter}"},
        "status": "success",
    }


def pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(p * len(ordered) + 0.999999) - 1))]


async def post(client: httpx.AsyncClient, events: list[dict]) -> float:
    started = time.perf_counter()
    response = await client.post(f"{API}/v1/events/batch", json={"events": events}, headers=WRITE)
    response.raise_for_status()
    return (time.perf_counter() - started) * 1000


async def main() -> None:
    limits = httpx.Limits(max_connections=STREAMS + 10)
    async with httpx.AsyncClient(timeout=60, limits=limits) as client:
        await post(client, [event("run.started")])
        for start in range(0, PRELOAD, 500):
            await post(client, [event() for _ in range(min(500, PRELOAD - start))])
        newest = event()["event_id"]
        counter_before = counter
        await post(client, [{**event(), "event_id": newest}])  # the history's last event
        acked: dict[str, float] = {}
        seen: list[dict[str, float]] = [{} for _ in range(STREAMS)]
        errors: list[str] = []
        ready = asyncio.Event()
        finished = asyncio.Event()  # all live events have been ingested
        last_live = [""]
        connected = 0

        async def viewer(i: int) -> None:
            nonlocal connected
            url = f"{API}/v1/runs/{RUN}/stream"
            async with client.stream(
                "GET", url, params={"last_event_id": newest}, headers=READ
            ) as response:
                if response.status_code != 200:
                    errors.append(f"viewer {i}: HTTP {response.status_code}")
                    return
                connected += 1
                if connected == STREAMS:
                    ready.set()
                name = ""
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        name = line[6:].strip()
                    elif line.startswith("data:") and name == "error":
                        errors.append(f"viewer {i}: error frame")
                    elif line.startswith("id:"):
                        seen[i][line[3:].strip()] = time.time() * 1000
                    if line.startswith("id:") and finished.is_set() and last_live[0] in seen[i]:
                        return

        tasks = [asyncio.create_task(viewer(i)) for i in range(STREAMS)]
        try:
            await asyncio.wait_for(ready.wait(), 60)
        except TimeoutError:
            errors.append("viewers did not all connect")
        # Every viewer first replays the 30 s overlap window (here: the whole history). Probe ingestion meanwhile.
        replay_started = time.perf_counter()
        replay_ingest_ms: list[float] = []
        while not all(newest in v for v in seen) and time.perf_counter() - replay_started < 180:
            replay_ingest_ms.append(await post(client, [event()]))
            await asyncio.sleep(0.2)
        replay_s = time.perf_counter() - replay_started
        ingest_ms: list[float] = []
        for _ in range(LIVE):
            e = event()
            ingest_ms.append(await post(client, [e]))
            acked[e["event_id"]] = time.time() * 1000
            last_live[0] = e["event_id"]
            await asyncio.sleep(1 / RATE)
        finished.set()
        done, pending = await asyncio.wait(tasks, timeout=30)
        for t in pending:
            t.cancel()
        latencies = [
            seen[i][eid] - at for i in range(STREAMS) for eid, at in acked.items() if eid in seen[i]
        ]
        missing = sum(1 for i in range(STREAMS) for eid in acked if eid not in seen[i])
        result = {
            "viewers": STREAMS,
            "history_events": counter_before + 1,
            "live_events": LIVE,
            "replay_seconds": round(replay_s, 2),
            "ingest_during_replay_ms": {
                "p50": pct(replay_ingest_ms, 0.5),
                "p95": pct(replay_ingest_ms, 0.95),
                "max": max(replay_ingest_ms),
            }
            if replay_ingest_ms
            else None,
            "ingest_ms": {"p50": pct(ingest_ms, 0.5), "p95": pct(ingest_ms, 0.95), "max": max(ingest_ms)},
            "delivery_ms": {"p50": pct(latencies, 0.5), "p95": pct(latencies, 0.95), "max": max(latencies)}
            if latencies
            else None,
            "missing_deliveries": missing,
            "viewer_errors": errors[:5] + ([f"... {len(errors)} total"] if len(errors) > 5 else []),
        }
        print("STREAM FANOUT", json.dumps(result))  # noqa: T201
        assert not errors and missing == 0, result


asyncio.run(main())

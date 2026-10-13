"""Ingestion latency and detection time for Phase 13 gate 7 (docs/benchmarks/phase-13-security.md).

    uv run --project apps/api python scripts/security_bench.py --api URL --key-file FILE --read-key-file FILE \\
        [--batches 40] [--run-events 10000] [--assert]

Two measurements through the real HTTP API, against a project that captures FULL payloads:

1. `--batches` sequential POSTs of 100 events with an inline payload of about 1 KB each (latency of each request).
2. One run of `--run-events` events (a mix of shell commands, file reads and tool calls), posted in batches of 100: the
   time to ingest it and the time from the last acknowledgement until the run's summary and findings are `current`.

Prints one JSON line. With `--assert`, exits 1 when a bound is exceeded. The bounds are generous (shared CI runners are
slow and noisy); the measured figures and the comparison with `main` are in the benchmark note. The script needs only
HTTP and a key, so it also measures a server without Phase 13 (the comparison run).
"""

import argparse
import json
import statistics
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from abb_event_schema.ids import IdKind, new_id

# Bounds asserted with --assert: about 8 times the figures measured on a laptop (see the note), so that a slow shared runner
# passes and a real regression (a scan that costs milliseconds per event) does not.
BOUNDS = {"batch_p50_ms": 250.0, "batch_p95_ms": 600.0, "run_ingest_seconds": 60.0, "run_current_seconds": 60.0}

PROSE = (
    "The quick brown fox jumps over the lazy dog while the build agent reads the output of the unit tests and "
    "decides what to change next. "
)


def payload_1kb(n: int) -> dict[str, Any]:
    text = (f"{n} " + PROSE * 8)[:1000]
    return {"stdout": text}


class Writer:
    def __init__(self, api: str, key: str) -> None:
        self.client = httpx.Client(base_url=api, headers={"Authorization": f"Bearer {key}"}, timeout=120)

    def post(self, events: list[dict[str, Any]]) -> float:
        started = time.perf_counter()
        response = self.client.post("/v1/events/batch", json={"events": events})
        elapsed = (time.perf_counter() - started) * 1000
        if response.status_code not in (200, 202):
            raise SystemExit(f"ingest failed: {response.status_code} {response.text[:200]}")
        body = response.json()
        if body["accepted"] != len(events) or body["rejected"]:
            raise SystemExit(f"ingest rejected events: {body}")
        return elapsed


def event(run: str, trace: str, n: int, kind: str, when: datetime, **extra: Any) -> dict[str, Any]:
    attrs: dict[str, Any]
    if kind == "shell":
        attrs = {"shell.command": f"python scripts/step_{n}.py --input data/{n % 97}.json", "shell.exit_code": 0, "shell.risk_class": "R1"}
        event_type = "shell.command.completed"
    elif kind == "file":
        attrs = {"file.path": f"src/module_{n % 211}/handler_{n}.py", "file.operation": "read"}
        event_type = "file.read"
    else:
        attrs = {"tool.name": f"tool_{n % 7}"}
        event_type = "tool.call.completed"
    body = {
        "schema_version": "1.0",
        "event_id": new_id(IdKind.EVENT),
        "run_id": run,
        "trace_id": trace,
        "span_id": new_id(IdKind.SPAN),
        "agent_id": "bench-agent",
        "event_type": event_type,
        "sequence": n,
        "occurred_at": when.isoformat().replace("+00:00", "Z"),
        "attributes": attrs,
    }
    body.update(extra)
    return body


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def measure_batches(writer: Writer, batches: int) -> dict[str, float]:
    latencies: list[float] = []
    n = 0
    base = datetime.now(UTC) - timedelta(minutes=30)
    for _ in range(batches):
        run, trace = new_id(IdKind.RUN), new_id(IdKind.TRACE)  # one run per batch: no summary pile-up on a single run
        events = []
        for i in range(100):
            n += 1
            events.append(event(run, trace, i + 1, "shell" if i % 3 == 0 else "tool", base + timedelta(milliseconds=n), payload=payload_1kb(n)))
        latencies.append(writer.post(events))
    return {
        "batches": batches,
        "events": batches * 100,
        "p50_ms": round(statistics.median(latencies), 1),
        "p95_ms": round(percentile(latencies, 0.95), 1),
        "max_ms": round(max(latencies), 1),
        "mean_ms": round(statistics.fmean(latencies), 1),
    }


def measure_run(writer: Writer, reader: httpx.Client, total: int) -> dict[str, float]:
    run, trace = new_id(IdKind.RUN), new_id(IdKind.TRACE)
    base = datetime.now(UTC) - timedelta(minutes=30)
    started = time.perf_counter()
    last_ack = started
    for first in range(0, total, 100):
        events = [
            event(run, trace, n + 1, ("shell", "file", "tool")[n % 3], base + timedelta(milliseconds=n), **({"payload": payload_1kb(n)} if n % 2 == 0 else {}))
            for n in range(first, min(first + 100, total))
        ]
        writer.post(events)
        last_ack = time.perf_counter()
    ingest = last_ack - started
    deadline = time.perf_counter() + 300
    while time.perf_counter() < deadline:
        r = reader.get(f"/v1/runs/{run}")
        f = reader.get(f"/v1/runs/{run}/findings")
        if r.status_code == 200 and f.status_code == 200 and r.json().get("summary_state") == "current" and f.json()["state"]["status"] == "current":
            break
        time.sleep(0.25)
    else:
        raise SystemExit("the run never became current")
    current = time.perf_counter() - last_ack
    return {"events": total, "run_ingest_seconds": round(ingest, 1), "run_current_seconds": round(current, 1), "events_per_second": round(total / ingest)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--read-key-file", type=Path, required=True)
    parser.add_argument("--batches", type=int, default=40)
    parser.add_argument("--run-events", type=int, default=10_000)
    parser.add_argument("--label", default="branch")
    parser.add_argument("--assert", dest="check", action="store_true")
    args = parser.parse_args()
    writer = Writer(args.api, args.key_file.read_text().strip())
    reader = httpx.Client(base_url=args.api, headers={"Authorization": f"Bearer {args.read_key_file.read_text().strip()}"}, timeout=60)
    writer.post([event(new_id(IdKind.RUN), new_id(IdKind.TRACE), 1, "tool", datetime.now(UTC), payload=payload_1kb(0))])  # warm up
    result = {"label": args.label, "batch100": measure_batches(writer, args.batches), "run": measure_run(writer, reader, args.run_events)}
    print(json.dumps(result, sort_keys=True))
    if args.check:
        measured = {
            "batch_p50_ms": result["batch100"]["p50_ms"],
            "batch_p95_ms": result["batch100"]["p95_ms"],
            "run_ingest_seconds": result["run"]["run_ingest_seconds"],
            "run_current_seconds": result["run"]["run_current_seconds"],
        }
        over = {k: (measured[k], BOUNDS[k]) for k in BOUNDS if measured[k] > BOUNDS[k]}
        if over:
            print(f"SECURITY-BENCH FAILED: over the bound (measured, bound): {over}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

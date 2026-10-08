"""Analytics endpoint latency against a running API (spec §61.2: p95 dashboard aggregate < 1.5 s).

    BENCH_API_URL=http://localhost:8150 BENCH_KEY=abb_live_... python scripts/bench_analytics.py

Each endpoint is requested `--rounds` times per window, with the project filter alternating between the whole
workspace and single projects (a dashboard is opened both ways). The first request of each distinct query is
reported separately as "cold" (cache-cold pages); the percentiles cover all requests. Numbers depend on the
machine; record the environment next to them (docs/benchmarks/phase-7-analytics.md).
"""

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

ENDPOINTS = ("summary", "cost", "reliability", "performance")


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=15)
    parser.add_argument("--end", help="exclusive end (default: the next UTC midnight)")
    parser.add_argument("--days", type=int, nargs="+", default=[1, 7])
    parser.add_argument("--projects", nargs="*", default=[], help="project ids to alternate with")
    parser.add_argument("--budget-ms", type=float, default=1500.0)
    parser.add_argument("--json-out")
    args = parser.parse_args()
    url, key = os.environ["BENCH_API_URL"], os.environ["BENCH_KEY"]
    now = datetime.now(timezone.utc)
    end = (
        datetime.fromisoformat(args.end).astimezone(timezone.utc)
        if args.end
        else datetime(now.year, now.month, now.day, tzinfo=timezone.utc) + timedelta(days=1)
    )
    client = httpx.Client(base_url=url, headers={"authorization": f"Bearer {key}"}, timeout=60)
    results: dict[str, dict[str, float]] = {}
    failed = False
    for days in args.days:
        for endpoint in ENDPOINTS:
            samples: list[float] = []
            cold: list[float] = []
            filters: list[str | None] = [None, *args.projects]
            for project in filters:
                params = {
                    "from": (end - timedelta(days=days)).isoformat(),  # whole UTC days: today is the last
                    "to": end.isoformat(),
                }
                if project:
                    params["project_id"] = project
                for round_ in range(args.rounds):
                    started = time.perf_counter()
                    response = client.get(f"/v1/analytics/{endpoint}", params=params)
                    elapsed = (time.perf_counter() - started) * 1000
                    if response.status_code != 200:
                        print(f"FAILED {endpoint} {days}d: {response.status_code} {response.text[:200]}")
                        failed = True
                        break
                    (cold if round_ == 0 else samples).append(elapsed)
            if not samples:
                continue
            label = f"{endpoint} window={days}d"
            results[label] = {
                "n": len(samples),
                "cold_ms": round(statistics.mean(cold), 1) if cold else 0,
                "p50_ms": round(percentile(samples, 0.5), 1),
                "p95_ms": round(percentile(samples, 0.95), 1),
                "max_ms": round(max(samples), 1),
            }
            print(f"{label:34} n={len(samples):3} cold={results[label]['cold_ms']:8.1f} "
                  f"p50={results[label]['p50_ms']:8.1f} p95={results[label]['p95_ms']:8.1f} "
                  f"max={results[label]['max_ms']:8.1f} ms")
    over = [k for k, v in results.items() if v["p95_ms"] > args.budget_ms]
    print("OVER BUDGET:" if over else "within budget", ", ".join(over))
    if args.json_out:
        with open(args.json_out, "w") as handle:
            json.dump(results, handle, indent=2)
    return 1 if failed or over else 0


if __name__ == "__main__":
    sys.exit(main())

"""Analytics endpoint latency against a running API (spec §61.2: p95 dashboard aggregate < 1.5 s).

    BENCH_API_URL=http://localhost:8150 BENCH_KEY=abb_live_... python scripts/bench_analytics.py

Each query is warmed up (`--warmup` discarded requests), then measured `--rounds` times, and the whole pass is repeated
`--repeats` times; every repeat is reported, and the budget is checked against the worst repeat. The very first request
of each query is reported separately. Numbers depend on the
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
    parser.add_argument("--rounds", type=int, default=20, help="measured requests per query per repeat")
    parser.add_argument("--warmup", type=int, default=3, help="discarded requests per query before measuring")
    parser.add_argument("--repeats", type=int, default=3, help="whole measurement passes; each is reported")
    parser.add_argument("--end", help="exclusive end (default: the next UTC midnight)")
    parser.add_argument("--days", type=int, nargs="+", default=[1, 7])
    parser.add_argument("--projects", nargs="*", default=[], help="project ids to measure as well")
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
    queries = []  # (label, endpoint, params)
    for days in args.days:
        for endpoint in ENDPOINTS:
            for project in [None, *args.projects]:
                params = {"from": (end - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                          "to": end.strftime("%Y-%m-%dT%H:%M:%SZ")}  # fmt: skip
                if project:
                    params["project_id"] = project
                label = f"{endpoint} {days}d " + (project[-6:] if project else "workspace")
                queries.append((label, endpoint, params))
    results: dict[str, dict[str, object]] = {}
    failed = False
    first_seen: dict[str, float] = {}
    for repeat in range(args.repeats):
        for label, endpoint, params in queries:
            for i in range(args.warmup + args.rounds):
                started = time.perf_counter()
                response = client.get(f"/v1/analytics/{endpoint}", params=params)
                elapsed = (time.perf_counter() - started) * 1000
                if response.status_code != 200:
                    print(f"FAILED {label}: {response.status_code} {response.text[:200]}")
                    failed = True
                    break
                if repeat == 0 and i == 0:
                    first_seen[label] = elapsed
                if i >= args.warmup:
                    results.setdefault(label, {"repeats": []})  # type: ignore[union-attr]
                    bucket = results[label]["repeats"]  # type: ignore[index]
                    if len(bucket) <= repeat:  # type: ignore[arg-type]
                        bucket.append([])  # type: ignore[attr-defined]
                    bucket[repeat].append(elapsed)  # type: ignore[index]
    summary: dict[str, dict[str, object]] = {}
    over = []
    for label, data in results.items():
        runs = data["repeats"]  # type: ignore[assignment]
        per_repeat = [
            {"p50_ms": round(percentile(r, 0.5), 1), "p95_ms": round(percentile(r, 0.95), 1),
             "max_ms": round(max(r), 1), "n": len(r)}
            for r in runs  # type: ignore[attr-defined]
        ]  # fmt: skip
        worst = max(p["p95_ms"] for p in per_repeat)
        summary[label] = {"first_request_ms": round(first_seen[label], 1), "repeats": per_repeat,
                          "worst_p95_ms": worst}  # fmt: skip
        flag = "  OVER" if worst > args.budget_ms else ""
        print(f"{label:34} first={first_seen[label]:7.1f}  "
              + "  ".join(f"[p50 {p['p50_ms']:6.1f} p95 {p['p95_ms']:6.1f} max {p['max_ms']:6.1f}]" for p in per_repeat)
              + flag)  # fmt: skip
        if worst > args.budget_ms:
            over.append(label)
    print("OVER BUDGET:" if over else "within budget", ", ".join(over))
    if args.json_out:
        with open(args.json_out, "w") as handle:
            json.dump({"environment_note": "see docs/benchmarks/phase-7-analytics.md", "results": summary}, handle, indent=2)
    return 1 if failed or over else 0


if __name__ == "__main__":
    sys.exit(main())

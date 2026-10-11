"""Alignment and comparison cost on the hard inputs (Phase 11, AC-7). No database needed.

    cd apps/api
    uv run python ../../scripts/bench_compare.py [--repeat 5]

Times the pure `align` on 2,000-step pairs (disjoint keys, shifted keys, two seeded random sequences
over 3 keys, alternating 3 tools with one inserted step), on 20,000-step random pairs (the
ABB_COMPARE_MAX_STEPS=20000 mutation), and the whole `compare_runs` on two 20,000-event runs (the
event cap, truncated to the step cap) in both projections. Prints one markdown row per case. Numbers
depend on the machine; they are a regression baseline, not a capacity claim.
"""

import argparse
import platform
import random
import statistics
import sys
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from abb_event_schema.event import finalize
from abb_event_schema.ids import IdKind, new_id
from abb_event_schema.parse import parse_event_in

from abb_api.comparison.align import align
from abb_api.comparison.compare import SideInput, compare_runs

MAX_EDITS = 600
START = datetime(2026, 10, 12, 9, 0, 0, tzinfo=UTC)


def cases() -> dict[str, tuple[list[Any], list[Any], int]]:
    n = 2_000
    rng = random.Random(7)
    alternating = ["a", "b", "c"] * (n // 3)
    big = random.Random(3)
    one, two = random.Random(1), random.Random(2)
    return {
        "disjoint keys, 2,000": ([("k", i) for i in range(n)], [("j", i) for i in range(n)], MAX_EDITS),
        "shifted by one, 2,000": (
            [("k", i) for i in range(n)], [("k", i) for i in range(1, n + 1)], MAX_EDITS),
        "3-key random, 2,000": (
            [rng.choice("abc") for _ in range(n)], [rng.choice("abc") for _ in range(n)], MAX_EDITS),
        "alternating 3 tools + 1 insertion, 2,000": (
            alternating, [*alternating[:1000], "z", *alternating[1000:]], MAX_EDITS),
        "3-key random, 20,000 (steps cap raised)": (
            [big.choice("abc") for _ in range(20_000)], [big.choice("abc") for _ in range(20_000)],
            MAX_EDITS),
        "3-key random, 2,000, max_edits 4,000 (work cap)": (
            [one.choice("abc") for _ in range(n)], [two.choice("abc") for _ in range(n)], 4_000),
    }  # fmt: skip


def run_events(count: int, seed: int) -> list[Any]:
    rng = random.Random(seed)
    run, trace = new_id(IdKind.RUN), new_id(IdKind.TRACE)
    ws, project = new_id(IdKind.WORKSPACE), new_id(IdKind.PROJECT)
    events = []
    for n in range(1, count + 1):
        wire = {
            "schema_version": "1.0", "event_id": new_id(IdKind.EVENT), "run_id": run,
            "trace_id": trace, "span_id": new_id(IdKind.SPAN), "agent_id": "bench-agent",
            "event_type": "tool.call.completed", "status": "success", "sequence": n,
            "occurred_at": (START + timedelta(seconds=n)).isoformat().replace("+00:00", "Z"),
            "attributes": {"tool.name": f"tool-{rng.randrange(3)}"},
        }  # fmt: skip
        events.append(finalize(parse_event_in(wire), workspace_id=ws, project_id=project,
                               received_at=START))  # fmt: skip
    return events


def best(fn: Any, repeat: int) -> tuple[float, Any]:
    times, result = [], None
    for _ in range(repeat):
        start = time.perf_counter()
        result = fn()
        times.append(time.perf_counter() - start)
    return statistics.median(times), result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=5)
    args = parser.parse_args()
    print(f"Python {platform.python_version()} on {platform.platform()}, repeat {args.repeat}\n")
    print("| case | median ms | quality |\n| --- | ---: | --- |")
    for name, (a, b, edits) in cases().items():
        seconds, result = best(lambda a=a, b=b, edits=edits: align(a, b, max_edits=edits), args.repeat)
        print(f"| {name} | {seconds * 1000:.1f} | {result.quality} |")
    left, right = run_events(20_000, 1), run_events(20_000, 2)
    for content in (True, False):
        sides = [
            SideInput("run_x", "SUCCESS", START, {"event_count": 20_000}, events, 20_000)
            for events in (left, right)
        ]
        seconds, result = best(
            lambda s=sides, c=content: compare_runs(s[0], s[1], content=c, max_steps=2_000,
                                                    max_edits=MAX_EDITS), args.repeat)  # fmt: skip
        label = "content" if content else "metadata"
        print(f"| compare_runs, two 20,000-event runs, {label} | {seconds * 1000:.1f} | "
              f"{result.quality}, {result.left.steps} steps/side, truncated={result.left.truncated} |")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())

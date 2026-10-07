"""Application-thread overhead of the SDK (spec §156: target < 1 ms typical).

Measures what the *caller* pays per operation, with a real exporter thread shipping events to a
local stub server so thread contention is included. Run: `uv run python bench.py`.
"""

import statistics
import sys
import time

from blackbox import BlackBox, PayloadMode
from tests.helpers import start_stub, stop_stub

N = 20_000


def measure(label: str, fn, n: int = N) -> None:  # type: ignore[no-untyped-def]
    samples = []
    for _ in range(n):
        t0 = time.perf_counter_ns()
        fn()
        samples.append(time.perf_counter_ns() - t0)
    samples.sort()
    us = [s / 1000 for s in samples]
    print(
        f"{label:<46} mean {statistics.fmean(us):7.1f} us   p50 {us[n // 2]:7.1f}   "
        f"p99 {us[int(n * 0.99)]:7.1f}   max {us[-1]:8.1f}"
    )


def main() -> None:
    stub = start_stub()
    bb = BlackBox(api_key="k", endpoint=stub.url, max_queue=200_000)
    full = BlackBox(
        api_key="k", endpoint=stub.url, payload_mode=PayloadMode.FULL, max_queue=200_000
    )
    print(f"python {sys.version.split()[0]}, {N} iterations each (1 run, exporter thread active)")
    with bb.run("bench") as run:
        measure(
            "event() with 3 attributes",
            lambda: run.event("custom.tick", {"a": 1, "b": "x", "c": True}),
        )

        def span() -> None:
            with run.span("step", kind="tool"):
                pass

        measure("span() enter+exit (2 events)", span)

        def llm() -> None:
            with run.llm_call("openai", "gpt-4.1") as call:
                call.record_usage(100, 50)

        measure("llm_call() enter+exit (2 events)", llm)
        measure(
            "event() with string needing secret scan",
            lambda: run.event(
                "custom.cmd", {"shell.command": "git commit -m 'fix: parser' --author=a@b.c"}
            ),
        )
    with full.run("bench-full") as run2:
        payload = {"messages": [{"role": "user", "content": "hello " * 50}] * 4}
        measure(
            "event() with 4 KB payload (FULL mode)",
            lambda: run2.event("custom.p", payload=payload),
            5000,
        )
    bb.shutdown(10)
    full.shutdown(10)
    print("stats:", {k: v for k, v in bb.stats().items() if v})
    stop_stub(stub)


if __name__ == "__main__":
    main()

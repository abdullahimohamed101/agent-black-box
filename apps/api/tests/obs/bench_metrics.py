"""Informational timings for docs/benchmarks/phase-19c-metrics.md (not a test, no thresholds).

Run: `uv run python -m tests.obs.bench_metrics` from apps/api.
"""

import asyncio
import platform
import statistics
import time

import httpx

from abb_api.core.config import Settings
from abb_api.main import create_app
from abb_api.obs.catalogue import build_observability
from abb_api.obs.metrics import Registry, capped


def per_call_ns(fn, n: int = 200_000) -> float:  # type: ignore[no-untyped-def]
    best = float("inf")
    for _ in range(5):
        start = time.perf_counter_ns()
        for _ in range(n):
            fn()
        best = min(best, (time.perf_counter_ns() - start) / n)
    return best


async def healthz_rps(with_metrics: bool, n: int = 3000) -> float:
    obs = build_observability()
    settings = Settings(database_url="postgresql+asyncpg://x/none", environment="test")
    app = create_app(settings, observability=obs)
    if not with_metrics:  # drop the outermost layer: rebuild the stack without our middleware
        app.user_middleware = [
            m for m in app.user_middleware if getattr(m.cls, "__name__", "") != "MetricsMiddleware"
        ]
        app.middleware_stack = None
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        for _ in range(200):
            await client.get("/healthz")
        runs = []
        for _ in range(5):
            start = time.perf_counter()
            for _ in range(n):
                await client.get("/healthz")
            runs.append(n / (time.perf_counter() - start))
    return statistics.median(runs)


def main() -> None:
    import logging

    logging.disable(logging.CRITICAL)
    registry = Registry()
    counter = registry.counter("a_total", "A.", [capped("r", 10)])
    hist = registry.histogram("b_seconds", "B.", [capped("r", 10)])
    print(
        f"machine: {platform.platform()} {platform.processor()} python {platform.python_version()}"
    )
    print(f"counter.inc (1 label):      {per_call_ns(lambda: counter.inc('x')):8.0f} ns")
    print(f"histogram.observe (1 label): {per_call_ns(lambda: hist.observe(0.01, 'x')):8.0f} ns")
    with_m = asyncio.run(healthz_rps(True))
    without = asyncio.run(healthz_rps(False))
    print(f"/healthz in-process, with metrics:    {with_m:8.0f} req/s")
    print(f"/healthz in-process, without metrics: {without:8.0f} req/s")
    print(
        f"difference: {(1 - with_m / without) * 100:.1f}% "
        f"({(1 / with_m - 1 / without) * 1e6:.1f} us/request)"
    )


if __name__ == "__main__":
    main()

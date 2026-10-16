# Phase 19c: metrics overhead (informational)

Recorded 2026-10-16, step 1 (registry, HTTP middleware). **These numbers are informational and are not asserted by any test**: the tests assert deterministic facts (lock acquisitions per operation and per request, series counts; `apps/api/tests/obs/test_overhead.py`), because wall-clock thresholds failed in CI in Phase 19a.

Machine: Apple M5 Pro, 15 logical CPUs, macOS 26.6 (arm64), CPython 3.12.15, one process. Load average 4.5 to 4.6 (other builders running tests at the same time; the Docker VM was busy), so absolute figures are pessimistic and noisy.

Reproduce: `cd apps/api && uv run python -m tests.obs.bench_metrics`.

| Measurement | Result |
| --- | --- |
| `Counter.inc` with one capped label (existing series, best of 5 x 200k) | 243 ns |
| `Histogram.observe` with one capped label (existing series) | 288 ns |
| In-process `GET /healthz` through the full ASGI stack with the metrics middleware | about 5000 req/s (median of 5 runs of 3000) |
| The same without the middleware | about 5200 req/s |
| Difference | 3.3 percent, 6.5 us per request |

Notes.

- A request takes four lock acquisitions (in-flight up and down, one counter, one histogram); `test_overhead.py` pins that number.
- Before the fast path in `Metric._resolve` (a stored label tuple is admitted by construction, so it skips validation), the same run measured 565 ns and 613 ns per call and 17 us per request.
- `/healthz` is the cheapest route, so the percentage is the worst case; a real ingestion request spends milliseconds in the database. The plan's gate 8 (ingestion overhead against a stated bound) is measured end to end in step 6 against the real stack.
- A scrape (`GET /metrics`) renders the exposition on the event loop; its cost grows with the series count, bounded by the per-metric ceilings (KI-223).

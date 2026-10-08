# Phase 5: live streaming latency

Target (plan, spec §76): p95 accept-to-display under 1 s. **Measured: p95 about 9 ms** on one machine, no network between the parts.

## What is measured
`scripts/stream-e2e.sh`, test "accept-to-display latency": a driver posts 60 single events over HTTP, 200 ms apart, to an API that a
real Chrome is watching through the built web server's proxy. For each event:

- **start** = the moment the API's `202` response reached the driver (`time.time()`, epoch ms);
- **end** = the first moment the run page's progress line (`N of M events shown`) counted that event (a `MutationObserver`, `Date.now()`).

So it covers: Postgres commit and `NOTIFY`, the listener wake-up, the stream's page query, SSE framing, the web proxy relay, the
browser's `EventSource`, the animation-frame batch, the merge and React render. It does **not** cover the SDK's own batching delay
(a flush interval before events leave the process), the summary worker (about 1 s, only affects the header figures) or any real network.
The start point is the response to the writer, which the stream can beat (the commit happens before the response is sent), so
latencies are a conservative-looking lower bound of "commit to display" and a fair one for "accepted to display".

## Result (three runs, 60 events each)
| run | p50 | p95 | max |
| --- | --- | --- | --- |
| 1 | 6.5 ms | 8.9 ms | 20.3 ms |
| 2 | 7.1 ms | 9.2 ms | 14.4 ms |
| 3 | 6.8 ms | 8.7 ms | 15.3 ms |

Environment: Apple M5 Pro (arm64, 15 cores), macOS, local Postgres 16 in Colima on port 5433, API (uvicorn, one process) and worker
on the host, Next.js 16 production build, system Chrome driven by Playwright. Fallback poll 2 s, so these are all `NOTIFY` wake-ups.

## Not measured (and why it matters)
- **One writer, one viewer, an idle machine.** Fan-out cost grows with streams per run: each wake-up is one indexed query per stream
  (ADR-022). The measured trigger for Redis/NATS is not reached; measure again with many viewers of one hot run before Phase 18.
- **Real network latency** adds one round trip on top (browser to web, web to API).
- **The fallback path** (listener down) is bounded by the 2 s poll, tested for correctness (`tests/test_stream_hub.py`), not timed here.
- **CI hardware** (2 vCPU) will be slower; the test fails above 1 s, not above 9 ms.

Raw output of one run: `STREAM LATENCY {"events":60,"p50_ms":6.47,"p95_ms":8.86,"max_ms":20.28,"min_ms":3.32}`.
Reproduce: `make stream-e2e` (writes `apps/web/test-results/stream-latency.json`).

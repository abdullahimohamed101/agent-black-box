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

## Hot run: many viewers while the run keeps ingesting
`STREAM_E2E_MODE=bench scripts/stream-e2e.sh` (`scripts/bench_stream_fanout.py`): a run with 5,003 events, then 50 viewers that each resume
after the history (so each replays the 30 s overlap window, here the whole history), while the writer keeps posting 200 events at 20/s.
Same machine and stack as above, one API process (default settings: poll floor 0.1 s, 4 concurrent stream queries, window check 2 s).

| | 1 viewer (baseline) | 50 viewers |
| --- | --- | --- |
| time for every viewer to finish its replay | 0.5 s | 17-18 s (three runs: 16.8, 17.4, 17.8) |
| ingest latency while viewers replay (p50 / p95) | 49 / 55 ms | about 200 / 600-710 ms |
| ingest latency afterwards, steady state (p50 / p95) | 13 / 498 ms | 16-17 / 470-540 ms |
| live delivery latency per viewer (p50 / p95 / max) | 45 / 94 / 120 ms | 50-58 / 98-129 / 209-268 ms |
| missed deliveries, viewer errors, `stream failed` logs | 0 | 0 |

Reading it: fan-out costs ingestion only during the replay storm. The steady-state p95 around 500 ms is the **same with one viewer**, so it
is not streaming: it is the summarizer recomputing a 5,000-event run and holding the run row (KI-016). Live delivery stays under 270 ms for
every viewer.

Before the fixes from verification and review (index, incremental polls, poll floor, database budget) the same scenario made the independent
verifier's streams exhaust the shared pool (`stream failed (TimeoutError)`), and replays had not finished after 90 s. The first version of this
document called the cost "one indexed query"; that was wrong for a hot run and no index existed.

## Not measured (and why it matters)
- **Beyond 50 viewers, several API processes, or runs larger than 5,000 events.** Fan-out cost grows with viewers per run (ADR-022).
  A shared fan-out (Redis/NATS) is justified only when this measurement shows the stream queries, not the summarizer, limiting ingestion.
- **Real network latency** adds one round trip on top (browser to web, web to API).
- **The fallback path** (listener down) is bounded by the 2 s poll, tested for correctness (`tests/test_stream_hub.py`), not timed here.
- **CI hardware** (2 vCPU) will be slower; the test fails above 1 s, not above 9 ms.

Raw output of one run: `STREAM LATENCY {"events":60,"p50_ms":6.47,"p95_ms":8.86,"max_ms":20.28,"min_ms":3.32}`.
Reproduce: `make stream-e2e` (writes `apps/web/test-results/stream-latency.json`).

# Phase 19a step 4: request-body bytes-in-flight cap (ADR-155, KI-026)

Tool: `scripts/bench_body_budget.py` (starts one uvicorn process of this checkout against a throwaway database `abb_p19a_edge_*`; the load
generator, the API and PostgreSQL share one machine). Machine: Apple M5 Pro, 15 cores, 24 GiB; PostgreSQL 16 in Docker on the same host;
quotas off and rate limits raised so only the cap is measured. Load: 64 concurrent clients each posting valid 1000-event batches of about
4.5 MiB (each event carries 4,300 bytes of payload, so every body is close to the 5 MiB per-request limit) for 15 s, plus 2 clients posting
one-event batches alongside. A refused client waits 250 ms before its next try, roughly what the SDK backoff does. Resident set size of the API
process is sampled every 20 ms. One round per configuration; the machine was otherwise idle, but the generator itself uses a full core, and the
latencies below are dominated by 64 clients competing for one event loop, one connection pool and the server-side secret scan, so read them as
relative. A first full run (before error codes were named in the output) gave the same shape: peak RSS 1,215 / 665 / 339 MiB.

| configuration | baseline RSS | peak RSS | 202 | 503 BUSY (the cap) | 503 DEPENDENCY_UNAVAILABLE (pool timeout) | large p50 / p95 (ms) | small batches alongside |
| --- | --- | --- | --- | --- | --- | --- | --- |
| cap off (8 GiB) | 237 MiB | **1,281 MiB** | 53 | 0 | 65 | 16,824 / 23,582 | 2 accepted, 1 pool timeout, p50 11,064 ms |
| default 64 MiB | 250 MiB | **665 MiB** | 40 | 205 | 0 | 10,613 / 11,521 | 6 accepted, 0 refused, p50 6,086 ms |
| 16 MiB | 248 MiB | **319 MiB** | 29 | 610 | 0 | 2,472 / 3,241 | 25 accepted, 0 refused, p50 1,094 ms |

Reading it:

* Without the cap 64 clients drove the process from 237 MiB to 1.28 GiB, and the overload showed up as database pool timeouts
  (`503 DEPENDENCY_UNAVAILABLE`) and 17-second medians. With the default cap the peak was 665 MiB (about half), and nothing timed out on the pool; the excess was
  shed up front as `503 BUSY` with `Retry-After`. With 16 MiB the peak was 319 MiB, only 70 MiB over the baseline.
* The growth over the baseline is 6.5 times the accounted bytes at the default (415 MiB over 64 MiB) and 4.4 times at 16 MiB (71 MiB): a body is held as the
  read buffer, its `bytes` copy and, for the work after, parsed structures. That is the multiple to size the cap with: **resident memory is roughly baseline + 4 to 6.5 x the cap
  for large ingestion bodies**. The cap bounds accounted bytes (tests assert the peak never exceeds it); it does not bound the work after a body is read.
* Small requests were never refused by the cap in any configuration (the 64 KiB allowance), and their latency fell as the cap tightened because the process was doing
  less concurrent work. The one small request that failed with the cap off failed on the exhausted database pool, not on the cap.
* Throughput of accepted large batches is not improved by the cap (it is limited by the secure ingestion path, not by memory); the cap trades refused work for a bounded footprint
  and a process that stays responsive. Clients that honour `Retry-After` recover.

The measurements above were taken with the first design (bytes counted as read); the review fix reserves the declared `Content-Length` at admission, which only makes refusals earlier (before any byte is read) and was not re-benchmarked: expect the same peak or lower and more `503 BUSY` answered before transfer. The accounted-bytes figure itself is asserted by `tests/test_body_budget.py` (a flood of 40 concurrent 3 MiB bodies against an 8 MiB cap: peak accounted bytes at most the cap, ten concurrent 3 MiB declarations against a 4 MiB cap: nine refused before any read,
back to zero afterwards, also after disconnects, cancellations, exceptions and read timeouts).

Reproduce: `cd apps/api && uv run python ../../scripts/bench_body_budget.py --admin-url postgresql+asyncpg://USER:PASS@HOST:PORT/postgres --json out.json`.

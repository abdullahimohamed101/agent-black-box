# Phase 19a step 2: failed-authentication flood (ADR-153)

Tool: `scripts/bench_auth_throttle.py` (starts the API from this checkout against a throwaway database; the load generator, the API, and
PostgreSQL share one machine). Machine: Apple M5 Pro, 15 cores, 24 GiB; PostgreSQL 16.15 in Docker on the same host; one uvicorn
process; defaults for the throttle (30 per address, 10 per key id, 600 global, 60 s window, negative cache 30 s, known-valid 300 s),
`ABB_TRUST_PROXY=1`. Each case ran 12 s, two rounds, throttle off (`ABB_AUTH_FAIL_ENABLED=false`) and on alternating. The machine was **not
quiet**: other work ran on it during the second run, which is why the same configuration varies between rounds (valid-only p95 148 to 323 ms).
Treat the numbers as a regression baseline and read ratios within a round, not absolute figures.

Database load is `pg_stat_database` commits + rollbacks per second over the case (idle was 1 to 12 transactions/s). A flood request is
`GET /v1/runs` with a well-formed but invalid key (a fresh random key id and secret every time); "one address" sends every request with
the same `X-Forwarded-For`, "spread" with thousands of different addresses and key ids. 16 concurrent connections.

## Database load during a flood of bad keys (the gate: bounded)

| case | throttle | requests/s | 401 | 429 | DB transactions/s |
| --- | --- | --- | --- | --- | --- |
| one address, round 0 | off | 472 | 5,680 | 0 | 949 |
| one address, round 0 | **on** | 1,534 | **30** | 18,386 | **24** |
| one address, round 1 | off | 469 | 5,640 | 0 | 937 |
| one address, round 1 | **on** | 1,451 | **60** | 17,355 | **28** |
| spread, round 0 | off | 466 | 5,608 | 0 | 926 |
| spread, round 0 | **on** | 1,162 | **569** | 13,380 | **304** |
| spread, round 1 | off | 468 | 5,624 | 0 | 939 |
| spread, round 1 | **on** | 1,820 | **569** | 21,289 | **303** |

Reading it: with the throttle off every request costs about two transactions (key lookup and its bookkeeping), so the database load is
proportional to the flood and the flood is limited only by the generator. With it on, the database work is a **count per window, not a rate**:
one address stopped after its 30 failures (the second round saw 60, one window boundary inside the 12 s) and then cost nothing, and a
distributed flood stopped at the global ceiling (569 to 600 lookups; the figure is 600 less requests still in flight and the 30 s
negative cache). The spread case's 300 transactions/s is that one burst of 600 failures (each: one lookup and about two counter writes,
about 6 transactions in all) spread over a 12 s run; over the 60 s window it is about 60/s and then zero. Throttled requests do **no** database
work: in an isolated 10 s run of one address (2,935 requests/s) the database saw 197 transactions in all, 31 `api_keys` scans and 63
counter writes; in a 8 s spread run 600 lookups and 1,206 counter writes for 21,083 requests. The bound per window and per process is
`auth_fail_global` lookups plus about 2 counter writes each; the sum over N processes is `auth_fail_global + N`.

## Valid ingestion during a flood

A stream of valid 100-event batches (4 clients) with a flood of bad keys running at the same time:

| round | throttle | valid-only p50 / p95 / p99 (ms) | valid + one-address flood | valid + spread flood | non-202 |
| --- | --- | --- | --- | --- | --- |
| 0 | off | 150 / 280 / 312 | 157 / 439 / 573 | 320 / 500 / 534 | 0 |
| 0 | **on** | 168 / 323 / 388 | 130 / 220 / 546 | 173 / 471 / 618 | 0 |
| 1 | off | 110 / 152 / 223 | 370 / 771 / 1005 | 164 / 572 / 720 | 0 |
| 1 | **on** | 106 / 148 / 199 | 188 / 452 / 550 | 250 / 390 / 421 | 0 |

Stated honestly: **no valid request was ever refused or failed (non-202 = 0 in every case)** and the key kept authenticating (it is in the
known-valid cache, so the throttle gate is skipped while authentication still runs). Latency is **not** unaffected on this setup: a flood at
1,000 to 1,800 requests/s competes with the valid stream for the same CPU, event loop and connection pool, and the load generator is
in the same process as the valid stream. With the throttle on, valid p95 during a flood was 1.5x to 3x its flood-free value in these
runs (220 to 470 ms against 148 to 323 ms); with it off, 1.2x to 5x, and the worst case was twice as bad (771 ms versus 452 ms). Part of the remaining
inflation is the cost of answering 429s at 1,000+/s, which a real flood would meet at the proxy (OPERATIONS.md). The figures do not support a claim
of "no effect on latency"; they support "never refused, and database load bounded".

## What the first run found

An earlier version used a refilling token bucket as the per-process fallback. Once the shared window was full the bucket refused with
"retry in 0.1 s", the gate reopened ten times a second, and each reopening let the in-flight requests do a lookup and charge the
address and key-id counters: 1,693 `api_keys` scans and 3,400 counter writes for 600 admitted failures, 10,200 transactions in 8 s.
The fallback is now a fixed window aligned to the clock like the database's, so a refusal always reports the time left in the window
(`test_a_closed_window_stays_closed_for_the_rest_of_the_window`); the table above is from the fixed version.

## Not measured

Two API processes under flood (the counters are shared and tested for exactness in `tests/test_auth_throttle.py`, but the load figures
are one process); a flood across a window boundary at scale; a real proxy.

# Phase 14 step 2: decision latency (gate 8, server side)

Measured 2026-10-13 on branch `feature/phase-14-policy-and-approvals` (the step-2 commits), Apple silicon laptop, Postgres 16 in a Colima VM
(the same stack the test suite uses), one run of `tests/approvals/test_latency.py -s` after the full suite had finished (machine otherwise idle),
repeated twice. **In-process**: the API app is called through httpx's ASGI transport, so there is no network and no uvicorn; every call still
authenticates the key, opens a transaction and commits one. These figures are a regression baseline for this repository on one laptop, not a capacity
claim. The end-to-end figures through the real SDK and HTTP server belong to step 5 (`policy-e2e.sh`).

## Decision endpoint (`POST /v1/policy/decide`), 1,000 calls per row, a 60-rule policy

| Case | p50 | p95 | p99 |
| --- | --: | --: | --: |
| allow, no rule matches (walks all 60 rules) | 17.4 - 18.3 ms | 25.5 - 27.7 ms | 27.8 - 30.5 ms |
| deny, last rule matches | 18.7 - 19.1 ms | 26.8 - 27.4 ms | 29.3 - 30.8 ms |

Where the time goes (300 calls each, same harness): evaluating the 60 rules is **5.5 microseconds**; reading the fail-mode table (key authentication plus two
queries) is 4.5 ms p50; a single-event `POST /v1/events` is 9.7 ms p50 (25.9 ms p95); a decision is 12.5 ms p50 (19.3 ms p95). A decision is therefore
"authenticate, read the policy, write one event and its outbox job in one transaction": its cost is the event write (a decision *is* a recorded event), not the
evaluation. The policy is read per call (two single-row indexed statements, newest version first) so a publish takes effect on the next decision; the compiled
form is cached by document text.

**Stated bound:** p95 under 100 ms in this harness (the test asserts it; a loaded CI runner passes, a per-call table scan or a blocked event loop does not).

## Ingestion is not regressed

Ingestion changed by one attribute comparison per event (the reserved emitter name). `tests/approvals/test_latency.py` also posts 40 batches of 100 events:

| | p50 | p95 |
| --- | --: | --: |
| ingestion alone (two runs) | 48.5 / 37.8 ms | 51.9 / 51.6 ms |
| ingestion while 4 clients decide in a loop (about 230 - 255 decisions in the window) | 37.6 / 41.4 ms | 67.0 / 69.7 ms |

Deciders slow the p95 of a concurrent batch by about 15 ms (they share the connection pool and the database), not the median; the test asserts the busy p95
stays within five times the quiet p95 (or 50 ms). Waiting for approvals holds no database connection (`tests/approvals/test_wait.py`), so parked agents do not
touch this figure. A flood is refused at the rate limit (`tests/approvals/test_flood.py`).

# Phase 14 step 5: decision and ingestion latency over real HTTP (gate 8)

Measured 2026-10-13 by `scripts/policy-e2e.sh` (stage 8, `gate8` in `scripts/policy_e2e_gates.py`) on branch `feature/phase-14-policy-and-approvals`: Apple silicon laptop,
Postgres 16 in a Colima VM on the same machine, `uvicorn` with one process for the API and one worker, a real project-bound API key, the 60-rule policy below. The
laptop was **not idle** (a browser and other agents' processes were running), which is why three runs are shown: the spread is the noise, and the machine's own
floor (the median of 30 trivial authenticated reads, which the bounds are scaled by) is printed with each. These figures are a regression baseline for this repository on
one laptop, not a capacity claim; `docs/benchmarks/phase-14-decide.md` is the in-process baseline of step 2 (no network, no uvicorn).

## Method

- **The policy**: 60 rules (`big_rules()`): 16 path denies (`**/secret-N.txt`), a `.env*` deny, 10 `deploy.tN` approvals, 15 shell allows by executable, 5 R4 shell denies, 5 tool-name denies, a tool
  deny for a name with unsafe characters, attribute rules (a push to `main` or `production`, a deleted file), an R3 shell approval, `src/*.py` allow, a database approval, filler allows and a final `x.*` deny.
  Published for the `bench` project through the management API as an admin; `POST /v1/policies/validate` says `valid` and 60 rules.
- **Decisions**: `POST /v1/policy/decide` as an agent key (the SDK's own transport is not in this loop; a raw `httpx` client keeps the measurement about the server). Three kinds in rotation, none needing a
  person (no approval rows are created): a path read (some deny, some default allow, matching at depth 1 to 16 or walking all 60 rules), a shell command by executable (allow rules at depth 31-45), and a tool call
  (deny rules at depth 52-56 or default). Every call writes its `policy.decision` or `policy.action.blocked` event and outbox job in one transaction (a decision IS a recorded event). 10 run ids, 30 warm-up calls.
- **Sequential**: 500 calls on one `httpx.Client` (a kept-alive connection). **Four concurrent callers**: 4 threads x 250 calls (1,000 requests), each with its own client.
- **Ingestion alongside**: `scripts/security_bench.py`'s builders (the Phase 13 benchmark) post 20 batches of 100 events with a 1 KB payload each (2,000 events), first alone, then while four threads ask for decisions in a loop
  (about 125 decisions land inside the window).
- **Bounds asserted by the script** (generous: a shared CI runner): decide p50 50 ms, p95 250 ms, p99 800 ms; four callers p95 800 ms; ingestion p50 250 ms, p95 1,000 ms; each multiplied by `max(1, floor / 5 ms)`.
  Ingestion under decision load must stay within `max(3 x alone, 250 ms x scale)` at p50 and 1,500 ms x scale at p95.

## Results (three runs, milliseconds)

| | A (quiet start, floor 4.8) | B (full pipeline, machine busy, floor 15.1) | C (floor 6.8) |
| --- | --: | --: | --: |
| decide, sequential, p50 / p95 / p99 | 7.4 / 15.3 / 23.0 | 14.2 / 21.0 / 26.5 | 12.2 / 20.8 / 25.5 |
| decide, 4 concurrent callers, p50 / p95 / p99 | 10.5 / 12.6 / 19.1 | 24.3 / 36.5 / 40.0 | 15.1 / 17.6 / 21.1 |
| requests per second, 4 callers | 366 | 158 | 255 |
| ingestion batch of 100, alone, p50 / p95 | 53.4 / 61.8 | 76.3 / 94.3 | 55.9 / 90.5 |
| ingestion batch of 100, with 4 deciders running, p50 / p95 | 44.9 / 51.6 | 85.5 / 121.5 | 63.9 / 72.4 |

Read: a decision over real HTTP costs about 7-14 ms at the median and stays under 30 ms at p99 on this laptop, with 60 rules and a transaction that writes an event; the evaluation itself is microseconds
(5.5 microseconds in step 2). Deciders do not make ingestion slower in any run beyond noise (the p95 with deciders running was lower in A and C, and 29% higher in the busy run B, inside the
same spread as the "alone" figure). The step-2 in-process harness measured 17-19 ms at the median because it measured a different thing (the ASGI transport, a fresh run for each call, one process
running the test fixtures); the two baselines are not comparable to a user's network and should not be compared with each other, only each with its own next measurement.

## What the same script asserts about correctness

The dry run over the 720 crafted events of three runs and over the demo's own five recorded runs (52 mapped actions) gives exactly the counts of an independent evaluation (a regular-expression glob and a
hand-written event mapping, in the check script): crafted runs 476 allow, 108 deny, 136 require approval over 26 rules; demo runs 46 allow, 5 deny, 1 require approval. Samples carry run id, event id,
capability, risk class, decision and rule id, nothing else, and no path, command or push target appears in the response.

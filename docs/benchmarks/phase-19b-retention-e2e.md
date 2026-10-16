# Phase 19b step 6: what enforcement costs ingestion (informational)

Measured 2026-10-16 with `scripts/retention-e2e.sh` (`make retention-e2e`), gate 8, in a full run that ended `RETENTION-E2E PASSED` (181 checks, 0 failed). A **regression baseline
for one laptop, not a capacity claim**: re-measure on target hardware before quoting a figure (spec section 158). **Nothing here is asserted by CI.** The job asserts only the
deterministic facts printed next to the figures: every ingest was accepted (202), none was refused, no 5xx, no rejected event, and every enforcement tick finished with exit 0
having expired the 30 runs it was given. The timings are recorded so a reader can see a change between releases.

## Environment

| | |
| --- | --- |
| Host | Apple M5 Pro (15 cores), macOS (Darwin 25.6), the developer machine, **shared with other running work**: 1-minute load average 2.67 before the rounds, 2.62 after |
| Database | PostgreSQL 16 (the Compose container in the Colima VM, host port 5433); the API and worker connect as a throwaway login that is a member of `abb_runtime`, the retention job as a login that is a member of `abb_retention` |
| Processes | one API process, one worker, one retention process per round (`run-once`), the fake OIDC provider; the load generator is one Python process on the same machine (it competes with the API for CPU; this is stated, not corrected for) |
| Load | one client, sequential: each request is a `POST /v1/events/batch` of one 4-event run, a new connection per request |
| Enforcement | `ABB_RETENTION_MODE=enforce`, `ABB_RETENTION_BATCH_SIZE=1` (one row per transaction, the worst case for contention: the most transactions per run), `ABB_RETENTION_MAX_RUNS_PER_TICK=30` |
| Data | 150 runs aged 120 days (4 events, 2 artifacts of 1.5 KB and 1.8 KB, spans, a cost line, a search entry and findings each): 30 per round, so each tick expires the artifacts and the trace detail of 30 runs |
| Rounds | 5, interleaved: 60 idle posts, then posts for as long as the enforcement process runs |

## Result

| | requests | p50 | p95 |
| --- | --: | --: | --: |
| idle (no retention process) | 300 | 11.1 ms | 15.8 ms |
| while the retention process enforces | 1,386 | 12.1 ms | 13.7 ms |
| ratio | | 1.09 | 0.87 |

* An enforcement tick of 30 runs took 3.4 to 3.5 s (five rounds: 3.44, 3.42, 3.43, 3.51, 3.44 s), during which 275 to 279 ingest requests were accepted (about 80 per second from one client).
* **Zero refusals, zero 5xx, zero rejected events** in all 1,686 requests (asserted).
* The p50 rose by about one millisecond (9%); the p95 was *lower* during enforcement than at rest, which means the spread at this scale is noise (a single slow request moves a p95 of 300 samples), not an effect of the job. Read the figures as "no measurable
  cost at this size", not as "free".

## The stated bound (a statement, not an assertion)

On a machine like this one, with one-row batches (the most contended setting), ingestion latency while the retention job enforces stays within **1.5 times the idle p50** for small batches, and no request is refused or fails. The
reasons are structural, and the job's design does not depend on the figure: each batch is its own short transaction with a statement timeout, deletes by `ctid` of a bounded sub-select, takes no table lock, and the job
holds a per-workspace advisory lock that ingestion never takes; the project row that serialises one project's event ingestion is not touched by deletion (the artifact byte counter row is, for the length of one small update per artifact). If a deployment sees more, lower `ABB_RETENTION_BATCH_SIZE`, run the job in a
quiet window, or raise the tick interval; `docs/runbooks/retention.md` says how.

What this does **not** show: a database with tens of millions of events (index maintenance and vacuum pressure from deleting at that scale are not measured here), many concurrent writers, or a database that is already near its
connection limit. KI-225 records that CI does not track the figure.

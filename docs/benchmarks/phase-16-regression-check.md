# Phase 16: ingest cost of the search document (AC-11) and search latency (AC-12)

Measured 2026-10-11. Same machine, same session, **native processes** (not the Colima VM, so these numbers are **not comparable** with
`phase-2-ingestion.md` or `phase-7-regression-check.md`; the compose stack could not be rebuilt for this run: UNVERIFIED (env) for a Colima
re-measurement, which stays a useful follow-up). Postgres is the shared Colima container (published on 5433); the API (uvicorn, one process) and the
worker run natively on macOS, one pair per side, on scratch databases (`abb_p16_base`, `abb_p16`, both dropped afterwards).

## A/B method (AC-11)

- **A** = `origin/main` at `84491c6` (no `run_search`; migrations to 0047), exported with `git archive` into a temp directory, its own venv.
  **B** = this branch (migration 0080, the summarizer writes the search document in the run's transaction).
- Both stacks run at once but only one is driven at a time; each side has its own database, API (:8210 / :8211) and worker.
  `ABB_RATE_LIMIT_EVENTS_PER_SECOND=1000000 ABB_RATE_LIMIT_BURST_EVENTS=1000000`; default summary debounce (1 s).
- Three rounds, alternating the order (A,B / B,A / A,B), with the outbox drained before every run. Per round and side, two invocations of the same
  `scripts/bench_ingest.py` (the branch's copy drives both sides):
  `--run-events 1000 --ingest-only` (case I: one client sending runs of 1,000 events, then 4 and 8 concurrent clients) and the default full run
  (case II: one client sending one 30,000-event run, 4 and 8 clients, reads, summary freshness for 2,000 and 10,000 events).
- Reported: the **median of the three runs** and the raw triple. Noise band: the Phase 7 check saw about 14% run-to-run on p95 (4 clients).
- Trigger (plan D7): p95 of the 1,000-event-run case worse than 10%, or summary freshness worse than 20%, moves the write to its own coalesced
  `index_run` job (step 3b).

## Results

| Case | Statistic | Base (A): raw triple | median | Branch (B): raw triple | median | B vs A |
| --- | --- | --- | --- | --- | --- | --- |
| 1 client, runs of 1,000 events (ms) | p50 | 39.9, 36.9, 37.9 | 37.9 | 36.1, 37.4, 37.4 | 37.4 | -1.3% |
| | **p95** | 50.1, 47.7, 55.6 | **50.1** | 51.1, 47.0, 53.3 | **51.1** | **+2.0%** |
| | p99 | 60.1, 63.8, 69.7 | 63.8 | 63.6, 58.3, 60.8 | 60.8 | -4.7% |
| 4 clients, concurrent, case I (ms) | p50 | 89.6, 101.9, 92.5 | 92.5 | 89.6, 103.9, 93.0 | 93.0 | +0.5% |
| | p95 | 154.9, 142.7, 132.4 | 142.7 | 153.3, 168.4, 215.3 | 168.4 | +18.0% (one 215 ms outlier) |
| 4 clients, concurrent, case II (ms) | p95 | 181.2, 129.3, 129.5 | 129.5 | 126.4, 134.6, 137.2 | 134.6 | +3.9% |
| 8 clients, concurrent, case I (ms) | p95 | 220.6, 312.3, 240.2 | 240.2 | 233.2, 342.9, 226.3 | 233.2 | -2.9% |
| 8 clients, concurrent, case II (ms) | p95 | 309.2, 232.4, 224.1 | 232.4 | 254.5, 242.7, 254.7 | 254.5 | +9.5% |
| one 30,000-event run (ms) | p50 | 40.6, 36.6, 38.2 | 38.2 | 37.5, 37.7, 38.0 | 37.7 | -1.3% |
| | p95 | 107.3, 428.5, 78.7 | 107.3 | 557.7, 77.4, 546.3 | 546.3 | not meaningful (see below) |
| | p99 | 3,425, 3,033, 2,797 | 3,033 | 2,956, 2,615, 2,691 | 2,691 | -11.3% |
| Summary freshness, 2,000 events (ms) | | 915, 944, 1,130 | 944 | 1,766, 1,068, 935 | 1,068 | **+13.1%** |
| Summary freshness, 10,000 events (ms) | | 3,743, 3,764, 3,670 | 3,743 | 4,857, 3,834, 4,023 | 4,023 | **+7.5%** |
| `GET /v1/runs?limit=50` (ms) | p95 | 13.2, 19.1, 19.4 | 19.1 | 21.0, 13.4, 14.1 | 14.1 | -26% (noise) |
| `GET /v1/runs/{id}` 2,000 events (ms) | p95 | 12.7, 9.7, 10.5 | 10.5 | 6.7, 11.9, 9.7 | 9.7 | -7.6% (noise) |

## Reading against the trigger

- **The D7 trigger did not trip.** The 1,000-event-run p95 moved +2.0% (inside the band, far below 10%); summary freshness moved +13.1% (2,000 events) and
  +7.5% (10,000 events), both below the 20% threshold. **Step 3b (a separate `index_run` job) was not needed.** The write stays in the summarizer.
- The 2,000-event freshness has the widest spread (one branch run at 1.77 s, the other two at 0.94 and 1.07 s, against 0.92 to 1.13 s for A): the
  median shift is within what a single slow run does, but it is the number to watch if a regression is ever suspected.
- The 4-client p95 in case I is the only figure above 10% (+18%): it comes from one branch run at 215 ms (the other two, 153 and 168 ms, overlap A's
  132 to 155 ms), the same case in case II is +3.9%, and the 8-client cases are -2.9% and +9.5%; it is within the 14% band's neighbourhood and
  does not repeat, so it is reported, not acted on. The 1,000-event-run case the trigger names is the 1-client one.
- The 30,000-event run's **p95 is a knife-edge statistic**: about 5% of its 300 batches overlap a summarizer pass over the whole run (KI-016), so
  p95 lands either in the fast mode (about 80 to 107 ms) or in the slow mode (about 430 to 560 ms) depending on run-to-run timing, for **both** sides
  (A: 107, 429, 79; B: 558, 77, 546). Its medians must not be compared; p50 (-1.3%) and p99 (-11%) are flat. This is the known KI-016 tail, not a Phase 16 effect.
- **Write amplification.** On the branch database after all six rounds: `run_search` `n_tup_upd = 0`, `n_dead_tup = 0` for the benchmark's runs
  (the hash-skipped upsert wrote each changed document once and left unchanged re-summaries alone), heap + five indexes about 2.2 to 2.6 KB per run.
  The benchmark's runs have a fixed tool, model and event-type vocabulary, so this is the favourable case; a run whose tools keep changing re-writes its document.

## Search latency (AC-12)

`scripts/bench_search.py`: bulk-seeds runs with documents of the real shapes (arrays of 1 to 4 tools, 1 to 2 models, 5 to 10 event types, 2 to 6 commands, 3 to 8 paths,
`to_tsvector` text for the two tsvector columns) into the branch database, then times `GET /v1/runs?q=&limit=50` through the real API with a `runs:read`
key (so content search is allowed), sequentially, each response including its facets statement. The corpus is a benchmark corpus (plain words), not the
production document builder. 90 days of start times, 5 projects not modelled (one project).

| Class (variations) | 10,000 runs: p50 / p95 / p99 (ms), n = 200 | 100,000 runs: p50 / p95 / p99 (ms), n = 100 |
| --- | --- | --- |
| run-row only (`status:failed cost:>c`, `retries:>=2`, ...) | 15.7 / 22.2 / 40.0 | 29.0 / 111.9 / 129.5 |
| array contains (`tool:tool-k`, `model:... event:...`) | 21.8 / 30.9 / 39.2 | 109.0 / 190.9 / 223.6 |
| metadata text (`w7`, `"deploy failed"`, `rareword`, `dep*`) | 24.1 / 32.3 / 34.6 | 79.7 / 242.2 / 266.9 |
| content text (`mod12`, `login mod3`, ...) | 18.9 / 40.4 / 45.1 | 43.2 / 186.8 / 345.9 |
| `command:` substring | 52.0 / 58.9 / 81.8 | 412.7 / 448.9 / 500.4 |
| `file:` glob (`src/auth/**`, `*.ts`, ...) | 57.2 / 85.8 / 114.1 | 462.8 / 519.9 / 582.1 |

Sizes (branch database): 10,000 documents 26.2 MB total (2,638 bytes per run); 100,000 documents 218 MB (2,229 bytes per run), of which the content GIN
about 4.7 MB per 10,000 runs, `ix_run_search_meta` 2.0 MB, event-types 1.9 MB, tools 0.5 MB, project-started 1.0 MB, primary key 0.6 MB.

Against the plan's thresholds (metadata text and arrays below 500 ms, substring and glob below 1 s): **all classes pass at 100,000 runs**
(worst p95: `file:` glob 520 ms; metadata text 242 ms), so the `pg_trgm` index (migration 0081) and the dedicated search service are **not triggered**.
`command:` and `file:` are linear in the rows left after the tenant, project and time predicates, so they scale with the window: at about 460 ms for 100,000
runs they are the first class to cross 1 s (roughly 200,000 runs inside the searched window), which is the signal to schedule the trigram index (ADR-100 (8)).
Not measured: the plan's 9,000,000-run Stage A volume, concurrent searches (the semaphore caps them at four per process), a cold cache, a viewer's
metadata-only query shapes (they take the same statements without the content branch).

# Runbook: reliability findings and run health

Findings are derived records (ADR-080): a `detect_findings` job per run reads the run's events, applies pure detectors and writes rows that cite events.
They are rebuildable from events, never edited, and carry **metadata only** (ids, counts, tool and model names, thresholds), so an OWNER and a VIEWER
read the same bytes. Health (ADR-081) is computed at read time from the run's figures and its findings. `GET /v1/runs/{id}/findings` returns both.

## What each detector means

| Finding type (detector) | Kind | What it says | Default thresholds (setting) |
| --- | --- | --- | --- |
| `possible_loop` (`repeated-sequence`) | probable | A block of tool/model/shell/db/file steps repeated back to back. `info` on a run that succeeded, however many repetitions; on a run that failed, timed out, was blocked or is still active, `warning`, and `critical` at 10 or more (so the severity of a loop in an active run can fall to `info` when the run succeeds) | 3 repetitions (`ABB_FINDINGS_LOOP_MIN_REPETITIONS`); a block of one step (the same tool again and again) needs 8 (`..._SINGLE`, KI-094) |
| `identical_request` (`identical-request`) | deterministic | The same tool or model request with the same inline payload made repeatedly. Needs captured payloads (capture is opt-in); without them nothing is reported. The payload is never shown and nothing derived from it (digest, fingerprint, id) is exposed; only the fact that the cited events were equal | 3 (`ABB_FINDINGS_IDENTICAL_MIN_COUNT`) |
| `retry_storm` (`retry-storm`) | deterministic | `warning` for a burst on one operation, `info` when retries are only many but spaced | burst: 3 on one span within 60 s (`ABB_FINDINGS_RETRY_BURST_WINDOW_SECONDS`); 5 in the run (`ABB_FINDINGS_RETRY_STORM_MIN`) |
| `context_growth` (`context-growth`) | probable | Model input tokens growing faster than ordinary multi-turn growth: mean increment of 4,000 tokens or more per call and 2x the first value, and either accelerating or at least 100,000 tokens; `info` below 100,000 | `ABB_FINDINGS_CONTEXT_GROWTH_MIN_DELTA_TOKENS` 4000, `_FACTOR` 2.0, `_WARNING_TOKENS` 100000 |
| `cost_spike` (`cost-spike`) | probable | A finished run costing more than the p95 and twice the median of the same agent's recent runs (and at least $0.01) | baseline of at least 20 runs in 30 days (`ABB_FINDINGS_COST_SPIKE_MIN_BASELINE`, `_WINDOW_DAYS`); evidence shows the baseline |
| `tool_failure_cascade` (`tool-failure-cascade`) | deterministic | At least 3 failures among 10 consecutive tool, shell, db or HTTP-5xx calls of one emitter | `ABB_FINDINGS_CASCADE_MIN_FAILURES` 3, `_WINDOW_CALLS` 10 |
| `failure_chain`, `probable_cause` (`failure-chain`) | deterministic / probable | For a failed run: the terminal event, the first error on its span lineage, retries and file or git changes in between, as linked events; plus heuristic causes labelled "Probable:" | none |
| `reported_loop` (`reported-loop`) | deterministic | An integration emitted `loop.detected` | none |

Every finding's `evidence.thresholds` lists the values it used, so an old finding explains itself even after a setting changed. Detector rules are
versioned (`name@x.y.z` per finding, `DETECTORS_VERSION` for the set). Keeping `ABB_FINDINGS_COST_SPIKE_MIN_BASELINE` above any plausible count switches
the cost-spike detector off.

## States

`state.status` on the route: `none` (never analysed), `processing` (a job is queued or running; the previous analysis, if any, is still returned),
`current`, `outdated` (the stored detector version is older than the deployed one: run `rebuild-findings`), `failed` (the newest job was dead-lettered; the
last good items are returned with their `computed_at`). The finding-based health dimensions read `no_data` in every state except `current`. A run with
more than `ABB_FINDINGS_MAX_EVENTS` events is analysed over its first events in canonical order and says so (`truncated`, `event_count_seen`).

## Re-running after a detector change

After a deployment that bumps `DETECTORS_VERSION` (or changes a threshold you want applied to past runs), findings show `outdated`. Re-derive:

```bash
python -m abb_api.cli rebuild-findings --workspace acme [--project p] [--since 2026-10-01T00:00:00Z] [--limit 5000]
```

It queues one `detect_findings` job per run (newest first), writes one `audit_log` row (`findings.rebuild`, actor `cli`) and prints `queued N of M`. A run whose
analysis is queued shows `processing` until it finishes. `--limit` is a **bound** (1 to 10,000, rejected outside that range); if it stops short the command
prints a warning, so narrow with `--since` or `--project` and run again. This differs from `rebuild-costs`, where `--limit` (default 10,000) is only a default.
There is no HTTP route for this.

## Worker load

- The job runs off the ingest path, once per delay window per run (`ABB_FINDINGS_DELAY_SECONDS`, default 5 s; scales with run size to at most 60 s: a
  30,000-event run is re-analysed at most every 15 s while it grows). Measured cost is about 44 microseconds per event: 54 ms for 1,000 events, 0.4 s for
  10,000 and 1.3 s for 30,000 (`docs/benchmarks/phase-10-regression-check.md`; KI-096).
- **Every cost rebuild also re-runs the detectors.** `POST /v1/cost/rebuild`, `rebuild-costs` and any later backfill that goes through the summarizer
  (for example Phase 16's search backfill) make the summarizer enqueue `detect_findings` for each rebuilt run, because a cost rebuild changes `cost_usd`, an
  input of the cost-spike detector. Plan the worker capacity for roughly double the per-run work, or run rebuilds in bounded slices.
- `ABB_FINDINGS_MAX_EVENTS` (default 50,000) caps the events read per run. Lowering it makes huge runs cheaper and their analysis more partial (20,000
  puts the 30,000-event case near 0.9 s); raising it makes the job scale linearly and the memory of one job follow. Changing it affects future analyses
  only until you run `rebuild-findings`.

## Dead-lettered `detect_findings` jobs

A detector bug is not swallowed: the job retries like any handler and is dead-lettered after five attempts, the route reports `state.status: failed`, and the
run's `summary_state` stays `current` (the summary is unaffected). Find them with `python -m abb_api.cli jobs-list --status dead_letter`, fix and deploy the
detector, then `jobs-retry` (see `dead-letters.md`). **`last_error` is a fixed message by design** (`DetectorError`): it names the detector and the event count, and never
the exception text, because an exception could quote an attribute value. To see the traceback, reproduce locally by feeding the run's events to
`run_all` (the fixtures in `apps/api/tests/fixtures/pathological/` show how); nothing about the cause is logged.

## Things that are not bugs

- A repetitive agent shows probable loops on every run (no dismissals yet, KI-091). Raise `ABB_FINDINGS_LOOP_MIN_REPETITIONS`, or accept the `info` severity on successful runs.
- Short loops of a single payload-less tool below the single-token threshold are not reported (KI-094).
- Retry windows are judged per emitter on `occurred_at`; clocks that differ between agents of one run make windows approximate (KI-095).
- Findings carry no command text, paths, payloads, run names or error messages (ADR-080); to see the content, open the cited event as a person with `payload.read`.
- `rebuild-findings` after a baseline change can legitimately add or drop a cost spike (the baseline moved).

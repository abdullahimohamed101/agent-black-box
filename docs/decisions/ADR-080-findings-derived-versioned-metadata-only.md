# ADR-080: Findings Are Derived, Versioned, Metadata-Only Records Produced by a Bounded Detector Job Over the Outbox

Status: Accepted
Date: 2026-10-11
Phase: 10

## Context
Spec §81 asks for deterministic detectors over windows of events with a versioned finding contract (§81.2: type, severity, confidence, start/end
event ids, evidence, detector version) and §80 for an evidence-based failure analysis; §72.2 lists the anomaly detector as a processor; INV-2
requires derived state to be rebuildable; ADR-061 withholds captured content from actors without `payload.read`; KI-016 and KI-022 make any new
per-run pass over events a cost to bound.

## Decision
1. **A `detect_findings` outbox job**, enqueued by `summarize_run` in its transaction (dedupe per run, delay growing with the run's event count,
   `findings_delay_seconds` x max(1, events / 10,000), capped at 60 s). The handler tries a per-run advisory lock (one writer; the loser re-enqueues
   itself and returns; its writes take only the foreign-key share lock on the run row, compatible with the summarizer's `FOR NO KEY UPDATE`, the
   pattern of `analytics/rollup.py`). It loads at most `ABB_FINDINGS_MAX_EVENTS` events (default 50,000) by the run's **full canonical order key** in SQL,
   then reads the run row in the same transaction (cost, project, agent, versions), derives spans, folds status and counters from the loaded events (so
   facts and events are one snapshot), runs every detector as a pure function per emitter (`agent_id`), and upserts `findings` and `run_findings` by
   fingerprint. The summarizer, `summary_state` and the live stream are untouched. A detector exception is re-raised as a fixed-message
   `DetectorError` (from `None`) so neither `outbox_jobs.last_error` nor the dead-letter log can carry an attribute value, and only the detect job
   dead-letters. A detect result is a function of the event set seen, the run's cost at that time and the cost baseline at that time; only the
   cost-spike finding depends on the last two.
2. **Rows.** `findings` (one row per finding, composite tenant key, cascade from the run) holds `detector_version` (`name@semver`), `kind`
   (`deterministic | probable`), `severity`, `confidence`, `start_event_id`/`end_event_id`, `evidence_event_ids uuid[]` (every cited event, at most 50,
   validated against the run), a stable `fingerprint` (`sha256(detector | type | start_event_id | key)`), an `id` derived from the fingerprint
   (UUIDv5, `fnd_` prefix: stable across rewrites; `created_at` is first-seen and kept by the upsert, `updated_at` moves) and a bounded JSON `evidence`.
   `run_findings` (one row per run) records `detectors_version`, `summary_version_seen`, `ordering_mode_seen`, `event_count_seen`, `truncated`,
   `finding_count` and `max_severity`. A bump of `DETECTORS_VERSION` makes stored findings `outdated` (never presented as current); `rebuild-findings`
   re-derives (argparse-bounded `--limit` of 1 to 10,000, audited as `findings.rebuild`, actor `cli`); after a dead letter the previous analysis stays
   readable, marked `failed` with its time. A late-arriving earlier event can extend a stretch backwards and change a fingerprint, so a future dismissal
   design must not assume stability under late arrival (KI-091).
3. **Evidence is metadata by construction.** An allowlist of keys per finding type (ids, agent slugs, event types, counts, tool and model names, exit
   and status codes, thresholds); never a content attribute, span name, payload or payload digest (the identical-request digest groups events in memory and is
   never stored, shown or part of the fingerprint key: every other fingerprint input is public in the same response, so a digest in the key, or a
   stored prefix of it, would be an offline equality oracle on withheld payloads; the fingerprint and the `id` derived from it are functions of public metadata
   only, and what a reader can learn is limited to "these events carried equal payloads"), `test.suite`, run name, tag, metadata, free-text reason or
   exception text. Titles and messages are generated at read time from the typed evidence. Consequently the route needs no per-actor projection: a VIEWER
   sees what an OWNER sees, and `run.read` governs the route (no new action).
4. **The cost-spike detector is the one that reads beyond the run**: a bounded baseline of the same workspace, project and agent's recent summarised
   finished runs (the newest 200 of the previous 30 days, at least 20, queried in the runs module as `RunQueries.cost_baseline`, nearest-rank p95),
   measured from the start of the run under analysis so a rebuild months later sees the same window. Its evidence stores the baseline; a rebuild may
   differ when the baseline has moved; an insider with `event.write` can move it (a false or missing probable finding, not exposure).
5. **Bounds are rules of the `Finding` model, not of the database**: 50 findings per detector, 200 per run (lowest severity dropped first), 50 ids per list,
   20 pattern tokens, every client-controlled string truncated to 128 characters, serialised evidence under 15 KiB (the 16 KiB CHECK is the backstop),
   duplicates by fingerprint collapsed before any write (a collision can never dead-letter the job).
6. **Every cost rebuild also re-runs the detectors** (the summarizer always enqueues the job). The enqueue is not skipped on an unchanged event count
   because a cost rebuild changes `cost_usd`, an input of the cost-spike detector. The runbook states the worker load.

## Alternatives
- Detectors inside `summarize_run` (one snapshot, no second read): rejected because summary freshness and `summary_state` would carry detector cost and
  failures, and re-running detectors would re-derive everything. Revisit if the second read shows up in `make bench` beyond the budget.
- Findings in `runs.summary` JSONB: no ids, no bounds, every list pays for them.
- Read-time detection: O(events) per view and unversioned.
- Append-only finding versions: unbounded per run; the version column plus a rebuild is the history.

## Consequences
Findings lag the summary by the delay (5 s default, longer for huge runs) and are visible as `processing`; one more derived table pair and one more job
type per run (KI-021 grows faster); huge runs are analysed partially and say so; a rule change needs a deployment, a version bump and a bounded rebuild;
findings never leak content, so there is nothing to project per role. Measured cost: about 44 microseconds per event, 1.33 s for a 30,000-event run
(`docs/benchmarks/phase-10-regression-check.md`, KI-096).

## Migration implications
`0060` is additive; existing runs have no findings until re-summarized or `rebuild-findings` runs; the runtime role keeps full DML on the new tables
(derived, rewritten).

## Amendment (2026-10-18, Phase 20, ADR-174)
The job stays one coalesced pending job per run, but its enqueue delay now also honours a per-run minimum interval (`ABB_FINDINGS_MIN_INTERVAL_SECONDS`, default 10 s) measured from the last analysis, with a terminal-run bypass.

## Revisit conditions
The detect job's share of worker time exceeds the summarizer's at Stage A volumes (merge into the summarizer or a dedicated worker pool); a customer
needs per-project thresholds (Phase 14); dismissals are requested (a `finding_dismissals` table keyed on `fingerprint`); alerts (Phase 17) need
findings as triggers (the job is the natural hook).

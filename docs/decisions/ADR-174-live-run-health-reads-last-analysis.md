# ADR-174: A live run's health reads the last completed analysis, labelled with its age

Status: Accepted (Phase 20, steps 3 and 4). Implements D3, D7 and D8 of the Phase 20 plan; D3 was decided by the user, D7 and D8 are the assistant's recommendations. Amends ADR-081 (health dimensions) and ADR-080 (findings job cadence). Resolves KI-097.
Date: 2026-10-18
Phase: 20

## Context

ADR-081 makes the finding-based health dimensions (`loops`, `context`, and the cost-spike part of `cost_efficiency`) `no_data` unless the findings state is `current`. `FindingsService._status` returns `processing` whenever a `detect_findings` job is pending or running. Every summary of an active run re-enqueues that job, so while events keep arriving the state is `processing` almost continuously and a live run showed no loop or context health until it ended, although the previous analysis was stored and listed (KI-097, security review P3-1). A live run is when health matters most.

## Decision

### Read path

1. **`processing` with a stored analysis of the current detector rules reads that analysis.** `health()` takes an optional `as_of` (the stored `run_findings.computed_at`). The service passes it only when the status is `current`, or `processing` and `detectors_version >= DETECTORS_VERSION`. The health function stays pure: nothing is read from the clock.
2. **Additive response fields, no change to existing vocabulary.** Each health dimension gets `as_of` (timestamp or null) and `stale` (boolean, default false); `RunFindingsOut` gets `server_time` (the database clock at read time). `state.status` keeps `none|processing|current|outdated|failed`, so existing clients are unaffected. The dimensions that read findings (`loops`, `context`, and `cost_efficiency` when it has a priced cost) carry `as_of`; `stale` is true only for the borrowed reading, and the rule gains "Based on an earlier analysis; newer events are still being analysed."
3. **Everything else keeps `no_data`.** A run never analysed is "not analysed yet" / "analysis in progress" with no `as_of`. `outdated` (the rules changed) and `failed` are not borrowed: a result of older rules, or after a failed analysis, is not honest to show as the run's health. A stored result of older rules that is merely queued for re-analysis is not borrowed either.
4. **`server_time` exists so the client measures age against the server's clock**, not its own: a browser clock that is minutes off would otherwise print "as of 5 min ago" for a fresh result. The client corrects by the offset between `server_time` and its clock at fetch time.
5. No event, finding or `run_findings` row is written or changed by this: derived state is still rebuildable from events (INV-1, INV-2).

### Cadence

6. **A per-run minimum interval, `ABB_FINDINGS_MIN_INTERVAL_SECONDS` (default 10 s, `0` disables).** Where the job is enqueued (`jobs/handlers.py::enqueue_detect`) the delay is `max(detect_delay(...), interval - age(computed_at))`, with the age taken on the database clock (`now()` in the same transaction as `run_findings.computed_at`). `findings/job.py::detect_enqueue_delay` is the pure arithmetic and takes the clock as an argument, so the tests inject it.
7. **Trailing by construction.** The job stays one coalesced pending job per run (`dedupe_key`); events arriving in the wait join it, so the last batch is always analysed after the interval. No new table, no timer.
8. **No throttle when the run ends.** If the summary shows a terminal status, the floor is skipped and, because an already queued throttled job would otherwise keep its later `available_at`, `OutboxRepository.enqueue(..., advance=True)` brings a pending job forward (never back). Only the outbox row changes; no event is touched (INV-1).
9. **Known limit.** The floor is measured when the job is enqueued. A job that is already running when the next batch arrives is not delayed by it, so two analyses can complete close together at the boundary; the rate is still bounded by one running plus one pending job per run. `rebuild-findings` and cost rebuilds enqueue without the floor.

## Alternatives considered

- **Analyse on every batch.** Rejected: cost grows with run size and the 5 s coalescing delay already exists for that reason.
- **A new state value (`stale`).** Rejected: breaks exhaustive clients; `status` stays the job state, `stale` is a property of the reading.
- **Borrow results of `outdated` rules.** Rejected: ADR-081's honesty rule.
- **Client-clock ages.** Rejected: skew.
- **A timer or debounce table for the interval.** Rejected: the outbox already coalesces and delays; a second scheduler is more state to keep consistent.
- **Throttling inside the handler (re-enqueue itself when too early).** Rejected for now: it would also cover the running-job boundary of point 9, at the cost of a wasted claim per run; revisit if the boundary shows up in the metrics.

## Consequences

A live run shows loop and context health from its last completed analysis, clearly labelled. The label can be wrong in only one direction (older than the events), which is what it says.

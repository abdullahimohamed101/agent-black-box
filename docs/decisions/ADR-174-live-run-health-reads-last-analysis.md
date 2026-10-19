# ADR-174: A live run's health reads the last completed analysis, labelled with its age

Status: Accepted (Phase 20, step 3; the cadence section is completed in step 4). Implements D3, D7 and D8 of the Phase 20 plan; D3 was decided by the user, D7 and D8 are the assistant's recommendations. Amends ADR-081 (health dimensions) and ADR-080 (findings job cadence). Resolves KI-097.
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

## Alternatives considered

- **Analyse on every batch.** Rejected: cost grows with run size and the 5 s coalescing delay already exists for that reason.
- **A new state value (`stale`).** Rejected: breaks exhaustive clients; `status` stays the job state, `stale` is a property of the reading.
- **Borrow results of `outdated` rules.** Rejected: ADR-081's honesty rule.
- **Client-clock ages.** Rejected: skew.

## Consequences

A live run shows loop and context health from its last completed analysis, clearly labelled. The label can be wrong in only one direction (older than the events), which is what it says.

# ADR-121: Events Carry the Cost Engine's Settled Per-Call Cost

Status: Accepted (Phase 12, review fix P1-2). Amends ADR-120 ("no new endpoint, no schema change": the endpoint set and the database schema
are unchanged; one additive response field pair is new).
Date: 2026-10-12
Phase: 12

## Context

Replay's "cost so far" (ADR-120) added up the `cost.estimated_usd` attribute of the events the run page holds. The run header's figure is
different: `estimated_cost_usd` is the cost engine's effective total (ADR-040: provider-reported, else price book, else the caller's own
estimate). A run traced by the Python SDK without `cost_usd=` (tokens and a model name only) is priced by the price book on the server and
carries no cost attribute, so replay said "$0.00 so far" at every playhead while the header said, for example, "$0.42". The adversarial
review (P1-2) proved it with the failed fixture stripped of `cost.estimated_usd`.

The server already prices every `llm.request.completed` call into `cost_calculations` (one row per call, primary key
`(workspace_id, event_id)`, rewritten wholesale whenever the run is summarised, INV-2). The events read path simply did not expose it.

## Decision

- `EventOut` gains two additive, read-only fields, on `GET /v1/runs/{id}/events` and `GET /v1/runs/{id}/events/{event_id}`:
  - `cost_usd: number | null`: the stored line's effective `total` for that call (the figure `estimated_cost_usd` adds up);
  - `cost_unpriced: boolean`: the stored line exists and nothing could price it (`source = unpriced`; `cost_usd` is then 0).
  Both are `null`/`false` for events with no stored line: every event that is not a completed model call, a run whose lines are not derived
  yet (the summary is `processing`), and live stream frames (the stream sends events as they arrive, before the debounced summariser runs).
- Metadata for every role (INV-5): a USD figure and a flag derived from token counts, model names and prices, never an attribute value, a
  payload or a name. A viewer gets exactly what an owner gets (`tests/authz/test_content_withheld.py`).
- One batched, workspace-scoped lookup per page: `CostRepository.event_costs(run_id, event_ids)`, `workspace_id = ? AND run_id = ? AND
  event_id = ANY(?)` on the primary key, for the page's model calls only (at most 500 ids). No N+1, no new index, no migration, no new route
  (the route-walking authz registry is unchanged). `tests/test_event_costs.py` asserts one call per page, the workspace scoping and, on a
  200,000-row table, an index plan.
- The replay projection reads `cost_usd` when it is present (so at the last event the replay figure equals the run summary's
  `estimated_cost_usd`, tested against an API-backed run whose events carry no cost attribute). Where the server has not priced a call
  (`cost_usd` null on a model call: a live arrival, an underived run) it falls back to the engine's own client-side order, `cost.provider_usd`
  then `cost.estimated_usd`, and the figure is then shown as "about" rather than exact. A call with neither, or one the server flagged
  `cost_unpriced`, makes the figure a floor: "at least $X, N calls unpriced". Replay never claims parity it does not have.

## Alternatives considered

- Floor-only (read `cost.provider_usd` before `cost.estimated_usd`, render "at least", record a known issue): honest, but the common SDK path
  would show a floor of $0.00 forever. Rejected because the data was one indexed lookup away.
- Pricing in the browser from a price book endpoint: a second implementation of the engine that would drift (ADR-040 precedence, tiers,
  regional multipliers, overrides). Rejected.
- Adding the figure to the cursor query with a join: couples the keyset page to a derived table that may lag. Rejected; a second statement
  on the same connection keeps `EventQueries` unchanged.

## Consequences

- Measured on a 10,000-event run (2,000 priced calls, 20 pages of 500, local PostgreSQL, three runs of seven walks each): walking the whole
  run took 780-830 ms median before and 910-940 ms after, about +5 ms per page (+12%).
- The two fields appear in `openapi.json` and the generated web types; `cost_unpriced` is required in the TypeScript type because the
  response always sends it.
- While a run is being summarised, events may briefly show `cost_usd: null`; the figure becomes exact on the next read after the summary is
  current. A rebuild of cost lines (a price override) changes the figures on the next read, like the header's.
- The timeline's per-row cost and the first-error headline still read the `cost.estimated_usd` attribute (outside replay). They are
  unchanged by this ADR; moving them to the settled figure is a follow-up.

# ADR-040: Cost Is Computed by a Versioned, Reproducible Engine Inside the Summarizer

Status: Accepted
Date: 2026-10-08

## Context
Spec §79 requires reproducible cost calculations: a versioned pricing table, each calculation storing its pricing version, estimated and
provider-reported values kept visually apart, and user overrides. Until now `estimated_cost_usd` was the sum of a number the SDK user supplies
(`cost.estimated_usd`); nothing could explain it. INV-2 requires cost totals to be rebuildable from events plus versioned metadata.

## Decision
- **`CostEngine` is pure.** Input: an `llm.request.completed` event (tokens, cached tokens, model, provider, `occurred_at`, optional reported cost) and a
  price book. Output: a `CostLine`. Prices are `Decimal`; per-call amounts are rounded to 9 decimal places.
- **Price entries are immutable data** (§79.1): `pricing_version`, `provider`, `model_pattern` (glob), `valid_from`, `valid_to`, per-million prices
  (input, output, cached input), per-request price, currency (USD only), source. An entry applies when `valid_from <= occurred_at < valid_to`. Among applicable entries the
  most specific pattern wins (longest literal prefix, then an exact provider match), then the latest `valid_from`. The event time, not the processing time, selects the entry, so
  rebuilding next year gives the same answer for the same version set.
- **Overrides** (`pricing_overrides`, workspace or project scoped, append-only) take precedence over built-in entries. Their pricing version is `override:<id>`. Newest `valid_from` wins.
  Changing an override does not rewrite history by itself: `rebuild-costs` re-derives the runs a user chooses.
- **Source precedence per call:** `provider_reported` (event attribute `cost.provider_usd`, new, optional) > `estimated` (priced from tokens) > `client_estimate`
  (`cost.estimated_usd` supplied by the SDK user) > `unpriced` (no usable cost: amount 0 and flagged; the UI shows it, totals report how many calls are unpriced).
  Both the reported and the engine estimate are stored on the line when both exist, so a UI can show estimated next to billed.
- **Stored per call** in `cost_calculations` (derived, INV-2): source, pricing version, price components (input, output, cached, request cost), effective total, reported
  amount, estimated amount, retry flag. Rewritten wholesale for a run by the summarizer under the run lock (delete + insert), like spans.
- **The built-in table is illustrative only** (`example-provider` models). Real vendor prices cannot be verified offline and a wrong default price is worse than a visible
  "unpriced"; users add overrides, and a reviewed table update ships as a new `pricing_version` (KI-050).
- `summary` gains `cost_usd` fields while `estimated_cost_usd` is kept (now the effective total); `SUMMARY_VERSION` becomes 2.

## Alternatives
- Compute cost in the SDK: couples every client to a price list and cannot be re-run when prices change; rejected (the SDK may still pass an estimate).
- A separate `compute_cost` outbox job: a second pass over the same events and a window where summary and cost disagree; rejected until cost computation shows up in profiles.
- Store costs only in the summary JSONB: no per-model/agent/day aggregation without scanning JSON; rejected.
- Floats for money: rounding drift in sums; rejected (Decimal in Python, numeric in Postgres).

## Consequences
Positive: explainable, reproducible, rebuildable cost; unpriced calls are visible. Negative: one more derived table written per summarization (O(LLM calls)); a price
change needs an explicit rebuild; the built-in table is of little use until overrides are added.

## Migration implications
Migration 0040 adds `cost_calculations` and `pricing_overrides`. Existing runs keep summary v1 until re-summarized (`rebuild-costs`). The new attribute is optional and additive (schema 1.0 stays).

## Revisit conditions
Invoice reconciliation (§79.3) or budgets (§79.4) need a billed-cost table and a streaming cost counter; revisit then. Multi-currency needs a currency column on lines.

## Amendment 2026-10-12: prompt-size tiers
Status: Accepted

Several vendors price the whole request higher when the prompt is large: OpenAI above 272K input tokens (2x input and cache rates, 1.5x output for the
full request), Claude Haiku 5.5 above 100,000, Gemini 3.1 Pro and 2.5 Pro above 200k. The engine could not express this, so those models were left unpriced (KI-050).

- `PriceEntry.min_prompt_tokens` (optional, default none). An entry with a threshold applies only when the call's prompt tokens, `llm.input_tokens`
  (cached tokens included, as the vendors count them), are **strictly greater** than it. The whole request is then priced at that entry's rates, not
  only the tokens above the line. Entries without a threshold behave exactly as before.
- Selection: among otherwise equal candidates the largest satisfied threshold wins; the threshold ranks right before `valid_from`, so a tier beats its
  base row whatever their start dates. Override, project scope, provider and pattern specificity still rank above it, so a workspace override (which has no
  tier) beats a built-in tier for every prompt size. `PriceBook.find` takes `prompt_tokens`; the engine passes it.
- No migration: tiers exist only in the built-in table. Override rows (`pricing_overrides`) cannot express a tier yet; add `min_prompt_tokens` to
  them if a workspace needs one.
- Reproducibility is unchanged: selection still depends only on the entry set, model, provider, event time and token counts. A pinned `pricing_version` reproduces old
  results, and a new batch of prices is a new version (`2026-10-12`, `valid_from` 2026-10-12) with its source and fetch date on every row.
- Not modelled and documented: batch/flex (50%), fast mode (2x), regional processing (+10%), cache writes, modality prices.

Evidence for each price is in `docs/research/openai-and-tiered-prices-2026-10-12.md`.


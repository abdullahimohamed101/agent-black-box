# ADR-044: Billing Modifiers Are Optional Event Attributes the Engine Prices From Stated Rules

Status: Accepted
Date: 2026-10-12
Amends: ADR-040

## Context
After the prompt-size tiers (ADR-040 amendment, 2026-10-12) the built-in table still billed four things at the standard rate: Batch and Flex
(roughly half price), Fast mode (a per-model premium), regional processing (a 10% uplift) and cache writes (their own price). An event could not
say that a call used any of them, so a batch call was over-estimated and a cache write under-estimated, with nothing in the data to tell. KI-050
asked for the fields and the rules; both vendors' pages state them.

## Decision
**Event contract (additive, schema 1.0 unchanged; `registry.KNOWN_ATTRIBUTES`).** Four optional `llm.*` attributes on `llm.request.completed`:

| Attribute | Type | Meaning |
| --- | --- | --- |
| `llm.service_tier` | string | `standard`, `batch`, `flex`, `fast` or `ultrafast`; any other word is kept as the vendor wrote it and leaves the call unpriced |
| `llm.data_residency` | string | `global`, or a region such as `us` / `eu`; absent means unknown, priced as non-regional |
| `llm.cache_write_tokens` | integer >= 0 | tokens written to the prompt cache; part of `llm.input_tokens`, like cached ones |
| `llm.cache_write_1h_tokens` | integer >= 0 | the part of the writes with the one-hour lifetime (the rest used the default five minutes) |

They are metadata, not content: not in `CONTENT_ATTRIBUTES`, so every reader sees them (INV-5). Absent attributes mean "standard processing, no cache
writes", so every stored event prices exactly as before; no migration, no `SUMMARY_VERSION` change (a run's cost lines are re-derived only by
`rebuild-costs`, as for any price change).

**Where each integration gets them** (verified against the vendors' API reference and guides, 2026-10-12):

| Attribute | OpenAI | Anthropic |
| --- | --- | --- |
| `service_tier` | response `service_tier` (`default` -> standard, `flex`, `priority` / `fast` -> fast; other words kept). Batch API results do not report a tier, so batch calls need `record_usage(service_tier="batch")` | `usage.service_tier` (`standard`, `batch`, `priority`); `usage.speed == "fast"` -> `fast` |
| `data_residency` | the client's base URL host `<region>.api.openai.com` (the response carries no region) | `usage.inference_geo` (`global`, `us`) |
| `cache_write_tokens` | `usage.input_tokens_details.cache_write_tokens` or `usage.prompt_tokens_details.cache_write_tokens` | `usage.cache_creation_input_tokens` |
| `cache_write_1h_tokens` | not reported | `usage.cache_creation.ephemeral_1h_input_tokens` |

The SDK helper is `LlmCall.record_usage(..., cache_write_tokens=, cache_write_1h_tokens=, service_tier=, data_residency=)`; values that are not a short
lowercase word or a non-negative integer are dropped, never guessed. The LangGraph and MCP adapters do not report them (their frameworks do not expose them).

**Price entries.** `PriceEntry` gains `service_tier` (default `standard`; `None` for overrides, which price every tier), `cache_write_per_million`,
`cache_write_1h_per_million`, `regional_multiplier` and `max_prompt_tokens`. Selection adds the tier as a filter (no rank: an any-tier row exists only as an override); among otherwise equal rows the later `pricing_version` wins, so a batch that re-publishes a price supersedes the earlier one without editing it.

**Engine rules** (`CostEngine.calculate`):
- A tier that is not one of the five is **unpriced** with reason `unknown_service_tier`; a known tier the model has no row for (or a prompt above a
  row's `max_prompt_tokens`, which is how a vendor's "-" long-context cell is kept) is `no_price_for_tier`; a regional call whose row has no
  `regional_multiplier` is `no_regional_price`. Never silently standard. Provider-reported and client estimates still win, as before. The reason is on
  the in-memory `CostLine.unpriced_reason` and is not stored (no migration; the line's `source` is `unpriced`).
- Cache-write tokens are bounded by the prompt (`min(writes, input - cached)`), billed at the entry's write price (the 1-hour price falls back to the
  5-minute one, which falls back to the input price), and their cost is reported inside `input_cost`.
- `regional_multiplier` scales input, cached, write and output cost, not the per-request price; overrides are never scaled. `regional_regions` lists the residency words a vendor prices (Anthropic: only `us`, so `eu` is `no_regional_price`); a residency value that is not a string is also unpriced. A tier value that is not a string is `unknown_service_tier`.
- One-hour write tokens are written tokens by definition: `writes = max(cache_write_tokens, cache_write_1h_tokens)`, so a client that sends only the 1-hour count is not billed the input price for them.
- Stacking is as the page states it and nothing more. Anthropic: Batch (x0.5), Fast mode (x2, three Opus models, not with Batch), cache multipliers
  (reads per model, writes 1.25x / 2x the input price) and US-only inference (x1.1, Claude 4.6 and later) all stack. OpenAI: every tier has its own
  stated row (Batch and Flex are not always half, Fast is 2x on the newest models but 2.5x on gpt-5.5 and 1.75x on gpt-4.1); the regional uplift is
  entered for the Standard row of the models whose own page states it, and a regional Batch, Flex, Fast or Ultrafast call is left unpriced because no
  page says how they combine.

**Data.** Built-in version `2026-10-12b` re-publishes every OpenAI and Anthropic Standard row (the same prices, now with cache-write and regional
prices), adds the Batch / Flex / Fast / Ultrafast rows the pages state, the models the earlier batches left out and the Gemini Flash models priced by
modality (text price only). Evidence and the entries left out are in `docs/research/openai-and-tiered-prices-2026-10-12.md`.

## Alternatives rejected
- *Multipliers on the entry* (`batch_multiplier`, `fast_multiplier`): wrong for OpenAI, whose pages state a different figure per model.
- *A service-tier column on the stored cost line*: would need a migration and a rebuild for no decision the line supports; the tier is on the event.
- *Pricing an unknown tier as standard*: a quiet over- or under-statement is worse than a visible unpriced call (the project's first principle).

## Consequences
Events that name a tier, a region or cache writes are priced from the vendor's stated figure or flagged; history is unchanged. The price list API and
the settings page show the tier and prompt-size bounds, because one model now has several rows. Remaining gaps are in KI-050: Gemini Batch, Flex and Priority (1.8x) prices and
caching-storage fees (stated by the page, not entered), OpenAI's generic regional rule for models whose page does not repeat it, OpenAI Priority-like tiers outside the five, regional combinations no page prices, audio and other modality prices.

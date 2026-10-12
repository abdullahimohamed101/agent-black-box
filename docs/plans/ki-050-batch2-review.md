# Adversarial review: KI-050 batch 2 (`feature/ki-050-batch2`, 4 commits on `origin/main`)

Reviewed 2026-10-12 in `abb-worktrees/ki-050b` (`git diff origin/main...HEAD`, 35 files, +1902/-44).
Evidence vocabulary: VERIFIED = run here and observed; PLAUSIBLE = reasoned from one read, not reproduced.
No tracked file was modified; the only new file is this report (uncommitted).

## Verdict

The engine, contract and integrations are sound and the vendor data is accurate (0 price mismatches in a
~110-cell sample, see table). One real defect blocks merge: the branch leaves the API test suite red
(an authz fail-closed test the branch did not update). The rest is data hygiene and coverage.

| Sev | Status | Finding |
| --- | --- | --- |
| P1 | CONFIRMED | `tests/authz/test_content_withheld.py::test_every_text_attribute_is_classified_as_content_or_reviewed_metadata` fails on the branch |
| P2 | PLAUSIBLE | Four OpenAI models that were shut down on 2026-09-28 are entered as current from 2026-10-12 (`davinci-002`, `babbage-002`, `gpt-3.5-turbo-1106`, `gpt-3.5-turbo-instruct`); nine more shut down on 2026-10-23 with no `valid_to` |
| P2 | CONFIRMED (pre-existing, out of diff) | Gemini version `2026-10-09`: `gemini-3.5-flash` is not on the vendor page any more; `gemini-3.5-flash-lite` cache price is unset while the page states $0.03 (cached tokens over-estimated 10x) |
| P3 | CONFIRMED | Regional multiplier is not exercised on `cached_cost` by any test (mutant survives); exact-tier-over-any-tier rank is dead code; 5 of 8 random data mutations survive the suite |
| P3 | CONFIRMED | Anthropic rows apply the 1.1x uplift to any non-`global` residency word (`eu` priced at 1.1x) although the vendor offers only `us` |
| P3 | CONFIRMED | Non-string `llm.service_tier` (raw path only) is priced as standard, against "never silently standard" |
| P3 | CONFIRMED | `cache_write_1h_tokens` without `cache_write_tokens` is silently dropped (bounded by writes = 0) |
| P3 | CONFIRMED | KI-050 says prices are "complete for what the vendors' pages state": Gemini Batch/Flex/Priority and the OpenAI generic regional rule are stated on the pages but not entered (conservative, but the wording overclaims); row count in the request (331) does not match the data (212 batch-2 rows, 265 total) |

## 1. Price data accuracy (VERIFIED against the live vendor pages, 2026-10-12)

Sources read by me, independently of the branch: `developers.openai.com/api/docs/pricing` (browser pane,
every `<table>` of the DOM dumped with "All models" expanded: Standard, Batch, Flex, Fast, Ultrafast,
Cyber, Specialized Standard+Fast), model pages `gpt-6-astra`, `gpt-6-sol`, `gpt-5.6-terra`, `gpt-5.5`,
`gpt-5.4-mini`, `gpt-4.1`, `o3`, `o1`, the OpenAI prompt-caching guide and deprecations page;
`platform.claude.com/docs/en/about-claude/pricing` (raw markdown), `/build-with-claude/fast-mode`,
`/manage-claude/data-residency`, `/api/service-tiers`; `ai.google.dev/gemini-api/docs/pricing` (three fetches).

### Sample compared cell by cell (all match; "-" = vendor shows no price, entered as None / unpriced)

| # | Entry (provider, model, tier, context) | Vendor (in / cached / write / out) | builtin.py | Match |
| --- | --- | --- | --- | --- |
| 1 | openai gpt-6-astra standard short | 10 / 1 / 12.50 / 50 | same | yes |
| 2 | openai gpt-6-astra standard long | 20 / 2 / 25 / 75 | same | yes |
| 3 | openai gpt-6-astra batch short/long | 5/0.5/6.25/25 ; 10/1/12.5/37.5 | same | yes |
| 4 | openai gpt-6-astra fast short/long | 20/2/25/100 ; 40/4/50/150 | same | yes |
| 5 | openai gpt-6-astra ultrafast short/long | 60/6/75/300 ; 120/12/150/450 | same | yes |
| 6 | openai gpt-6.1-sol flex short/long | 1/0.05/1.25/5 ; 2/0.10/2.50/7.50 | same | yes |
| 7 | openai gpt-6.1-sol ultrafast | 12/0.60/15/60 ; 24/1.20/30/90 | same | yes |
| 8 | openai gpt-6-luna batch | 0.05/0.005/0.0625/0.25 ; 0.10/0.01/0.125/0.375 | same | yes |
| 9 | openai gpt-6-sol standard (new) | 2/0.20/2.50/10 ; 4/0.40/5/15 (model page agrees) | same | yes |
| 10 | openai gpt-6-sol fast | 4/0.40/5/20 ; 8/0.80/10/30 | same | yes |
| 11 | openai gpt-5.6-sol standard (promo) | 4/0.40/5/20 ; 8/0.80/10/30, "at least through Nov 21, 2026" | same, date in source | yes |
| 12 | openai gpt-5.6-terra standard (new) | 2/0.20/2.50/12 ; 4/0.40/5/18 (model page: 2/0.2/12, >272K 2x/1.5x, writes 1.25x) | same | yes |
| 13 | openai gpt-5.6-terra fast | 4/0.40/5/24 ; 8/0.80/10/36 | same | yes |
| 14 | openai gpt-5.6-luna batch | 0.10/0.01/0.125/0.60 ; 0.20/0.02/0.25/0.90 | same | yes |
| 15 | openai gpt-5.5 standard | 5/0.50/-/30 ; 10/1/-/45 (model page: 10% regional uplift stated) | same, regional 1.1 | yes |
| 16 | openai gpt-5.5 fast | 12.50/1.25/-/75 ; long "-" | same, `max_prompt_tokens=272000` | yes |
| 17 | openai gpt-5.4 batch | 1.25/0.13/-/7.50 ; 2.50/0.25/-/11.25 | same (0.13 kept as shown) | yes |
| 18 | openai gpt-5.4 fast | 5/0.50/-/30 ; long "-" | same, ceiling | yes |
| 19 | openai gpt-5.4-pro flex | 15/-/-/90 ; 30/-/-/135 | same | yes |
| 20 | openai gpt-5.4-mini standard / batch / fast | 0.75/0.075/4.50 ; 0.375/0.0375/2.25 ; 1.50/0.15/9 (page: 10% uplift) | same, regional 1.1 | yes |
| 21 | openai gpt-5.4-nano batch | 0.10/0.01/0.625 | same | yes |
| 22 | openai gpt-5.2 flex / fast | 0.875/0.0875/7 ; 3.50/0.35/28 | same | yes |
| 23 | openai gpt-5.2-pro batch | 10.50/-/84 (no Flex row) | same, no flex row | yes |
| 24 | openai gpt-5 / gpt-5.1 fast | 2.50/0.25/20 | same | yes |
| 25 | openai gpt-5-mini batch / fast | 0.125/0.0125/1 ; 0.45/0.045/3.60 | same | yes |
| 26 | openai gpt-5-nano flex | 0.025/0.0025/0.20 | same | yes |
| 27 | openai gpt-5-pro batch | 7.50/-/60 | same | yes |
| 28 | openai gpt-4.1 batch / fast | 1/-/4 ; 3.50/0.875/14 | same (batch cached None) | yes |
| 29 | openai gpt-4.1-nano fast | 0.20/0.05/0.80 | same | yes |
| 30 | openai gpt-4o batch / fast | 1.25/-/5 ; 4.25/2.125/17 | same | yes |
| 31 | openai gpt-4o-mini fast | 0.25/0.125/1 | same | yes |
| 32 | openai o4-mini standard / flex / fast | 1.10/0.275/4.40 ; 0.55/0.138/2.20 ; 2/0.50/8 | same | yes |
| 33 | openai o3 standard / batch / flex / fast | 2/0.50/8 ; 1/-/4 ; 1/0.25/4 ; 3.50/0.875/14 (model page's 2nd table = 1/0.25/4) | same | yes |
| 34 | openai o3-mini / o3-pro standard+batch | 1.10/0.55/4.40, 0.55/-/2.20 ; 20/-/80, 10/-/40 | same | yes |
| 35 | openai o1 / o1-pro standard+batch | 15/7.50/60, 7.50/-/30 ; 150/-/600, 75/-/300 | same | yes |
| 36 | openai gpt-4o-2024-05-13 fast | 8.75/-/26.25 | same | yes |
| 37 | openai gpt-4-0613 / gpt-4-turbo-2024-04-09 batch | 15/-/30 ; 5/-/15 | same | yes |
| 38 | openai gpt-3.5-turbo-0125 / -1106 batch | 0.25/-/0.75 ; 1/-/2 | same | yes |
| 39 | openai davinci-002 / babbage-002 std+batch | 2/-/2, 1/-/1 ; 0.40/-/0.40, 0.20/-/0.20 | same | yes (but see P2) |
| 40 | openai gpt-5.3-codex fast (Specialized tab) | 3.50/0.35/28 | same | yes |
| 41 | openai gpt-5.5-pro | pricing page 30/-/180 ; 60/-/270 ; batch long "-" | left out (model page states no rule) | consistent |
| 42 | anthropic Fable 5.1 / Mythos 5.1 | 10 / write 12.50 / 1h 20 / read 0.25 / 50 | same | yes |
| 43 | anthropic Fable 5 / Mythos 5 | 10 / 12.50 / 20 / read 1 / 50 | same | yes |
| 44 | anthropic Opus 5.5 | 4 / 5 / 8 / 0.20 / 20; fast 8 / 40; batch 2 / 10 | same | yes |
| 45 | anthropic Opus 5, 4.8 | 5 / 6.25 / 10 / 0.50 / 25; fast 10 / 50; batch 2.50 / 12.50 | same | yes |
| 46 | anthropic Opus 4.7, 4.6, 4.5 | 5 / 6.25 / 10 / 0.50 / 25; no fast (4.7 errors, 4.6 runs standard) | same, no fast rows | yes |
| 47 | anthropic Sonnet 5.5 / Sonnet 5 | 2 / 2.50 / 4 / 0.10 (0.20 for 5) / 10; batch 1 / 5 | same | yes |
| 48 | anthropic Sonnet 4.6 / 4.5 | 3 / 3.75 / 6 / 0.30 / 15; batch 1.50 / 7.50 | same | yes |
| 49 | anthropic Haiku 5.5 <=100K / >100K | 0.10/0.125/0.20/0.01/0.50 ; 0.50/0.625/1/0.05/2.50; batch 0.05/0.25 ; 0.25/1.25 | same, `min_prompt_tokens=100000` | yes |
| 50 | anthropic Haiku 4.5 | 1 / 1.25 / 2 / 0.10 / 5; batch 0.50 / 2.50 | same | yes |
| 51 | anthropic US-only 1.1x: 4.6 and later only; 4.5 models reject the parameter | regional on Opus 4.6+/Sonnet 4.6+/Haiku 5.5/Fable/Mythos/Opus 5/Sonnet 5, not on Opus 4.5, Sonnet 4.5, Haiku 4.5 | same (script check) | yes |
| 52 | anthropic retired (Opus 4.1, Opus 4, Sonnet 4, Haiku 3.5) | marked retired on the page | left out | consistent |
| 53 | google gemini-3.6-flash | 0.75/3.75/0.075 through 2026-12-31, then 1.50/7.50/0.15 | same, `valid_to`/`valid_from` 2027-01-01 | yes |
| 54 | google gemini-3.1-flash-lite | 0.25 (text/image/video; audio 0.50) / 1.50 / 0.025 | same, text price | yes |
| 55 | google gemini-3-flash-preview | 0.50 (audio 1.00) / 3.00 / 0.05 | same | yes |
| 56 | google gemini-2.5-flash | 0.30 (audio 1.00) / 2.50 / 0.03 | same | yes |
| 57 | google gemini-2.5-flash-lite | 0.10 (audio 0.30) / 0.40 / 0.01 | same | yes |
| 58 | google gemini-3.1-pro-preview, gemini-2.5-pro (version 2026-10-12) | 2/0.20/12 ; >200k 4/0.40/18 ; 1.25/0.125/10 ; >200k 2.50/0.25/15 | same | yes |
| 59 | google gemini-3.8-flash (version 2026-10-09) | 0.75/3.75/0.075, doubled 2027-01-01 | same | yes |
| 60 | google gemini-3.5-flash (version 2026-10-09) | **not on the page** (two fetches, one asked for it by name) | 1.50 / 9.00 / 0.15 | **no source today** |
| 61 | google gemini-3.5-flash-lite (version 2026-10-09) | 0.30 (all modalities) / 2.50 / cache **0.03** | 0.30 / 2.50 / cache **None** (= input price) | **mismatch** (pre-existing) |

Also checked in code (script, VERIFIED): every Anthropic batch row is exactly 0.5x and every fast row exactly
2x its standard row in all five price fields; regional flag identical across a model's tiers; no duplicate
`(provider, pattern, tier, min_prompt, valid_from)` key in batch 2; `source` strings all start with the vendor
URL and carry "read 2026-10-12"; the gpt-5.6-sol promo date is on every tier row of that model.

Vendor rule statements vs the engine's stacking (VERIFIED from the pages):
- Anthropic: cache multipliers "stack with other pricing modifiers, including the Batch API discount and data
  residency"; fast "stacks" with caching and residency, "not available with the Batch API"; residency 1.1x "on all
  token pricing categories" for 4.6+. Engine: batch x0.5, fast x2 (three Opus models), writes 1.25x/2x, reads per
  model, x1.1 on input/cached/write/output. Matches.
- OpenAI: tier rows copied, not derived (Fast is 2.5x on gpt-5.5/5.4, 1.75x on gpt-4.1/o3, 1.7x on gpt-4o: matches
  the tables). Cache writes: "not an additive fee: input tokens use the uncached-input, cached-input, or cache-write
  rate" and `input_tokens_details.cache_write_tokens` is part of `input_tokens` (guide's own example subtracts it).
  Engine folds writes out of plain input at the write rate. Matches.
- OpenAI regional: the pricing page states a generic rule ("10% uplift for models released on or after March 5,
  2026"), and the gpt-6-sol page states "EU data residency is available with Standard, Fast, Flex, and Batch
  processing" plus "adds a 10% premium". The branch enters the uplift only on the Standard row of eight models whose
  page states it; gpt-6-astra (no sentence on its page) and every non-standard tier are unpriced. Conservative, never
  wrong; see P3-5.

### Deprecated / retired models entered as current (P2, PLAUSIBLE: one read of the deprecations page)

`developers.openai.com/api/docs/deprecations` (fetched 2026-10-12) lists shutdowns: `gpt-3.5-turbo-1106`,
`gpt-3.5-turbo-instruct`, `davinci-002`, `babbage-002` on **2026-09-28** (already off), and `gpt-4o-2024-05-13`,
`gpt-4-turbo-2024-04-09`, `gpt-4-0613`, `gpt-3.5-turbo`, `gpt-3.5-turbo-0125`, `o1`, `o1-pro`, `o3-mini`, `o4-mini`
on **2026-10-23**; `gpt-5.1` and `gpt-5.3-codex` on 2027-04-01. The pricing page still lists all of them, which is
why the branch's two reads agreed. Impact: none on figures (a shut-down model gets no new calls), but `builtin.py`
(lines 309-314, 358-361) and the research note call davinci/babbage "legacy Completions API" with no shutdown
note, and the branch's own rule is "no retired model as current". Fix: confirm against the deprecations page (a
second read), then either leave them out of `2026-10-12b` or set `valid_to` to the shutdown date with the date in
`source`; add the 2026-10-23 batch to KI-050's "re-read" reminder.

## 2. Engine correctness (VERIFIED: `scratchpad/edge_checks.py`, 82 cost tests, 19 mutants)

- Boundaries: 272,000 prices at short rates, 272,001 at long (standard, batch, flex); gpt-5.5/5.4 Fast at
  272,001 -> `unpriced` / `no_price_for_tier`; gpt-5.4-pro Flex at 272,001 priced at its long row. Haiku 100,000 vs
  100,001 correct, and the threshold counts cached tokens (all-cached 100,001 prompt pays the upper tier, as the page
  says). Gemini Pro 200,000 / 200,001 correct.
- Cache writes: capped at `input - cached`; 1h capped at writes; both-over-prompt hostile counts cost nothing extra;
  `cache_write_1h_tokens` alone (no `cache_write_tokens`) is ignored (P3-7). OpenAI gpt-5.5 writes (column "-") cost
  the input price; gpt-6.x writes cost their column.
- Tiers: `" BATCH "` -> batch; `priority`, `scale`, `auto`, `default`, 4096-char junk -> `unknown_service_tier`;
  Anthropic `flex`/`ultrafast`, OpenAI gpt-5.5 `ultrafast`, Gemini `flex` -> `no_price_for_tier`; `""`/`"   "` ->
  standard (acceptable: an empty word is "absent").
- Regional: `GLOBAL`/`""` -> non-regional; Anthropic `us` x1.1; Anthropic `eu` also x1.1 (P3-4); Opus 4.5 `us` ->
  `no_regional_price`; OpenAI gpt-6-sol `eu` standard x1.1, batch -> `no_regional_price`, gpt-6-astra -> unpriced.
- Determinism (INV-2): five shuffles of `BUILTIN_ENTRIES` give identical lines; `2026-10-12b` wins the tie with
  `2026-10-12` in either order, and pinning to `2026-10-12` or `2026-10-09` reproduces the old figures exactly
  (standard calls). A pinned old version with a regional or tiered event is `unpriced` (correct: the old version
  never stated those prices). Version tie-break is a string compare: fine for ISO dates with a suffix, but a
  version named e.g. `2026-9-1` would sort wrong; keep zero-padded names (note only).
- Precision/money: 1 cached token on gpt-6-luna batch = 5E-9 (quantised at 9 places, half-even); factor applied
  before quantisation; clamping at 1,000,000 USD holds with 10**40 tokens on every field plus `us` (line
  `clamped=True`, `cost_summary` fine). Hostile raw values (int/list/dict/bool/None tier, list residency, negative,
  float, NaN, string, 10**40 counts) never raise in `calculate` or `cost_summary`. Non-string tier -> standard (P3-6).
- Mutation results (`scratchpad/mut/mutant.py`, 82 tests of `test_cost_modifiers.py`, `test_cost_engine.py`,
  `test_pricing_api.py`):

| Mutant | Killed? |
| --- | --- |
| unknown tier priced as standard | yes (2) |
| drop `pricing_version` from `specificity` | yes (4) |
| ignore `max_prompt_tokens` | yes (2) |
| regional factor not on output | yes (2) |
| **regional factor not on cached input** | **survives** |
| writes billed at input price | yes (3) |
| writes not capped by prompt | yes (1) |
| regional scales overrides | yes (1) |
| tier filter off in `applies` | yes (14) |
| **exact-tier rank in `specificity` removed** | **survives** (dead code: no builtin row has `service_tier=None`, overrides win on origin first) |
| data: o3 fast output 14 -> 15 | survives |
| data: gpt-5.4-nano batch output 0.625 -> 0.60 | survives |
| data: gpt-6-astra ultrafast input 60 -> 50 | survives |
| data: sonnet-4-6 batch output 7.50 -> 7 | killed (half-of-standard test) |
| data: gpt-5.6-terra flex write 1.25 -> 1.5 | survives |
| data: gemini-3-flash-preview output 3 -> 3.5 | survives |
| data: gpt-5.5 fast input 12.5 -> 12 | killed |
| data: o1-pro standard output 600 -> 500 | killed |

## 3. Contract compatibility (VERIFIED)

- Schema stays `1.0`, additive: four optional keys in `event.json`/`event-in.json`; `make schema-check` parts
  (`export --check`, `json2ts` generate) leave the tree clean; `openapi --check` exit 0; web
  `gen:api:check` clean; `schema.d.ts`/`openapi.json` current. Old events validate (test
  `test_an_event_without_the_new_attributes_is_priced_exactly_as_before`, conformance suite 18 passed,
  event-schema 351 passed).
- Registry: `AttrSpec(INTEGER, 0)` x2, `AttrSpec(STRING)` x2; not in `CONTENT_ATTRIBUTES` so viewers see them
  (INV-5, tests `test_the_event_contract_names_the_modifier_attributes`, `test_a_viewer_without_payload_read_sees_...`).
- **P1**: `tests/authz/test_content_withheld.py:44` requires every STRING attribute to be in `CONTENT_ATTRIBUTES` or
  in that file's `REVIEWED_METADATA` (fail closed). The branch added two STRING attributes and did not touch that
  file, so `uv run pytest tests/authz` fails: `assert not {'llm.data_residency', 'llm.service_tier'}`
  (1 failed, 248 passed over the cost/pricing/analytics/authz selection). `make test` / `scripts/quality.sh full`
  would be red on this branch. Fix: add `"llm.service_tier", "llm.data_residency"` to `REVIEWED_METADATA`
  (`apps/api/tests/authz/test_content_withheld.py:29`), which records the INV-5 decision the ADR already states.
- INV-8: vendor words (`default`, `priority`, `speed`, `inference_geo`, host names) are mapped in `integrations/`;
  the schema holds canonical words plus pass-through. `docs/architecture/events.md` row updated; ADR-044 and
  ADR-040 amendment present and indexed.

## 4. Integrations (VERIFIED: openai 78, anthropic 65, sdk 894, conformance 18 passed; INV-4 by reading)

- OpenAI `tier_of`/`cache_writes_of`/`region_of` use `get`/`text`/`count` (never raise; `get` swallows attribute
  errors, handles dicts). `region_of` wraps the host read in try/except and `fullmatch`es `([a-z]{2,8})\.api\.openai\.com`,
  so `evil.api.openai.com.attacker`, a str base_url or a raising `host` yield None; `with_options`/`copy` re-run
  `_apply`, so a derived client on another base_url gets its own region. Streaming: `service_tier` read from each
  chunk or `response.completed`. Zero writes omit the attribute, so old behaviour is byte-identical.
- Anthropic `billing_of` is pure on `get`/`text`/`count`; stream `cache_creation` dicts handled; `speed: "fast"`
  wins over `service_tier`; Opus 4.6's documented `speed: "standard"` fallback records standard. `priority` passes
  through and is left unpriced (the service-tiers page gives burndown rates, no per-token price; commitments are no
  longer sold), as the ADR says.
- SDK `_label` regex (`[a-z0-9][a-z0-9_.-]{0,31}`) drops junk; no new dependency anywhere (`re` only); no payload or
  secret in any new log line (the only logging is the existing `contained()` debug).
- Note: chat-completions `prompt_tokens_details.cache_write_tokens` is not in OpenAI's caching guide (only
  `input_tokens_details`); harmless fallback.

## 5. API / web (VERIFIED)

- No new route; `/v1/pricing` stays `PRICING_READ` in `tests/authz/registry.py:235`; role-matrix tests pass. New
  `PriceOut` fields are plain numbers/words (Decimals serialised as strings, e.g. `"1.1"`).
- Tier column: `{p.service_tier ?? "any"}` plus a template string, React-escaped text only; vitest
  `settings.test.tsx` asserts `"batch, prompts over 272,000"` (567 web tests pass). Not exercised in a real browser
  here (needs a signed-in API): UNVERIFIED (env) for the rendered page, VERIFIED at the DOM level.

## 6. Test quality

- No tautological tests found; `len(BATCH2_ENTRIES) > 150` is weak but harmless. Unpriced reasons: all four are
  asserted at least once (`unknown_service_tier` x3, `no_price_for_tier` x7, `no_regional_price` x5, `no_price` x1).
- Gaps (P3-3): add a case with `cached_input_tokens` + `data_residency` (kills the surviving engine mutant); add a
  golden snapshot of `BATCH2_ENTRIES` (e.g. a sorted `(provider, pattern, tier, min, fields...)` table in a fixture
  file) so an accidental edit to one of 212 rows fails CI instead of only the sampled rows; either delete the
  exact-tier rank in `specificity` or give it a test with a `service_tier=None` builtin row.

## Findings in detail

### P1 (CONFIRMED) authz classification test fails
- Where: `apps/api/tests/authz/test_content_withheld.py:29-57` vs `packages/event-schema/src/abb_event_schema/registry.py:66-70`.
- Repro: `cd apps/api && uv run pytest tests/authz/test_content_withheld.py -k classified` -> `1 failed`.
- Fix: add the two keys to `REVIEWED_METADATA` in the same commit as the schema change (the ADR already makes the
  decision; the test is the record of it).

### P2 (PLAUSIBLE) shut-down OpenAI models entered as current
- Where: `apps/api/src/abb_api/cost/builtin.py:309-314, 358-361` (`davinci-002`, `babbage-002`,
  `gpt-3.5-turbo-1106`, `gpt-3.5-turbo-instruct`), plus the nine models shut down 2026-10-23.
- Repro: fetch `developers.openai.com/api/docs/deprecations` and compare.
- Fix: as above (second read, then `valid_to` or omit; note in the research doc and KI-050).

### P2 (CONFIRMED, pre-existing, version 2026-10-09, not in this diff)
- `builtin.py:104-105`: `gemini-3.5-flash` has no row on the vendor page today; `gemini-3.5-flash-lite` cache price
  is None (billed at $0.30) while the page states $0.03. A heavily cached 3.5-flash-lite call is over-estimated
  10x on its cached part. Fix in the next Gemini batch: new version with cache 0.03 and either a `valid_to` on
  3.5-flash or a documented source for it.

### P3 (CONFIRMED) smaller items
1. `engine.py:129`: `regional = residency not in (None, "global")` prices any word at the vendor's US uplift. For
   Anthropic only `us` exists; consider `regional_multiplier` keyed to the residency words the page states, or at
   least document that any non-global word is treated as "the regional price".
2. `engine.py:80-83, 127`: `_word` returns None for non-strings so a hostile non-string tier is standard. Only
   reachable past validation (ingestion types STRING), so low; `unknown_service_tier` would be the consistent answer.
3. `engine.py:182-183`: a client that sends only `cache_write_1h_tokens` is silently under-estimated; either derive
   `writes = max(writes, writes_1h)` or state in the SDK docstring that 1h requires the total.
4. KI-050 row / ADR wording "complete for what the vendors' pages state": the Gemini page states Batch, Flex and
   Priority (1.8x) prices per model, and the OpenAI pricing page states the generic post-2026-03-05 regional rule;
   both are deliberately left unpriced. Reword to "entered where a page states a figure for that model and tier".
5. The request text speaks of 331 rows; `len(BATCH2_ENTRIES)` is 212 (`BUILTIN_ENTRIES` 265). No doc claims 331, but
   the count in the PR description should match.
6. `test_cost_modifiers.py` has no case combining `cached_input_tokens` with `data_residency`; `specificity`'s
   exact-tier rank is untestable with current data.

## Commands run (all VERIFIED here)

```
uv run pytest tests/test_cost_engine.py tests/test_cost_modifiers.py tests/test_cost_derivation.py \
  tests/test_cost_retries.py tests/test_pricing_api.py tests/test_analytics_api.py tests/test_analytics_rollup.py tests/authz
  -> 1 failed, 248 passed (test_content_withheld classification)
packages/event-schema: 351 passed; packages/sdk-python: 894 passed; integrations/openai: 78; anthropic: 65; conformance: 18
abb_event_schema.export --check: 0; pnpm --filter @abb/event-schema generate: tree clean; abb_api.openapi --check: 0
pnpm --filter @abb/web gen:api:check: clean; pnpm --filter @abb/web test --run settings: 567 passed
ruff check / ruff format --check / mypy on apps/api, integrations/openai, integrations/anthropic, packages/sdk-python,
  packages/event-schema: all clean
scratchpad/edge_checks.py (boundaries, hostile values, determinism); scratchpad/mut/mutant.py x18 mutants
Test DB abb_test_ki050r created and dropped.
```

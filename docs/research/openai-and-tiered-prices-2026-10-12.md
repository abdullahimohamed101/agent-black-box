# OpenAI and prompt-tiered prices: verification table (read 2026-10-12)

Input to built-in price version `2026-10-12` (`apps/api/src/abb_api/cost/builtin.py`, ADR-040 amendment, KI-050). The research note of
2026-10-09 (`llm-model-reference-2026-10-09.md`) was one unconfirmed read and does not count as a second source.

**Method.** Each figure was read twice on 2026-10-12 through tools that return a model-written transcription of the page, from
independent pages:

- OpenAI: read 1 is the pricing page `developers.openai.com/api/docs/pricing` (Standard tab, "All models" expanded in the browser pane, plus a
  separate fetch of the page); read 2 is each model's own page `/api/docs/models/<id>`. Where a model has no page (HTTP 404), read 2 is a second
  fetch of the pricing page with a different question, which is weaker and marked "pricing page x2" below.
- Haiku 5.5: `platform.claude.com/docs/en/about-claude/pricing` (raw table rows) and `claude.com/pricing`.
- Gemini: two fetches of `ai.google.dev/gemini-api/docs/pricing` with different questions (the page is one source).

USD per 1M tokens, input / cached input / output. A "long" row prices the whole request once the prompt (cached tokens included) is over the
threshold. "n/o": no cached price offered, so cached tokens are billed at the input price. OpenAI's threshold wording: "Prompts with more than
272K input tokens are priced at 2x input and cache rates and 1.5x output for the full request" (the model pages of gpt-6-astra, gpt-6.1-sol,
gpt-6-luna; gpt-5.5, gpt-5.4, gpt-5.4-pro, gpt-5.6-sol say "2x input and 1.5x output for the full request/session"). We read 272K as 272,000,
which matches gpt-5.3-codex's stated maximum input of 272,000.

| Model | Short context | Long context (> 272,000) | Reads agree | Entered |
| --- | --- | --- | --- | --- |
| gpt-6-astra | 10 / 1 / 50 | 20 / 2 / 75 | yes | yes |
| gpt-6.1-sol | 2 / 0.10 / 10 | 4 / 0.20 / 15 | yes | yes |
| gpt-6-luna | 0.10 / 0.01 / 0.50 | 0.20 / 0.02 / 0.75 | yes | yes |
| gpt-5.6-sol | 4 / 0.40 / 20 | 8 / 0.80 / 30 | yes; promotional, "at least through 2026-11-21" | yes (date in source) |
| gpt-5.5 | 5 / 0.50 / 30 | 10 / 1 / 45 | yes | yes |
| gpt-5.4 | 2.50 / 0.25 / 15 | 5 / 0.50 / 22.50 | yes | yes |
| gpt-5.4-pro | 30 / n/o / 180 | 60 / n/o / 270 | yes (threshold on the model page) | yes |
| gpt-5.5-pro | 30 / n/o / 180 | 60 / n/o / 270 on the pricing page | **no**: the model page states no long-context rule | **no** |
| gpt-5.4-mini | 0.75 / 0.075 / 4.50 | none | yes | yes |
| gpt-5.4-nano | 0.20 / 0.02 / 1.25 | none | yes | yes |
| gpt-5.3-codex | 1.75 / 0.175 / 14 | none shown | yes | yes |
| gpt-5.2 | 1.75 / 0.175 / 14 | none | yes | yes |
| gpt-5.2-pro | 21 / n/o / 168 | none | yes | yes |
| gpt-5.1, gpt-5 | 1.25 / 0.125 / 10 | none | yes | yes |
| gpt-5-mini | 0.25 / 0.025 / 2 | none | yes | yes |
| gpt-5-nano | 0.05 / 0.005 / 0.40 | none | yes | yes |
| gpt-5-pro | 15 / n/o / 120 | none | yes | yes |
| gpt-4.1 | 2 / 0.50 / 8 | none | yes | yes |
| gpt-4.1-mini | 0.40 / 0.10 / 1.60 | none | yes | yes |
| gpt-4.1-nano | 0.10 / 0.025 / 0.40 | none | yes | yes |
| gpt-4o | 2.50 / 1.25 / 10 | none | yes | yes |
| gpt-4o-mini | 0.15 / 0.075 / 0.60 | none | yes | yes |
| gpt-3.5-turbo | 0.50 / n/o / 1.50 | none | yes | yes |
| gpt-3.5-turbo-instruct | 1.50 / n/o / 2 | none | yes | yes |
| gpt-4o-2024-05-13 | 5 / n/o / 15 | none | pricing page x2 | yes |
| gpt-4-turbo-2024-04-09 | 10 / n/o / 30 | none | pricing page x2 | yes |
| gpt-4-0613 | 30 / n/o / 60 | none | pricing page x2 | yes |
| gpt-3.5-turbo-0125 | 0.50 / n/o / 1.50 | none | pricing page x2 | yes |
| gpt-3.5-turbo-1106 | 1 / n/o / 2 | none | pricing page x2 | yes |
| Claude Haiku 5.5 | 0.10 / 0.01 / 0.50 | 0.50 / 0.05 / 2.50, prompts over 100,000 tokens | yes (both pages) | yes |
| gemini-3.1-pro-preview, `-customtools` | 2 / 0.20 / 12 | 4 / 0.40 / 18, prompts over 200k | yes (two fetches) | yes |
| gemini-2.5-pro | 1.25 / 0.125 / 10 | 2.50 / 0.25 / 15, prompts over 200k | yes (two fetches) | yes |

Haiku 5.5's page says a request's prompt length counts all input tokens including cache reads and writes, and that each request is priced on
its own. Gemini's "200k" is read as 200,000. Gemini's output price includes thinking tokens.

## Left out, and why

- `chat-latest`: a moving alias. `gpt-5.6-cyber`, `gpt-5.5-cyber`, `gpt-rosalind-*`: restricted access.
- `gpt-5.5-pro`: only the pricing page shows a long-context row; its model page states no threshold or multiplier. Priced flat it would undercharge
  long prompts, so it stays unpriced until the page states the rule.
- `gpt-6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, the o-series (`o1`..`o4-mini`), `davinci-002`, `babbage-002`: listed on the pricing page but outside the
  requested scope; a later batch can add them.
- Image, audio, realtime, transcription and fine-tuning models, tool fees (web search, containers, file search).
- Gemini 3.1 Flash-Lite and the 2.5 Flash family: flat in size but priced by modality (audio input costs more than text), which the engine cannot express.
- Dated snapshots not listed on the pricing page (for example `gpt-5-2025-08-07`) stay unpriced: entries are exact model IDs.

(The "Not modelled" list of the first batch is superseded by the second batch below, except modality prices.)

# Second batch, version `2026-10-12b` (read 2026-10-12)

Input to ADR-044 and `BATCH2_ENTRIES` in `builtin.py`. Same rule: every figure read twice from independent routes, entered only if they agree.

**Method.**
- OpenAI tier tables: read 1 is the pricing page in the browser pane, each of the Standard, Batch, Flex, Fast and Ultrafast tabs (text of the live DOM,
  "All models" expanded; the Specialized table's Fast tab separately). Read 2 is a fetch of the same page asked for the Batch, Flex, Fast and Ultrafast
  tables as text. The two were compared cell by cell for all 119 flagship rows (zero differences). The model pages of the gpt-6 family list the tiers too
  and agree; the other model pages list the Standard price only (read 2 for Standard).
- OpenAI model pages `/api/docs/models/<id>` for the new models, the long-context sentence and the regional-processing sentence.
- Anthropic: `platform.claude.com/docs/en/about-claude/pricing` as the rendered table in the browser pane and as raw markdown through a fetch (they agree),
  plus the Fast mode and Data residency pages.
- Gemini: two fetches of the pricing page with different questions (the browser pane's text omits the price tables, so it was not a third read).

## New models added (Standard, USD per 1M tokens, input / cached / output; long = prompts over 272,000)

| Model | Short | Long | Notes |
| --- | --- | --- | --- |
| gpt-6-sol | 2 / 0.20 / 10 | 4 / 0.40 / 15 | write 2.50 / 5; page states the 272K rule; regional uplift stated |
| gpt-5.6-terra | 2 / 0.20 / 12 | 4 / 0.40 / 18 | page: "2x input and 1.5x output"; writes 1.25x input; no regional sentence |
| gpt-5.6-luna | 0.20 / 0.02 / 1.20 | 0.40 / 0.04 / 1.80 | as above |
| o4-mini | 1.10 / 0.275 / 4.40 | none | model page has a second unlabelled table (1 / 0.25 / 4 for o3): the Flex/Batch rate, not used |
| o3 | 2 / 0.50 / 8 | none | |
| o3-mini | 1.10 / 0.55 / 4.40 | none | |
| o3-pro | 20 / n/o / 80 | none | |
| o1 | 15 / 7.50 / 60 | none | |
| o1-pro | 150 / n/o / 600 | none | |
| davinci-002 | 2 / n/o / 2 | none | legacy Completions API |
| babbage-002 | 0.40 / n/o / 0.40 | none | legacy Completions API |
| gpt-5.3-codex Fast | 3.50 / 0.35 / 28 | none | Specialized table, Fast tab (Standard was in batch 1) |
| Claude Fable 5, Mythos 5, Mythos 5.1 | 10 / 50, cache read 1 (0.25 for 5.1) | none | Mythos is limited availability |
| Claude Opus 5, 4.8, 4.7, 4.6, 4.5 | 5 / 25, read 0.50 | none | |
| Claude Sonnet 5 | 2 / 10, read 0.20 | none | introductory price made standard (page note 3) |
| Claude Sonnet 4.6, 4.5 | 3 / 15, read 0.30 | none | |
| Claude Haiku 4.5 | 1 / 5, read 0.10 | none | |
| gemini-3.6-flash | 0.75 / 3.75 / 0.075 through 2026-12-31, then 1.50 / 7.50 / 0.15 | none | one price, no modality split |
| gemini-3.1-flash-lite | 0.25 / 1.50 / 0.025 | none | text, image and video; audio input 0.50 not modelled |
| gemini-3-flash-preview | 0.50 / 3.00 / 0.05 | none | audio input 1.00 not modelled |
| gemini-2.5-flash | 0.30 / 2.50 / 0.03 | none | audio input 1.00 not modelled |
| gemini-2.5-flash-lite | 0.10 / 0.40 / 0.01 | none | audio input 0.30 not modelled |

Both reads agreed on every row above. The Gemini rows under-estimate a call whose input is audio, because the event has no modality; nothing else about them is guessed.

## Modifiers, as the pages state them

| Modifier | Rule entered | Source and stacking |
| --- | --- | --- |
| Anthropic Batch | every price x0.5 (the Batch table equals half the base table for every model, checked in a test) | stacks with caching and data residency |
| Anthropic Fast mode | Opus 5.5 8 / 40, Opus 5 and 4.8 10 / 50 (2x); only these three | "stacks with other pricing modifiers"; not with Batch |
| Anthropic cache writes | 5-minute 1.25x, 1-hour 2x the input price; reads per model (0.1x, 0.05x Opus/Sonnet 5.5, 0.025x Fable/Mythos 5.1) | stack with Batch, Fast and residency |
| Anthropic US-only inference | x1.1 on every category, Claude 4.6 and later (Opus 4.6+, Sonnet 4.6+, Haiku 5.5 incl. its upper prompt tier, Fable, Mythos); Opus 4.5, Sonnet 4.5, Haiku 4.5 reject the parameter | stacks with all of the above |
| OpenAI Batch / Flex | explicit rows per model; usually half the Standard price, but not always (cached input shown rounded: gpt-5.4 0.13, Flex o4-mini 0.138; Batch has no cached price for gpt-4.1, 4o and o-series) | rows copied, not derived; stored as shown |
| OpenAI Fast | explicit rows: 2x on gpt-6 / 5.6 / 5.4-mini / 5.2 / 5.1 / 5 / 5-mini, 2.5x gpt-5.5 and gpt-5.4, 1.75x gpt-4.1 and o3, 1.7x gpt-4o | gpt-5.5 and gpt-5.4 Fast show "-" above 272K: unpriced there |
| OpenAI Ultrafast | gpt-6-astra (6x) and gpt-6.1-sol (6x) only | the response's `service_tier` list does not name it; set through the SDK helper |
| OpenAI cache writes | column per row, "1.25x the uncached input rate" on gpt-5.6 and later; "-" (no separate charge) for gpt-5.5 and earlier | usage field `cache_write_tokens` |
| OpenAI regional processing | 10% uplift, entered on the Standard row of gpt-6.1-sol, gpt-6-sol, gpt-6-luna, gpt-5.5, gpt-5.4, gpt-5.4-pro, gpt-5.4-mini, gpt-5.4-nano (their pages state it) | no page says how it combines with other tiers; those calls are unpriced |

## Left out in the second batch, and why

- `gpt-5.5-pro`: its model page now states no long-context threshold or multiplier (it states "does not offer a cached input discount" and the 10% regional uplift
  only), while the pricing page shows a long row. Same disagreement as batch 1, so it stays unpriced.
- `gpt-5-search-api`, embeddings, moderation, TTS, transcription, image, realtime and audio models, fine-tuned models: not token-priced text models, or priced by modality.
- Claude Opus 4.1, Opus 4, Sonnet 4 and Haiku 3.5: marked retired on the page. Claude Mythos Preview: no row.
- Regional uplift for gpt-6-astra, the gpt-5.6 family, gpt-5.3-codex and every older OpenAI model: their pages do not state it, so a regional call to them is unpriced.
- Gemini batch (half price), Gemini audio input and context-cache storage fees: no event field for modality; batch needs a Gemini integration.
- Anthropic `priority` service tier (a capacity commitment, no per-token figure on the pricing page) and OpenAI `scale`/`auto`: unpriced with a reason.
- OpenAI cached prices shown rounded by the vendor (0.13, 0.138) are kept as shown.

## Review follow-up (read 2026-10-12)

- **Shutdowns.** `developers.openai.com/api/docs/deprecations`, read twice (fetch and the page text): `davinci-002`, `babbage-002`, `gpt-3.5-turbo-1106` and
  `gpt-3.5-turbo-instruct` were shut down on 2026-09-28; they are not entered in `2026-10-12b` (the first two of the earlier batch's rows cannot be edited and remain).
  `gpt-3.5-turbo`, `gpt-3.5-turbo-0125`, `gpt-4o-2024-05-13`, `gpt-4-turbo-2024-04-09`, `gpt-4-0613`, `o1`, `o1-pro`, `o3-mini` and `o4-mini` shut down on 2026-10-23;
  their `2026-10-12b` rows have `valid_to` 2026-10-23 and say so in `source`. Dated snapshots on the page (for example `o3-2025-04-16`, 2026-12-11) are not our IDs.
- **Gemini.** Read twice (fetch and the page's own tables): `gemini-3.5-flash-lite` caches at $0.03 for every modality, so `2026-10-12b` carries a corrected row from 2026-10-09
  (the published `2026-10-09` row left the cache price unset). A plain `gemini-3.5-flash` no longer appears on the page (both reads); its published row stays, nothing can end-date it.
- **Row counts.** 206 batch-2 rows and 259 built-in rows (before the review: 212 and 265); `apps/api/tests/golden/batch2_entries.txt` pins every batch-2 row.


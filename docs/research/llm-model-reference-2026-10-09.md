# LLM model reference: Claude, Gemini, OpenAI (read 2026-10-09)

Prices, context windows and limits for the three vendors Agent Black Box is most likely to record. This is
research input for the built-in price table (`apps/api/src/abb_api/cost/builtin.py`, ADR-040, KI-050), not a
source of truth: prices change, and the table only ever takes rows from a vendor's own page.

**How each figure was obtained.** Every page was fetched on 2026-10-09 through a tool that returns a model-written
summary or transcription of the page, not raw HTML. Confidence is therefore stated per section:

| Section | Source | Confidence |
| --- | --- | --- |
| Anthropic prices and specs | `platform.claude.com/docs/en/about-claude/pricing` and `/models/overview` (tables transcribed verbatim); prices agree with `claude.com/pricing` | High (two pages agree) |
| Gemini prices | `ai.google.dev/gemini-api/docs/pricing`, read twice; three text models agreed exactly, Flash-Lite's cache price did not | Medium; only the agreeing rows are in the price table |
| Gemini specs | one page per model | Medium (one read each) |
| OpenAI prices and specs | `developers.openai.com/api/docs/pricing` and `/models` (redirect from `platform.openai.com`), one read each | Medium-low: not cross-checked, and the pricing page marks some models with long-context tiers that the summary did not attribute to rows |

Nothing here was checked against an invoice. Do not quote these as authoritative; re-read the vendor page before relying on a figure.

All prices are USD per million tokens (MTok), standard (not batch) processing, unless stated.

## Anthropic Claude

Context window 1M tokens and max output 128K for every current model; adaptive thinking; text and image input, text output.
Tokenizer note: Claude 4.7 and later use a newer tokenizer that produces about 30% more tokens for the same text, so a per-token
price is not comparable across older and newer Claude generations.

| Model | API ID | Input | Output | Cache hit | 5 min cache write | 1 h cache write | Batch in / out |
| --- | --- | --: | --: | --: | --: | --: | --- |
| Fable 5.1 | `claude-fable-5-1` | 10 | 50 | 0.25 | 12.50 | 20 | 5 / 25 |
| Opus 5.5 | `claude-opus-5-5` | 4 | 20 | 0.20 | 5 | 8 | 2 / 10 |
| Sonnet 5.5 | `claude-sonnet-5-5` | 2 | 10 | 0.10 | 2.50 | 4 | 1 / 5 |
| Haiku 5.5, prompt up to 100K | `claude-haiku-5-5` | 0.10 | 0.50 | 0.01 | 0.125 | 0.20 | 0.05 / 0.25 |
| Haiku 5.5, prompt over 100K | `claude-haiku-5-5` | 0.50 | 2.50 | 0.05 | 0.625 | 1 | 0.25 / 1.25 |
| Fable 5 (legacy) | | 10 | 50 | 1 | 12.50 | 20 | 5 / 25 |
| Opus 5, 4.8, 4.7, 4.6, 4.5 (legacy) | | 5 | 25 | 0.50 | 6.25 | 10 | 2.50 / 12.50 |
| Sonnet 5 (legacy) | | 2 | 10 | 0.20 | 2.50 | 4 | 1 / 5 |
| Sonnet 4.6, 4.5 (legacy) | | 3 | 15 | 0.30 | 3.75 | 6 | 1.50 / 7.50 |
| Haiku 4.5 (legacy) | `claude-haiku-4-5-20251001` | 1 | 5 | 0.10 | 1.25 | 2 | 0.50 / 2.50 |
| Opus 4.1, Opus 4 (retired except on Bedrock and Google Cloud) | | 15 | 75 | 1.50 | 18.75 | 30 | 7.50 / 37.50 |

- Mythos 5 and 5.1 are limited-availability and priced as Fable.
- Cache hit multipliers: 0.1x input normally, 0.05x on Opus 5.5 and Sonnet 5.5, 0.025x on Fable 5.1.
- Modifiers that stack: Batch API 50% off; US-only inference (`inference_geo: "us"`) 1.1x on all categories (Claude 4.6 and later);
  fast mode (Opus 5.5 only of the current line): $8 input / $40 output.
- Only Haiku 5.5 is priced by prompt length (over 100K input tokens, counting cache reads and writes). The 1M window is otherwise standard pricing.
- Reliable knowledge cutoff: June 2026 for the four current models. Announced retirement: not before 2027-09-01 (Fable 5.1), 2027-09-22 (Opus 5.5),
  2027-09-28 (Sonnet 5.5), 2027-10-07 (Haiku 5.5).
- Server tools: web search $10 per 1,000 searches; web fetch free beyond tokens; code execution free with search or fetch, otherwise $0.05 per container-hour
  after 1,550 free hours a month. Managed Agents add $0.08 per session-hour.

## Google Gemini

All listed models: input limit 1,048,576 tokens, output limit 65,536, input text, image, video, audio and PDF, output text.
Capabilities: thinking, caching, code execution, function calling, structured outputs, search grounding, Google Maps grounding, file search, computer use (preview).
Batch API, Flex and Priority inference are supported on the models whose pages say so. Knowledge cutoff is stated only for 3.8 Flash (September 2026).

| Model | ID | Input | Output | Context caching | Notes |
| --- | --- | --: | --: | --: | --- |
| 3.8 Flash | `gemini-3.8-flash` | 0.75 | 3.75 | 0.075 | **doubles on 2027-01-01** to 1.50 / 7.50 / 0.15; stable |
| 3.7 Flash, 3.6 Flash | | 0.75 | 3.75 | 0.075 | "same as 3.8 Flash", one read only |
| 3.5 Flash | `gemini-3.5-flash` | 1.50 | 9.00 | 0.15 | stable |
| 3.5 Flash-Lite | `gemini-3.5-flash-lite` | 0.30 | 2.50 | **unclear** ($0.03 in one read, "not available" in the other) | |
| 3.1 Flash-Lite | | 0.25 text/image/video, 0.50 audio | 1.50 | 0.025 / 0.05 | priced by modality |
| 3.1 Pro (preview) | `gemini-3.1-pro-preview` | 2.00 up to 200K prompt, 4.00 above | 12.00 / 18.00 | 0.20 / 0.40 | preview; a `-customtools` variant exists |
| 2.5 Pro | `gemini-2.5-pro` | 1.25 up to 200K, 2.50 above | 10 / 15 | | legacy, limited access |
| 2.5 Flash | `gemini-2.5-flash` | 0.30 text/image/video, 1.00 audio | 2.50 | | legacy |
| 2.5 Flash-Lite | `gemini-2.5-flash-lite` | 0.10 text/image/video, 0.30 audio | 0.40 | | legacy |

- Specialist models (live/audio, text-to-speech, image generation, video, music, embeddings, robotics) have their own per-modality, per-second or per-image pricing and are not
  summarised here.
- **Free tier.** Free of charge for the main models, with rate limits, in exchange for "content used to improve our products". The paid tier states content is not used that way.
  Free-tier usage costs nothing, so a recorded agent on the free tier needs a $0 price override to show $0 instead of "unpriced".
- Batch is about 50% off.

## OpenAI

One read of the pricing page. The page says some models have a **long-context tier above 272K input tokens**; the summary did not say which rows, so the
figures below are the short-context ("up to 272K") rate where a tier exists. Batch is 50% off. Priority processing: "fast" 2x, "ultrafast" 3x, "flex" at custom rates for some models.

Context window and limits, from the models page (flagship line only): GPT-6 Astra, GPT-6.1 Sol and GPT-6 Luna have a 1.05M-token context window and 128K
max output, text and image input, text output, with function calling, web search, file search and computer use. Knowledge cutoff: 2026-04-30 (Astra, Sol) and
2026-05-18 (Luna). Specs for the older models were not returned by the page.

| Model | Input | Cached input | Output |
| --- | --: | --: | --: |
| gpt-6-astra | 10.00 | 1.00 | 50.00 |
| gpt-6.1-sol | 2.00 | 0.10 | 10.00 |
| gpt-6-sol | 2.00 | 0.20 | 10.00 |
| gpt-6-luna | 0.10 | 0.01 | 0.50 |
| gpt-5.6-sol | 4.00 | 0.40 | 20.00 |
| gpt-5.6-terra | 2.00 | 0.20 | 12.00 |
| gpt-5.6-luna | 0.20 | 0.02 | 1.20 |
| gpt-5.5 | 5.00 | 0.50 | 30.00 |
| gpt-5.5-pro | 30.00 | none | 180.00 |
| gpt-5.4 / -mini / -nano | 2.50 / 0.75 / 0.20 | 0.25 / 0.075 / 0.02 | 15.00 / 4.50 / 1.25 |
| gpt-5.4-pro | 30.00 | none | 180.00 |
| gpt-5.2 / gpt-5.2-pro | 1.75 / 21.00 | 0.175 / none | 14.00 / 168.00 |
| gpt-5.1, gpt-5 | 1.25 | 0.125 | 10.00 |
| gpt-5-mini / gpt-5-nano | 0.25 / 0.05 | 0.025 / 0.005 | 2.00 / 0.40 |
| gpt-5-pro | 15.00 | none | 120.00 |
| gpt-4.1 / -mini / -nano | 2.00 / 0.40 / 0.10 | 0.50 / 0.10 / 0.025 | 8.00 / 1.60 / 0.40 |
| gpt-4o / gpt-4o-mini | 2.50 / 0.15 | 1.25 / 0.075 | 10.00 / 0.60 |
| gpt-4o-2024-05-13 | 5.00 | none | 15.00 |
| o1 / o1-pro | 15.00 / 150.00 | 7.50 / none | 60.00 / 600.00 |
| o3 / o3-pro | 2.00 / 20.00 | 0.50 / none | 8.00 / 80.00 |
| o4-mini, o3-mini | 1.10 | 0.275, 0.55 | 4.40 |
| gpt-4-turbo-2024-04-09 | 10.00 | none | 30.00 |
| gpt-4-0613 | 30.00 | none | 60.00 |
| gpt-3.5-turbo, -0125 | 0.50 | none | 1.50 |
| gpt-3.5-turbo-1106 / -instruct | 1.00 / 1.50 | none | 2.00 |

Other OpenAI models listed without prices here: image generation (GPT-Image-2.5 Sunburst, Flare), realtime and voice (GPT-Live-1, GPT-Realtime-2.1 and others),
speech and transcription, and two restricted models (GPT-5.6 Cyber, GPT-Rosalind).

## What the built-in price table covers today

| Vendor | In the table | Not in the table, and why |
| --- | --- | --- |
| Anthropic | Fable 5.1, Opus 5.5, Sonnet 5.5 | Haiku 5.5 (prompt-length tier; the engine has no tiers), legacy models, batch, fast mode, US-only multiplier, cache writes |
| Google | 3.8 Flash (with the 2027-01-01 change), 3.5 Flash, 3.5 Flash-Lite (no cache price) | 3.1 Pro and 3.1 Flash-Lite (tiers by prompt size and modality), 2.5 family, specialist models |
| OpenAI | nothing | Single unconfirmed read, long-context tiers unattributed. Adding it needs a second read of the official page, and the engine needs long-context tiers for any row that has one |

## Engine limits these prices run into (for KI-050 follow-up)

1. **Tiered prices.** The engine selects one price per model and date. Haiku 5.5, Gemini 3.1 Pro, Gemini 2.5 Pro and some OpenAI models change price by prompt size.
2. **Modality prices.** Gemini audio input costs more than text on several models; the engine has one input price.
3. **Multipliers** (batch, priority, US-only inference, fast mode) are not represented, so those calls are priced at the standard rate.
4. **Cache writes** are not priced separately from input.
5. Dated changes (Gemini 3.8 Flash, 2027-01-01) work today through `valid_from` and `valid_to`.

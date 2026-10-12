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

## Not modelled

Batch and Flex (50%), Fast mode (2x), regional processing / data residency (+10% for models released on or after 2026-03-05) and FedRAMP (+10%),
cache writes (OpenAI lists a cache-write price for the newest models), and modality prices. Calls made that way are priced at the standard rate.

# Idea: a hub for all LLM usage

Status: **idea, not planned and not in the roadmap.** Recorded 2026-10-10 at the user's request; revisit when the current roadmap is
nearing completion (after Phases 9-16 and 19-20 are scoped or done), not before.

## The goal as the user described it

One place that shows how all of a person's LLM use behaves: Claude, ChatGPT, Gemini and the rest, with calls, tokens, cost and (where
possible) what the tool did. This is wider than the product today (see `README.md`): an **agent flight recorder** that only sees agents which
report on themselves through the SDK or an adapter (`packages/sdk-python`, `integrations/*`). Neither the spec nor the roadmap describes a hub, a gateway
or account-level collection; the only related line is the spec's "future feature" to import provider invoices and compare estimated with billed
spend (docs/architecture/agent-black-box-spec.md, near the cost section).

## Ways in, and what each can and cannot see

| Approach | Captures | Limits |
| --- | --- | --- |
| **LLM gateway** (a proxy in front of the vendor APIs) | Every model call from any script, app or tool that lets you set the API address: model, tokens, latency, cost | Tools that cannot change the API address are not covered; no file edits or shell commands; the chat apps are not covered |
| **Tool hooks / plugins** (e.g. Claude Code hooks) | Tool calls, file edits, shell commands of closed tools that expose hooks | One integration per tool; what the hooks expose is UNVERIFIED |
| **Usage and invoice import** | Totals per day, model and key from each vendor's usage reports; billed versus estimated spend | No step-level detail; likely needs admin-level vendor keys (UNVERIFIED) |
| **Chat apps** (ChatGPT, claude.ai, Gemini app) | Only manual import of exported conversations | No live connection exists |

## Feasibility notes (engineering judgement, nothing built or tested)

- A gateway fits the architecture: it is another source of the same canonical events, so storage, UI, analytics and the Phase 15 roles are reused,
  and the adapters already know the vendors' request and response shapes. Roughly one phase of work, comparable to Phase 8. It needs an ADR.
- Hard parts: streaming replies must pass through live while the final result is assembled for the record; if the proxy is down every tool behind
  it stops, so it needs a fail-open or bypass design (INV-4 spirit); it sees vendor keys and prompts, so keys must be passed through and never stored,
  and it needs the Phase 19 hardening (login throttling KI-019, quotas KI-018, retention KI-072) before anyone relies on it; vendor API shapes and
  prices drift (see `docs/research/llm-model-reference-2026-10-09.md`).
- The price table and cost engine already cover part of this (Claude, three Gemini models); OpenAI and tiered prices are open (KI-050).

## Recommended first step when revisited

A spike, not a phase: a proof-of-concept gateway for **one** vendor (Anthropic) that handles streaming and records to the existing API, to learn
whether streaming, latency and fail-open behave and whether the calls look right in the UI. Then decide scope. A Claude Code hook integration is the
cheapest way to see coding-tool sessions and could be the second step.

## Decision needed then

Whether the product stays an agent flight recorder (its current definition) or widens to a hub. That is a product decision and changes the
roadmap order (Phases 9-14 analyse and control agents; a hub collects more sources).

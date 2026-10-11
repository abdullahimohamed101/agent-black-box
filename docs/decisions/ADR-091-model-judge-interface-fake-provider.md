# ADR-091: Model judges behind a one-method provider interface; a deterministic fake is the only shipped provider; judges are never ground truth; the run is untrusted data

Status: Proposed
Date: 2026-10-11
Phase: 11

## Context
Spec §85.4: capture judge model, prompt template version, cost and raw score metadata; judges are not ground truth. No vendor
credentials exist in this project; INV-5 forbids sending captured content anywhere by default; the API must not depend on a vendor SDK; every label
in a run (tool names, model names, span names) is client-controlled text (KI-078).

## Decision
`JudgeModel.complete(prompt, max_output_tokens) -> JudgeReply(text, model, tokens, cost)` is the whole provider contract. The
`judge` implementation builds a prompt with the instructions and rubric first and the run projection in one delimited block marked untrusted;
unless `include_content: true` the projection equals what a VIEWER sees (no command text, paths or payloads), bounded by `max_input_chars`; the reply
is parsed as a bounded structured object (score clamped to `[0,1]`, label from an allowlist, the rest in a truncated `metadata.raw`, withheld without
`payload.read`); the result records `judge_model`, `prompt_template_version`, tokens, cost and provider. `max_input_chars` and `max_output_tokens`
bound a call before it is made; `max_cost_usd` is a reporting and cooldown bound after it, not a spend cap. `ABB_JUDGE_PROVIDER` is `none` (judge
evaluators cannot be created) or `fake` (a deterministic scorer, `fake-judge-1`, cost 0, documented as plumbing, not a signal), the latter refused in
production unless `ABB_JUDGE_ALLOW_FAKE=1`. Every judge result is `kind=llm_judge`, `ground_truth=false`, and the UI labels it "Model judge - not
ground truth" wherever a score appears. A real provider (Anthropic Messages over `httpx`) is an adapter implementing the same method, added only
with the user's key and one manual run (KI-100), subject to the same prompt layout and parser.

## Alternatives
Ship a vendor client now (unverifiable here, a dependency with a key path, INV-5 exposure by default). Treat judge scores like
deterministic ones (the spec forbids it). Run judges in the API request (unbounded latency and cost on the request path). Interpolate run labels
into the instructions (prompt injection by any agent that names a tool).

## Consequences
Plumbing, budgets, labelling, withholding and injection handling are tested end to end without any vendor; judgement quality is
unproven until a provider exists; content reaches a provider only by explicit per-evaluator opt-in; money can be overspent by at most one reply
before the cooldown.

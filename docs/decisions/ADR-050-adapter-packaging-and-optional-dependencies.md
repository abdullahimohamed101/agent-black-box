# ADR-050: Adapters Are Separate Distributions That Depend Only on the SDK; Frameworks Are Optional

Status: Accepted
Date: 2026-10-07

## Context

Spec §18 and §70 put framework adapters under `integrations/`, "separately versioned where framework compatibility makes that useful",
and AGENTS.md fixes the direction `integrations/* -> packages/sdk-python -> packages/event-schema`. The SDK has a near-zero dependency
budget (ADR-013) and the API must not learn about frameworks (INV-8). Frameworks release often and break callbacks; a user who
instruments LangGraph must not be forced to install OpenAI, and a user of the OpenAI client must not be forced to install LangGraph.

## Decision

1. **One distribution per integration**, each in `integrations/<name>/` with its own `pyproject.toml`, `uv.lock` and virtualenv:
   `agent-black-box-langgraph` (import `blackbox_langgraph`), `agent-black-box-openai`, `agent-black-box-anthropic`, `agent-black-box-mcp`.
   Spec §18 lists the directories `openai/` and `anthropic/` separately (spec §70 says `openai-agents/`); we follow §18 because Phase 8 wraps the
   *client libraries*. A future OpenAI Agents SDK adapter would be a new directory.
2. **Runtime dependencies: only `agent-black-box`.** The framework is an *optional extra* (`pip install agent-black-box-langgraph[langgraph]`),
   never a hard requirement: the adapter itself must import and work when the framework is absent (it is duck-typed against the framework's
   callback interface; when the framework is installed it additionally subclasses the framework's base class). The SDK and the API never
   gain a framework dependency.
3. **Frameworks are dev dependencies of the adapter's own environment**, locked in its `uv.lock`, so CI runs every adapter's tests twice: fakes
   that mimic the callback interface (no framework, always runnable) and the real framework library driven offline (fake models, mock HTTP
   transports, in-memory MCP transport). Tests never use the network or real API keys.
4. **Version policy**: adapters declare a lower bound of the framework version they were tested with and are versioned independently of the SDK
   (`0.1.0` each). An adapter is allowed to rely on an SDK addition only by raising its `agent-black-box` lower bound.

## Five-question review of the new dependencies (docs/BUILD_PROMPT.md section 4)

| Dependency (where) | Platform provides it? | Maintained? | Surface used | Local alternative clearer? | Lock-in |
| --- | --- | --- | --- | --- | --- |
| `langgraph` (langgraph adapter: optional extra + dev) | no: it is the thing being integrated | yes (LangChain Inc., frequent releases) | `langchain_core.callbacks.BaseCallbackHandler` only | no: the base class is what the framework type-checks handlers against | none: removable extra |
| `openai` (openai adapter: optional extra + dev) | no | yes | the client object we wrap, `httpx.MockTransport` in tests | no | none |
| `anthropic` (anthropic adapter: optional extra + dev) | no | yes | same | no | none |
| `mcp` (mcp adapter: optional extra + dev) | no | yes (Anthropic / MCP steering group) | `ClientSession.call_tool` | no | none |
| `abb-conformance` (dev only) | this repo (ADR-051) | n/a | the suite | n/a | n/a |

Vulnerability review: dev-only (not shipped) except the optional extras, which are the user's own framework choice; `make audit` is extended to
the integration lockfiles in a later hardening step if `pip-audit` is adopted (KI-060).

## Alternatives

- One `integrations` package with all adapters and many extras: couples release cadence and test matrices; one broken framework release blocks
  all adapters. Rejected.
- Put adapters inside the SDK (`blackbox.integrations.langgraph`): the SDK must stay dependency-free and framework-agnostic; a lazy import hides
  coupling and breaks SDK mypy strictness. Rejected.
- A uv workspace sharing one lockfile: forces one resolution of all frameworks (langchain-core and openai/anthropic pins conflict over time).
  Rejected for isolation, at the price of more lockfiles.

## Consequences

- Positive: installs are minimal; framework breakage is isolated; the dependency direction is enforced by packaging.
- Negative: one CI job per adapter and several lockfiles; small helper code (the never-raise guard) is duplicated rather than shared, because a
  shared runtime helper package would add a fifth distribution (revisit if the duplication exceeds ~40 lines or a bug is fixed in one copy only).

## Migration implications

None for the backend. Publishing the packages needs explicit approval (AGENTS.md Git Safety).

## Revisit conditions

Add a shared `agent-black-box-adapter-kit` if three or more adapters need the same non-trivial helper beyond the guard decorator.

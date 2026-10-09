# Framework integrations

Spec §18, §70; ADR-050 (packaging), ADR-051 (conformance), ADR-052 (behavior). Adapters translate framework callbacks into SDK calls; they
are not a privileged path and the backend knows nothing about frameworks (INV-8).

| Package (import) | Wraps | Canonical output |
| --- | --- | --- |
| `integrations/langgraph` (`blackbox_langgraph`) | LangChain/LangGraph callbacks | run, `span.*` (nodes), `llm.request.*`, `tool.call.*`, retrieval spans |
| `integrations/openai` (`blackbox_openai`) | `chat.completions.create`, `responses.create` | `llm.request.*` |
| `integrations/anthropic` (`blackbox_anthropic`) | `messages.create` | `llm.request.*` (input tokens = whole prompt) |
| `integrations/mcp` (`blackbox_mcp`) | `call_tool` of a session/client | `tool.call.*` |
| `integrations/conformance` (`abb_conformance`) | test support | scenarios, structural rules, golden helpers |

Each package has its own `pyproject.toml`/`uv.lock`; runtime dependency is `agent-black-box` only, the framework is an optional extra and a dev
dependency. Tests run offline with fakes and with the real framework (fake chat model, `MockTransport`, in-memory MCP server).

Shared mapping: `llm.provider`, `llm.model`, `llm.input_tokens`, `llm.output_tokens`, `llm.cached_input_tokens`, `llm.temperature`,
`llm.max_tokens`; `cost.estimated_usd` only through the user's `cost_fn`; adapter spans carry `framework.name`. No payloads by default;
`capture_payloads=True` adds bounded previews that stay structured (so key-based redaction applies) and obey the SDK `payload_mode`.

Run locally: `make integrations-test`; end to end: `make integrations-e2e` (`scripts/integrations-e2e.sh`, database `abb_p8`, API port 8160).
Golden fixtures: `integrations/<pkg>/tests/golden/*.json`; rewrite with `ABB_UPDATE_GOLDEN=1 uv run pytest` and review the diff.

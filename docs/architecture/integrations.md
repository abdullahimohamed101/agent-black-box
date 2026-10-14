# Framework integrations

Spec §18, §70; ADR-050 (packaging), ADR-051 (conformance), ADR-052 (behavior). Adapters translate framework callbacks into SDK calls; they
are not a privileged path and the backend knows nothing about frameworks (INV-8).

| Package (import) | Wraps | Canonical output |
| --- | --- | --- |
| `integrations/langgraph` (`blackbox_langgraph`) | LangChain/LangGraph callbacks | run, `span.*` (nodes), `llm.request.*`, `tool.call.*`, retrieval spans |
| `integrations/openai` (`blackbox_openai`) | `chat.completions.create`, `responses.create` | `llm.request.*` |
| `integrations/anthropic` (`blackbox_anthropic`) | `messages.create` | `llm.request.*` (input tokens = whole prompt) |
| `integrations/mcp` (`blackbox_mcp`) | `call_tool` of a session/client | `tool.call.*`; with `policy_mode="enforce"` also asks first (below) |
| `integrations/conformance` (`abb_conformance`) | test support | scenarios, structural rules, golden helpers |

Each package has its own `pyproject.toml`/`uv.lock`; runtime dependency is `agent-black-box` only, the framework is an optional extra and a dev
dependency. Tests run offline with fakes and with the real framework (fake chat model, `MockTransport`, in-memory MCP server).

Shared mapping: `llm.provider`, `llm.model`, `llm.input_tokens`, `llm.output_tokens`, `llm.cached_input_tokens`, `llm.temperature`,
`llm.max_tokens`; the OpenAI and Anthropic adapters also report the billing modifiers `llm.cache_write_tokens`, `llm.cache_write_1h_tokens`, `llm.service_tier` and `llm.data_residency` when the response says (ADR-044); `cost.estimated_usd` only through the user's `cost_fn`; adapter spans carry `framework.name`. No payloads by default;
`capture_payloads=True` adds bounded previews that stay structured (so key-based redaction applies) and obey the SDK `payload_mode`.

Run locally: `make integrations-test`; end to end: `make integrations-e2e` (`scripts/integrations-e2e.sh`, database `abb_p8`, API port 8160).
Golden fixtures: `integrations/<pkg>/tests/golden/*.json`; rewrite with `ABB_UPDATE_GOLDEN=1 uv run pytest` and review the diff.

## Policy (Phase 14, D5, ADR-142)

Enforcement is cooperative (it covers calls that go through the SDK). Adapters that **execute** tools ask the policy service before the call; adapters that do not leave it to the tool function.

| Adapter | Behaviour with `policy_mode="enforce"` |
| --- | --- |
| `blackbox_mcp` | `call_tool` is guarded as `tool.<name>`, resource `mcp.call_tool`; a denied call is not made and returns an MCP error result (`isError`), nothing is raised; no `tool.call.*` event is recorded for it |
| `blackbox_langgraph`, `blackbox_openai`, `blackbox_anthropic` | not hooked (they do not run tools): put `@guarded("tool.<name>", ...)` on the tool function; each README has the example and each test suite runs `abb_conformance.check_guarded_tool` |

The contract for an adapter that does not run tools is `check_guarded_tool` (an allowed tool runs once; a denied one raises `PolicyDenied` before its body; each call asks once); adapters test against
`abb_conformance.policy_stub`, a decide-only stub, so none depends on the API. Capability, resource and attributes follow the dry-run table in `apps/api/src/abb_api/policies/mapping.py`.

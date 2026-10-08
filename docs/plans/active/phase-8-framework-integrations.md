# Phase 8 - Framework integrations

Status: In progress
Owner: implementer agent
Branch: `feature/phase-8-framework-integrations` (from `main` cc56935; developed in parallel with Phases 6 and 7)
Depends on: Phase 3 (SDK), Phase 1 (event contract)
Spec refs: §18, §70, §62 (INV-4/5/6/8), §67 (SDK), ADR-007, ADR-010, ADR-013; new ADR-050, ADR-051, ADR-052

## Outcome
A LangGraph app is traced by adding one callback to its config and yields a useful trace (nodes, model calls with tokens, tool calls, nested
structure). OpenAI and Anthropic clients and MCP client sessions are instrumented with one call. All adapters pass one shared conformance
suite. No backend change.

## Non-goals
CrewAI / AutoGen / OpenAI Agents SDK adapters (later), TypeScript adapters (Phase 17), server-side cost computation (Phase 7), approvals and
policy hooks (Phase 14), multi-agent trace propagation and `agent.spawned` (Phase 9), LangGraph state-change events and checkpoints (KI-061),
streaming helper objects such as `anthropic.messages.stream()` (KI-062), changes to `apps/api` or `apps/web`.

## Current architecture
`packages/sdk-python` (`blackbox`): `BlackBox.run`, `Run.span(kind=)`, `Run.llm_call`, explicit `Span.start()/end()`, redaction, bounded
exporter. `examples/python/trace_an_agent.py` and `scripts/sdk-e2e.sh` show the SDK path. `integrations/` does not exist yet. CI has an `sdk` job.

## Known issues considered
- KI-018 (S1, quotas) and KI-019 (S1, auth throttling): server concerns, untouched; adapters add no new credential or endpoint.
- KI-029 (S1, web exposure): web not touched.
- KI-032 (S3, best-effort secret detection): adapters keep payloads off by default and rely on SDK redaction when enabled; conformance
  `sensitive_full` asserts known secret shapes never appear.
- KI-030 (S3, in-memory SDK queue): unchanged; examples call `shutdown()`.
- KI-016/022 (summarizer cost on huge runs): a chatty LangGraph app emits many events; the e2e uses small runs; no change.
- KI-027/028 (lookup / aggregate endpoints), KI-017/021/023/024/025/026: unrelated.
- New: KI-060 (no vulnerability audit of integration lockfiles), KI-061 (no LangGraph state events), KI-062 (no streaming-helper objects).

## Decisions
D1 packaging and optional dependencies: ADR-050. D2 conformance suite design: ADR-051. D3 adapter behavior contract: ADR-052.
D4 SDK addition: `Run.llm_call(..., parent=)` (backwards compatible, tested) so a model call can nest under an explicit span; every other
SDK API is used as is.
D5 Runtime check for the real frameworks: a hand-written fake that mimics the callback / client / session interface is the always-on test;
the real library (from the adapter's own lockfile) is driven offline (fake chat model, `httpx.MockTransport`, in-memory MCP transport).

## Proposed design
```text
integrations/
  conformance/   abb_conformance: Driver protocol, scenarios, normalize + golden helpers, self-tests
  langgraph/     blackbox_langgraph.BlackBoxCallbackHandler(bb, cost_fn=, capture_payloads=)
  openai/        blackbox_openai.instrument(client, bb)       chat.completions.create, responses.create (sync, async, stream)
  anthropic/     blackbox_anthropic.instrument(client, bb)    messages.create (sync, async, stream)
  mcp/           blackbox_mcp.instrument(session, bb, server=) ClientSession.call_tool
examples/langgraph/agent.py        a real LangGraph agent with a fake model and a tool
scripts/integrations-e2e.sh        own DB abb_p8, API :8160, worker, example -> real API -> read back
```
Each adapter: guard decorator (never raises), explicit parenting, attribute mapping per ADR-052, `tests/` with fake-driven and real-framework
conformance, hostile inputs, exception-in-callback tests, concurrency, goldens in `tests/golden/`.

## Affected files
New: `integrations/**`, `examples/langgraph/`, `scripts/integrations-e2e.sh`, ADR-050..052, this plan. Edited: `packages/sdk-python/src/blackbox/client.py`
(+test), `scripts/quality.sh`, `Makefile`, `.github/workflows/ci.yml`, `docs/KNOWN_ISSUES.md`, `docs/DECISIONS.md`. Not touched: `apps/*`, state docs.

## Acceptance criteria
1. Conformance suite: reference driver passes every scenario and the mutants fail (`integrations/conformance`).
2. Each adapter passes every supported scenario with its fake driver AND with the real framework (LangGraph, OpenAI, Anthropic, MCP).
3. Goldens exist per adapter scenario, are valid `EventIn`, and a drifted adapter output fails the golden test.
4. Exceptions raised inside SDK/redactor/cost callbacks and hostile framework inputs never propagate from adapter entry points; host exceptions
   propagate unchanged.
5. Concurrency (threads and asyncio), nested runs, token/cached-token/cost mapping tested for each adapter where applicable.
6. No payload or secret in events in default mode; with `payload_mode=full` known secret shapes are redacted.
7. LangGraph example instrumented with <= 3 changed lines runs through the real API (`scripts/integrations-e2e.sh`, DB `abb_p8`, port 8160),
   and the read-back run has the expected llm/tool counts, tokens and nesting; no `apps/` diff.
8. `scripts/quality.sh full` exit 0 including the new packages; CI jobs added for each package.
9. Review-change security pass recorded; mutation spot-check of committed code recorded.

## Verification plan
Per package: `uv run ruff check . && ruff format --check . && mypy && pytest --cov`. End to end: `scripts/integrations-e2e.sh`.
`git diff --stat main -- apps` must be empty. Mutations on committed code (drop the guard, swap token fields, parent lookup) must fail tests.

## Risks
Real-framework APIs drift (mitigated by lockfiles + lower bounds + fake drivers); callbacks on foreign threads (explicit parenting, lock);
leaked in-flight state (bounded map); secrets in error messages (SDK pattern redaction; `error.message` truncated).

## Ordered steps
- [ ] 1 Plan, ADR-050..052 (this commit)
- [ ] 2 SDK `llm_call(parent=)` + conformance package
- [ ] 3 LangGraph adapter (fakes, real LangChain/LangGraph, goldens)
- [ ] 4 OpenAI wrapper
- [ ] 5 Anthropic wrapper
- [ ] 6 MCP wrapper
- [ ] 7 quality.sh, Makefile, CI jobs
- [ ] 8 Example + `scripts/integrations-e2e.sh`, docs/architecture/integrations.md
- [ ] 9 Verify, review (security), harden, mutation check, close-out (plan moved to completed with evidence)

# ADR-177: The SDK import package is `agent_blackbox`

Status: Accepted (Phase 20, release polish). The owner delegated the choice to the lead's recommendation in `docs/design/launch/package-names.md`.
Date: 2026-10-19
Phase: 20

## Context

The SDK (distribution `agent-black-box`) was imported as `blackbox`. The public PyPI project `blackbox` (IRC macros, 0.7.1) installs a top-level `blackbox` module too, so a user with both installed would have two distributions writing the same directory and one silently shadowing the other. Nothing is published, which makes the rename free now and a breaking change for every user later (`docs/design/launch/package-names.md`, checked 2026-10-19).

## Decision

1. The import package is `agent_blackbox` (`packages/sdk-python/src/agent_blackbox`, moved with `git mv`). `from agent_blackbox import BlackBox` is the documented form everywhere.
2. Stay the same, deliberately:
   - the distribution name `agent-black-box` (free on PyPI) and the class names (`BlackBox`, `BlackBoxCallback`);
   - the environment variables `BLACKBOX_*` (`BLACKBOX_API_KEY`, `BLACKBOX_ENDPOINT`, `BLACKBOX_POLICY`, `BLACKBOX_TRACE_CONTEXT`): they are not import names, so they cannot collide with a package, and renaming them would break every configured environment for no benefit;
   - wire-visible identifiers (the `X-Blackbox-Trace-Context` header, event and attribute names, the `name = "blackbox"` of the LangGraph callback handler): they are protocol, not Python imports;
   - the adapter modules `blackbox_anthropic`, `blackbox_openai`, `blackbox_mcp`, `blackbox_langgraph`: they are different top-level names with no known collision; renaming them is churn without the safety benefit and can be decided with their own publication;
   - thread-name prefixes and the `blackbox:` prefix of log messages (display text, not an import);
   - there are no console scripts named `blackbox`, so there is nothing to decide for commands.
3. The SDK's logger namespace follows the import name: `logging.getLogger("agent_blackbox")`, and the adapters log under `agent_blackbox.<adapter>` so they remain children of it. Anyone who configured a logger called `blackbox` must use the new name (the SDK is unpublished).
4. `scripts/release_checklist.py imports` fails on `from blackbox import`, `import blackbox` and `src/blackbox` in any tracked file outside the historical records (ADRs, completed plans, the original spec, the landing concepts, the package-name study). A planted-violation test covers it.

## Alternatives considered

- `agent_black_box` (matches the distribution name exactly): longer and easy to mistype next to `agent_blackbox`; the class and the product word are "BlackBox", so the shorter form reads better. Either avoids the collision; nothing else separates them.
- Keep `blackbox` and publish under another distribution name: the collision is on the import name, so it would not fix it.
- Rename `BLACKBOX_*` too: see decision 2.

## Consequences

A breaking change to an unpublished SDK: the SDK, the four adapters and the conformance suite, the examples, scripts, docs, the site and web snippets, and CI were updated in one change. Old ADRs and completed plans keep the old name as history. `agentblackbox` (PyPI) and `agent-blackbox` (npm) remain taken by other projects; the package-name study keeps that note.

# ADR-051: A Shared Conformance Suite Defines Equivalent Canonical Events for Equivalent Operations

Status: Accepted
Date: 2026-10-07

## Context

Spec §70: "Every integration must pass a conformance suite that verifies it produces equivalent canonical events for equivalent operations."
Phase 8 ships four adapters with very different inputs (LangChain callbacks, client method wrappers, MCP sessions). Without a common bar each
would drift (different attribute names, parenting, failure statuses), and the UI/analytics would see inconsistent traces.

## Decision

1. **`integrations/conformance` (`abb-conformance`, test support, never published)** owns the expectations. An adapter's tests supply a
   `Driver` (how to run a standard operation through its framework, real or faked) and call `check_scenario(driver, name)` for each scenario it
   `supports`. The suite creates the SDK client (`mode="offline"`), runs the scenario, then checks the events.
2. **Scenarios**: `tool`, `tool_failure`, `llm` (provider, model, input/output/cached tokens), `llm_failure`, `nested` (tool and llm share one
   enclosing step), `concurrent` (8 simultaneous tool calls pair correctly), `sensitive` and `sensitive_full` (secrets in inputs, outputs and error
   messages never reach an event; no payload unless the mode asks), `hostile` (malformed framework inputs), `redactor_raises` (a failing SDK
   hook never reaches the host). A failure scenario must raise `ScenarioError` out of the framework call: this asserts the adapter
   lets the host's own exceptions propagate unchanged.
3. **Rules applied to every scenario** (`_structure`): each event validates through `abb_event_schema.EventIn` (contract drift fails the build);
   each run starts with `run.started` and ends exactly once; each span opens once and closes once, children open after their parent, nothing
   is left open; per-run sequence is monotonic; no `payload` or `payload_ref` in default mode.
4. **Golden fixtures**: each adapter keeps `tests/golden/<scenario>.json`, the event list normalized by `abb_conformance.normalize`
   (ids numbered by first appearance as legal ULIDs, times advancing 1 ms per event, durations and latencies fixed). Goldens are valid events,
   so a separate test validates every golden against `EventIn`; they are rewritten only with `ABB_UPDATE_GOLDEN=1` and reviewed in the diff.
5. The suite tests itself (`integrations/conformance/tests`): a reference driver calling the SDK directly passes; mutants (missing usage,
   swallowed error, wrong name) fail.

## Alternatives

- Compare every adapter's output to one shared golden: operations are not identical across frameworks (names, nesting), so exact equality is
  over-constraining; shape rules plus per-adapter goldens catch drift without false failures. Rejected.
- Put the suite in the SDK's tests: the SDK would depend on adapter conventions, and adapters could not import it. Rejected.

## Consequences

Positive: one bar for all adapters, including future CrewAI/AutoGen; contract drift fails adapter CI. Negative: the suite is code to
maintain; scenarios an adapter cannot express (`nested` for a thin client wrapper) are declared unsupported and listed in its README, not silently skipped.

## Migration implications / Revisit

None. Revisit to add scenarios (streaming, cancellation, approvals) as Phase 14 introduces policy hooks.

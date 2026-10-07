# Agent Engineering Guide

Entry point for AI agents working in this repository. Keep it concise: detailed
product and architecture knowledge lives in `/docs`.

## Mission

Build Agent Black Box: a flight recorder, observability, debugging, evaluation and
(eventually) control plane for AI agents. Produce correct, maintainable, testable
software that satisfies the specification while preserving the architecture.
Correctness and maintainability beat minimizing lines changed.

> Developers must first trust that Agent Black Box accurately shows what their agents
> did. Everything else depends on that trust.

## Session Start Protocol

Every session, in order, before editing anything:

1. Read `docs/PROJECT_STATE.md` (where we are, exact next task, failing tests).
2. Read the active plan in `docs/plans/active/` (there is at most one).
3. Read `ARCHITECTURE.md` and the spec sections the plan cites
   (`docs/architecture/agent-black-box-spec.md`; sections are written `§N`).
4. Read the newest ADRs in `docs/decisions/`.
5. Run `git status`, `git log -5`, then `scripts/quality.sh quick`.
6. Continue from the exact next task. Do not re-plan finished work.

Before ending a session, update `docs/PROJECT_STATE.md` so another agent can continue
without chat history.

## Source of Truth and Precedence

1. This file and the build prompt (`docs/BUILD_PROMPT.md`)
2. ADRs in `docs/decisions/` (they amend the spec)
3. The spec (`docs/architecture/agent-black-box-spec.md`)
4. Existing code, tests and API contracts
5. Engineering judgment

Never silently diverge from the spec. If a better tradeoff appears, write an ADR first
(skill: `write-adr`), then change the code.

## Workflow: one phase, one plan, one branch

Each phase follows the same loop, using the skills in `.agents/skills/`:

```text
plan-change  ->  implement-change  ->  verify-change  ->  review-change
             ->  harden-change     ->  complete-phase  ->  prepare-pr
```

- Non-trivial work starts with a plan in `docs/plans/active/phase-N-<name>.md`.
  Finished plans move to `docs/plans/completed/` with a closing status line.
- Work on `feature/phase-N-<name>`; one coherent commit per implementation unit
  (`feat(schema): ...`). Never work on `main`.
- A phase is complete only when every acceptance criterion in its plan is
  evidenced with a command and its output, not when code exists.
- Stop at a phase boundary only for a real blocker (see "When to Ask"). Otherwise start
  the next phase's plan.

## Architectural Invariants (spec §62 - never violate without an ADR)

- INV-1 Accepted events are immutable. Corrections are new events or derived records.
- INV-2 Derived state (summaries, costs, findings, aggregates) is rebuildable from events.
- INV-3 Every tenant-data operation carries workspace context. No repository method
  reads tenant data without it.
- INV-4 SDK failure never crashes the host agent. Buffers are bounded; retries are bounded.
- INV-5 Sensitive payload capture is opt-in/configurable; redact before export.
- INV-6 Agent concepts (tool call, LLM call, approval, retry) are typed, not log strings.
- INV-7 Storage sits behind contracts (`EventStore`, `ArtifactStore`, `RunRepository`,
  `AnalyticsStore`). No ad-hoc SQL against events from controllers.
- INV-8 External conventions (OpenTelemetry) are mapped at the boundary, never copied in.

Also: never trust arrival order; ingestion is at-least-once with idempotent persistence
on `(workspace_id, event_id)`; framework-specific shapes never leave `integrations/`.

## Dependency Direction

```text
apps/*  ->  packages/*          (apps depend on packages, never the reverse)
integrations/*  ->  packages/sdk-python  ->  packages/event-schema
apps/api modules:  routers -> services -> repositories -> db
```

`packages/event-schema` depends on nothing in the repo. The API's modules
(`ingestion, runs, traces, analytics, evaluations, auth, policies`) talk through
service/repository interfaces, not each other's tables.

## Engineering Principles

- Smallest coherent solution; no refactors of unrelated code.
- Reuse existing abstractions; do not abstract after one example.
- No new dependency without the five questions in the build prompt (§ Dependencies);
  the SDK has a near-zero runtime dependency budget.
- Validate external data at boundaries. Captured telemetry is hostile input.
- Every external call has a timeout; every retry loop is bounded; every queue is bounded.
- Comments explain why, not what. Structured logs only; never log payload bodies or secrets.
- No raw SQL in routers; no business logic in React presentation components.

## Verification

Evidence vocabulary, used in plans, reports and PROJECT_STATE:

- **VERIFIED** - a command was run in this environment and its output observed.
- **UNVERIFIED (env)** - cannot be run here (e.g. Docker missing). State exactly why and
  what would verify it. Never write PASS for these.
- **ASSUMED** - reasoned, not exercised.

After modifying code: focused tests, then `scripts/quality.sh full` (format, lint,
typecheck, tests, migration check, build). Report anything not verified.
Never remove, weaken, skip or rewrite a failing test to get green.
UI changes must be exercised in a browser (screenshot or DOM check), not just built.

## Debugging

Reproduce, gather evidence, find the root cause, make the smallest correct fix, prove
the failure is gone, check for regressions. Do not patch symptoms.

## Git Safety

- Never force push; never push or merge to `main`; never discard uncommitted user work.
- Commits on a `feature/phase-*` branch are allowed (one per implementation unit).
- Pushing, opening PRs, merging, tagging and publishing packages need explicit approval.
- Never commit secrets or `.env`; only `.env.example`.

## Security

Never expose, print, modify or commit API keys, passwords, tokens, private keys or `.env`
contents. Security-sensitive changes (auth, API keys, redaction, tenant scoping, policy,
approvals) require an explicit review pass (`review-change`) and a note in the plan.
Seed/demo credentials are generated, local-only and documented as such.

## When to Ask

Stop and ask only for: missing credentials or accounts, destructive or irreversible
actions, legal/compliance choices, product decisions that materially change scope,
installing system software, and anything that publishes or pushes. For everything else,
choose the simplest production-quality option, record it, continue.

If an environment tool is missing (Docker, Node, Postgres), do not fake it: build against
interfaces, mark affected criteria `UNVERIFIED (env)` in `KNOWN_ISSUES.md`, and continue
with work that is verifiable. Phase gates that need the tool stay open until it exists.

## Repository Knowledge

- State and next task: `docs/PROJECT_STATE.md`
- Phase roadmap and acceptance criteria: `docs/IMPLEMENTATION_PLAN.md`
- Active / completed plans: `docs/plans/active/`, `docs/plans/completed/`
- Decisions: `docs/decisions/` (index in `docs/DECISIONS.md`)
- Architecture: `ARCHITECTURE.md`; spec: `docs/architecture/agent-black-box-spec.md`
- Event contract: `packages/event-schema/` and `docs/architecture/events.md` (once created)
- Security: `docs/SECURITY.md` - Reliability: `docs/RELIABILITY.md`
- Testing: `docs/TESTING.md` - Operations / runbooks: `docs/OPERATIONS.md`, `docs/runbooks/`
- Known issues and env gaps: `docs/KNOWN_ISSUES.md`
- Local setup: `docs/development/setup.md`

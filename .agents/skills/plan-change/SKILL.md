---
name: plan-change
description: Plan a phase, feature, bug fix, migration, refactor or architectural change before implementation. Produces docs/plans/active/phase-N-*.md.
---

# Plan Change

Do not modify production code while executing this skill.

## Process

1. Restate the intended user or system outcome.
2. List non-goals (what the spec defers; what later phases own).
3. Read `docs/PROJECT_STATE.md`, `ARCHITECTURE.md`, the cited spec sections and recent ADRs, and
   **`docs/KNOWN_ISSUES.md`**: list every open item whose Target is this phase or whose trigger has fired,
   plus all open S1 items. For each, either pull it into the plan (with an acceptance criterion) or write
   one line in the plan under "Known issues considered" saying why it stays deferred.
4. Inspect the existing implementation; find patterns to reuse.
5. Identify affected modules, APIs, schemas, migrations and dependencies.
6. List unknowns and investigate the important ones before proposing a design.
7. Check the plan against the invariants in `AGENTS.md` (INV-1..8) and the spec's
   "Deferred" list. Anything that needs Kafka/ClickHouse/Redis/Kubernetes must cite
   the measured trigger or it is out of scope.
8. Check environment prerequisites (`docs/development/setup.md`). If a tool is missing,
   say which acceptance criteria become `UNVERIFIED (env)`.
9. Define explicit, command-checkable acceptance criteria and a verification plan.
10. Identify security, reliability, migration, compatibility and data-retention risks.
11. Produce the smallest coherent ordered implementation plan; one commit per step.

## Output

Persist under `docs/plans/active/phase-N-<name>.md` using this shape (see the
ServerFlow-style plans): Status, Owner, Depends on, Spec refs, Outcome, Non-Goals,
Current Architecture, Known issues considered, Decisions (D1..Dn, confirm before implementation), Proposed
Design, Affected Files, Acceptance Criteria, Verification Plan, Risks, Ordered Steps.

Decisions that a future contributor could reasonably question become ADRs
(`write-adr`). Ask the user only about real blockers.

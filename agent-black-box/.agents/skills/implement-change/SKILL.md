---
name: implement-change
description: Implement an approved execution plan or narrowly scoped engineering change, with tests, on a phase branch.
---

# Implement Change

Before modifying code:

1. Read the active plan and `docs/PROJECT_STATE.md`.
2. Read relevant architecture docs and the cited spec sections.
3. Confirm repository state (`git status`, correct `feature/phase-*` branch).
4. Inspect existing patterns before introducing abstractions.

During implementation:

- Smallest complete vertical slice first; keep each commit reviewable.
- Preserve module boundaries and the invariants in `AGENTS.md`.
- Tests ship with the code, in the same commit. Test against real PostgreSQL for
  anything touching repositories, migrations, idempotency or tenant scoping.
- Never put framework-specific shapes in the canonical model or the backend.
- Do not add dependencies without the dependency checklist in `docs/BUILD_PROMPT.md`.
- Schema/event changes follow `add-event-type`; migrations are additive and never
  edited after commit.
- Update the plan's step checklist as steps complete.

Before completion:

1. focused tests, then `scripts/quality.sh full`
2. inspect diagnostics, `git diff`, `git diff --check`
3. exercise UI/API changes manually and record the evidence

Report: files changed, behavior implemented, commands run with results, design
deviations (ADR link), unresolved risks, and anything `UNVERIFIED (env)`.

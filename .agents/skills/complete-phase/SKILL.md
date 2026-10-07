---
name: complete-phase
description: Close out a phase - evidence every acceptance criterion, update state docs, move the plan to completed, emit the phase completion report.
---

# Complete Phase

Run only after `verify-change` and `review-change` have no open P0/P1 findings.

1. Walk the plan's acceptance criteria one by one. For each, run the command and record
   the result. Mark `PASS` only with observed evidence; otherwise `UNVERIFIED (env)` or
   `FAIL`. A phase with a FAIL or an unverified *required* criterion is not complete.
2. Run `scripts/quality.sh full`; record test counts and duration.
3. Validate migrations: empty -> head, and previous head -> new head.
4. Update `docs/KNOWN_ISSUES.md` first (move fixed rows to Resolved and close their GitHub issues;
   every new deferral needs a severity, a target and an issue), then update `docs/PROJECT_STATE.md`, `docs/IMPLEMENTATION_PLAN.md` (status), README phase
   table, `ARCHITECTURE.md` component statuses, `docs/KNOWN_ISSUES.md`, and relevant docs.
5. Set the plan's Status to Completed and move it to `docs/plans/completed/`.
6. Emit the report:

```text
PHASE <N> COMPLETE
Implemented / Acceptance criteria (PASS|UNVERIFIED|FAIL each, with evidence) /
Tests (command, result) / Build / Migrations / Docs updated /
Known issues / Technical debt introduced / Next phase
```

7. Create the next phase's plan with `plan-change`. Do not push or open a PR without approval.

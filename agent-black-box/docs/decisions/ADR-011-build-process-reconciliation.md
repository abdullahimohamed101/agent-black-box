# ADR-011: Build-Process Reconciliation of the Prompt, the Spec, and the ServerFlow Workflow

Status: Accepted (pending user approval of the documentation set)
Date: 2026-10-07

## Context

The v1 build prompt and the engineering spec overlap but disagree in places, and the
user asked that the agent follow ServerFlow's development process (AGENTS.md as short entry
point, skills, per-phase plans, ADRs, honest status). Conflicts found:

1. **Event names**: spec §15 uses `tool_call_completed`; §64.3 (the low-level contract)
   uses `tool.call.completed`.
2. **Envelope fields**: §15 `timestamp`/`duration_ms`/`payload`; §64.1 `occurred_at`/`payload_ref`.
3. **Phase numbering**: the prompt has Phases 0-20; the spec has milestones M0-M8 and phases 0-9.
4. **Live transport**: prompt says "SSE / WebSockets"; spec §57.1, §76 commits to SSE by default.
5. **Repo tree**: spec §46 includes `services/`; §128 and the prompt do not.
6. **Docs layout**: prompt wants flat `docs/*.md` + `docs/adr/0001-*.md`; ServerFlow uses
   `docs/decisions/ADR-NNN-*.md` and `docs/plans/{active,completed}`; the spec pre-numbers ADR-001..010.
7. **Roles**: spec §91 lists BILLING; prompt lists five roles.
8. **Autonomy vs gates**: v1 says never stop; ServerFlow requires plans, independent
   verification/review, and approval for pushes.
9. **Environment**: the dev machine has no Node, Docker or PostgreSQL and Python 3.9 system-wide,
   so v1's "do not proceed until reproducible" and "run all tests" cannot be satisfied as written.

## Decision

1. Event names are dot-delimited per §64.3. §15's list is treated as the same families in older notation.
2. The envelope follows §64.1 (`occurred_at`, `duration_ms`, `payload_ref`); `received_at` is server-assigned.
3. The prompt's Phase 0-20 numbering is kept; `docs/IMPLEMENTATION_PLAN.md` maps spec milestones onto it
   and adds the outbox/summarizer to Phase 2 and an MVP review gate after Phase 7.
4. SSE by default; WebSockets only for approvals/cancel (Phase 14).
5. No `services/` directory; the API is a modular monolith (§103, §128).
6. ADRs live in `docs/decisions/ADR-NNN-*.md` with spec numbering; plans in `docs/plans/`;
   `docs/DECISIONS.md`, `PROJECT_STATE.md`, `IMPLEMENTATION_PLAN.md`, `KNOWN_ISSUES.md`,
   `TESTING.md`, `OPERATIONS.md`, `SECURITY.md`, `RELIABILITY.md` stay flat as the prompt asked.
7. Roles include BILLING (superset).
8. Autonomous *within* a phase and across phases, but each phase runs plan -> implement ->
   verify -> review -> harden -> complete with evidence; pushing/PRs/merging need approval.
   Local commits on `feature/phase-*` branches are allowed.
9. Missing tools are handled with the `UNVERIFIED (env)` convention; installs require user approval.

## Alternatives

- Keep v1 prompt unchanged: preserves its autonomy but leaves the conflicts and the
  unverifiable acceptance criteria, and lacks ServerFlow's proven review discipline.
- Adopt ServerFlow wholesale (no autonomy across phases): safer but slow for a 21-phase build.

## Consequences

- Positive: one coherent contract; agents can resume from files; claims are evidence-backed.
- Negative: more process per phase; the MVP gate introduces a deliberate human pause.

## Migration implications

None (no code exists). If event naming changed later it would need a schema major version (§64.5).

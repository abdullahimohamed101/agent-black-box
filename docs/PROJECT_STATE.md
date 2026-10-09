# Project State

Last updated: 2026-10-09 (Phase 15 branch `feature/phase-15-auth-rbac`)

## Current phase
Phases 0-8 are merged to `main` (CI green). The MVP gate (spec §49, §138) is reached but **not accepted**; it closes when the items under
"Next actions" are done and the user reviews it. **Active plan: `docs/plans/active/phase-15-auth-rbac.md`** (auth, workspaces, RBAC): steps 1-16 are implemented
and committed on `feature/phase-15-auth-rbac` (worktree `../abb-worktrees/phase-15`); status "Implemented; pending security review". Not pushed, no PR.

## Current milestone
M0-M3 complete (foundation, ingestion, SDK, web, live streaming, coding-agent demo, cost and analytics). Phase 8 (integrations) is
post-MVP and also done. M4 starts with Phase 9, after Phase 15.

## Completed work
- Phases 0-2: foundation, event contract, ingestion/outbox/query API (`docs/plans/completed/`).
- Phase 3: `packages/sdk-python` (stdlib only, ADR-013); KI-020 fixed (`abb_runtime` role, migrations 0007-0008).
- Phase 4: `apps/web` (dashboard, runs list, run detail, typed client, fixtures, server-side read proxy, ADR-020/021).
- Phase 5: SSE live streaming (`GET /v1/runs/{id}/stream`, arrival-time resume, NOTIFY wake-up, ADR-022), polling fallback.
- Phase 6: artifact store (ADR-030, migration 0044), coding telemetry, command risk classifier and secret-safe capture (ADR-031; capture is
  best-effort and opt-in, accepted gaps are listed there), local syntax highlighting (ADR-032), `blackbox.coding`, web story/diff/shell panels,
  `examples/coding-agent`, `scripts/coding-e2e.sh`.
- Phase 7: CostEngine, analytics rollups (migrations 0040-0043), `/v1/analytics/*`, analytics page (ADR-040..043).
- Phase 8: adapters for LangGraph, OpenAI, Anthropic and MCP, conformance suite, SDK 0.2.0 (ADR-050..052).
- Phase 15 (branch, unmerged): OIDC sign-in with API-owned sessions (ADR-060), one `authorize()` path and a role/scope matrix with a route-walking test registry (ADR-061),
  invitations, members, API-key and pricing-override management, append-only audit log (ADR-062, migrations 0045-0047), `payload.read` (VIEWER/BILLING never see content),
  stream re-authentication, web sign-in/switcher/settings, the shared web key removed; resolves KI-029, KI-027, KI-051, KI-033. Closing evidence is in the plan.

## In-progress work
Phase 15 awaits (1) the independent security review (`review-change`, plan AC-15 and its attack list), whose findings are fixed or filed; (2) the user's one manual
login with a real OIDC provider (AC-14, needs their own client id/secret; see `docs/runbooks/auth-and-access.md`); (3) a compose runtime check (`make up && make seed`,
sign in as `owner@local.test`, `make smoke && make sdk-e2e`; KI-074: the host's compose ports were busy). Then `complete-phase` (move the plan to `completed/`), push and PR
(needs approval), and file the GitHub issues for KI-067..074 ("to file" in `KNOWN_ISSUES.md`).

## Blocked work
None.

## Next actions (exact)
1. Run the security review of the Phase 15 branch against the plan's "Security review note"; fix or file findings; re-run `scripts/quality.sh full` and the five E2E scripts.
2. User: the AC-14 manual real-provider login; decide MVP gate acceptance (KI-050 vendor prices, README clean-machine run remain; KI-054 and KI-064..066 are done).
3. After the review: `complete-phase` for Phase 15 (plan to `completed/`), approve the push and PR. Then Phases 9, 10, 11 and 16 can run in parallel worktrees.

## Open decisions
- MVP acceptance (after the gate items above).
- Remove the `Co-Authored-By` trailers from the 5 earliest commits (needs a force-push; not done).
- Until Phase 15 is merged and a real provider is configured, `main` still has the shared-key web proxy (ADR-021, KI-029): do not expose it publicly. On the branch the key is gone.
- Whether to stamp `received_at` with the database clock (multi-instance clock skew vs the stream overlap, ADR-022).

## Known technical debt
`docs/KNOWN_ISSUES.md` (severity, target and GitHub issue per item): notably (Phase 15 additions KI-067..074: real-provider login unverified, no session list, no `email_verified`-less providers, audit retention, trusted proxies, compose login unverified) full recomputation of very large active runs (KI-016), no quotas (KI-018), no failed-auth throttling (KI-019).

## Last verified test status
2026-10-09, Phase 15 branch: `scripts/quality.sh full` run as its parts, all green: event-schema 348, sdk 806 (coverage 94%), examples 12, integrations 17/74/66/61/44, api 799, web 396; migrations 0045-0047 up/down/up; `next build`.
E2E (own databases): `auth-e2e` 8, `e2e-web-real` 2, `coding-e2e` 6, `stream-e2e` 5, `analytics-e2e` 4, `integrations-e2e` passed. Not run: `make smoke`, `make sdk-e2e`, `make up` (compose ports busy; UNVERIFIED (env), KI-074).
Previous (main): 2026-10-09 (Phase 6 branch merged with Phases 7 and 8): `scripts/quality.sh full` exit 0, api 535, web 311, plus event-schema, SDK, example and integration suites; GitHub CI green on PR #49 (18 checks incl. `coding-e2e`, `integrations-e2e`, `stream-e2e`). A manual browser run of the demo (runs list, run page, diff panel, analytics) worked; findings are KI-064..066.

## Last verified build status
Compose stack: last fully verified at Phase 2 (images build; clean-slate `docker compose up --wait` reached alembic 0006 with 15 tables;
`scripts/smoke.sh` passed; 858-request hostile probe: zero 5xx). **UNVERIFIED (env)** since: the compose stack was not rebuilt after Phase 2
(migrations 0007-0044, the `abb_runtime` role, streaming, artifacts, analytics). CI's `containers` job covered Phase 3's compose run; run `make up && make smoke
&& make sdk-e2e` as part of the MVP gate. Later phases were verified outside compose by their own E2E scripts (own API, worker, web and database).

## Commands to verify environment
```bash
colima status || colima start --cpu 2 --memory 4
cp -n .env.example .env && make setup
scripts/quality.sh full
make up && make smoke        # then: make down
```

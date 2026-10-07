# Phase 4 - Core web product

Branch `feature/phase-4-web-product` (from `main` 4828e61). Runs in parallel with Phase 3 (`packages/sdk-python`, API DB roles);
this phase does not touch either. Spec: §135, §145 items 10-11, FR-UI; ADR-020, ADR-021.

## Outcome
Someone unfamiliar with a run understands it in about ten seconds: header summary, a timeline that makes the first error
and any retries obvious, and drawers for LLM / tool / error / generic events.

## Non-goals
Live streaming (Phase 5), diffs and shell panel (6), analytics API and charts (7), span waterfall (9), auth/login and
workspace switching (15), compare/replay. Cost shown is the event-reported `cost.estimated_usd` sum (Phase 7 owns pricing).

## Known issues considered
- Open S1 items (KI-018/019/020) are API/ops concerns, untouched here; KI-020 is Phase 3's.
- KI-016/022 (huge active runs) matter to the UI only as 10,000-event runs: handled by virtualization and progressive paging.
- New items this phase (see `docs/KNOWN_ISSUES.md`): no project/workspace lookup endpoint, no aggregate endpoint.

## Design
- **Typed client**: `openapi-typescript` generates `src/lib/api/schema.d.ts` from the committed `apps/api/openapi.json`;
  `openapi-fetch` gives a typed client. `pnpm gen:api` regenerates; `scripts/quality.sh` fails if it is stale.
- **Auth/BFF (ADR-021)**: the browser calls same-origin `GET /api/abb/*`; a Next route handler allowlists read paths and adds
  the server-side `ABB_WEB_API_KEY` (a `runs:read` key). The key never reaches the browser. `ABB_WEB_DATA_SOURCE=fixtures`
  serves the same endpoints from deterministic fixtures, so Playwright and UI work never need the API or LLM spend.
- **Fetching/cache (ADR-020)**: TanStack Query on the client (stale-while-revalidate, keyed by URL, infinite queries for cursors),
  `@tanstack/react-virtual` for the timeline.
- **Routes**: `/w/[workspace]/projects/[project]/` dashboard, `/runs`, `/runs/[runId]`. `[project]` is the project id (`prj_...`)
  or `all`; `[workspace]` is a label until Phase 15 (no lookup endpoint; KI-027).
- **Timeline** is a pure module (`lib/timeline.ts`): classification by event family, canonical order preserved from the API,
  client-side class filters, grouping of consecutive events sharing a span (collapsible), first-causal-error detection,
  retry markers. Rendering is a thin virtualized list. Events page in progressively (500/page) with progress shown.
- Keyboard: `j`/`k` or arrows move the selection, `Enter` opens the drawer, `Esc` closes, `/` focuses nothing destructive.
- Untrusted payloads render only as text (`react/no-danger` is already an error).

## Acceptance criteria (command-checkable)
1. `pnpm --filter @abb/web gen:api:check` clean; client types derive from `openapi.json`.
2. Fixtures exist: success, failure+retry, expensive, 10,000-event stress; deterministic (test).
3. Component/unit tests: timeline ordering, filters, grouping, first-error, drawers (LLM/tool/error/generic), run states
   (running/success/failed/waiting), runs list filters + pagination, dashboard math, loading/empty/error states.
4. Playwright smoke on fixtures; Playwright on a real ingested run (API on port 8100, worker, seed + direct HTTP ingestion).
5. 10,000-event fixture: DOM row count stays bounded (asserted in Playwright) while scrolling to the end.
6. Accessibility: axe has no serious/critical violations on dashboard, list, detail, drawer; focus returns after the drawer;
   status is never colour-only.
7. Screenshots captured in `docs/screenshots/phase-4/`.
8. `scripts/quality.sh full` exits 0.

## Verification plan
vitest + Playwright (system Chrome, no browser download) against `next start` on port 3100; real run via the API CLI and
`POST /v1/events/batch` into a dedicated database `abb_p4` (never the shared dev/test databases).

## Risks
Read API lacks aggregates and project lookup: dashboard figures are computed over the latest 200 runs and labelled as such.

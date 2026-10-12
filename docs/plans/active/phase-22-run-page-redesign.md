# Phase 22: Run-page redesign, "Flight recorder trace"

Status: Planned (2026-10-12). Decisions D1-D12 were settled with the user one question at a time (grill, 2026-10-12).
Owner: web + api. Branch: `feature/phase-22-run-redesign` (from `main` `c7bf3e8`).
Depends on: Phase 21 (merged, PR #119). Phase 12 (visual replay) builds on this page next.
Spec refs: §62 invariants (INV-2 derived state rebuildable, INV-5 withheld content), `docs/design/ui-direction.md`, ADR-110.

## Outcome

An engineer whose agent just failed opens the run page and, within five seconds and without scrolling or clicking, can say where it went wrong, how many retries it caused, what they cost, and where the run diverged. Colour only ever means a problem. The same language runs through the runs list, compare and analytics.

## Non-goals

- No change to what the data says: same events, same viewer withholding, same role matrix (INV-5).
- No replay (Phase 12), no policies or approvals (Phase 14), no alerts (Phase 17).
- No landing or marketing page (Phase 20).
- No weakened tests. Existing specs may change only where the redesign legitimately moves a selector; each change is listed in the plan with the reason and keeps or strengthens its assertions.

## Decisions (settled with the user)

- D1 Primary user: an engineer whose agent just failed or misbehaved.
- D2 Success metric: the five-second first-error test on the fixture run at 1440 px and 375 px, plus a Playwright assertion that the first-error marker and the retry and cost figures are visible in the first viewport.
- D3 Run-page hierarchy: failure line, cost of the failure, quiet timeline, identity line, findings out of the main view (one line per finding in the inspector, two clicks).
- D4 The headline occupies one fixed place and adapts: failed / timed out / blocked (first error and its step), running (step, last event age, live dot), waiting or awaiting approval (what and how long), recovered (succeeded after N retries), clean success (completed in X, cost), cancelled (by whom, at which step). Skeleton, error and withheld variants for each.
- D5 Direction "Flight recorder trace": a fault line marks the first error on the timeline and the waterfall; rows above it are quiet, rows below it carry a faint tint; red is reserved for failure; amber for retry and waiting; a steel-blue interaction accent; status is a glyph plus word with no outline, only FAILED is filled.
- D6 First-error facts are computed on the server and stored with the run summary: `first_error_event_id`, `first_error_sequence`, `retries_after`, `seconds_after`, `cost_after`. Derived from events, rebuildable (INV-2), metadata so viewers see them. Needs an ADR, an API contract change and a rebuild path.
- D7 Search: a global top-bar search opened with `/` using the existing query grammar, with inline top results; a facet column on the runs list. Both edit the one query string, which stays the single source of truth and the URL. Viewer rules unchanged (ADR-101).
- D8 Runs list: its job is to show which runs need you. Failed, timed-out and blocked runs get a second line with the failure sentence; clean runs stay one quiet line; a "Needs attention" facet; the compare checkboxes appear only when comparing; events and errors move into row detail.
- D9 Illustration and empty states: the first-run empty state is a working setup guide (API key, SDK install, three-line snippet with copy, live switch to the first run). One small inline-SVG trace system (trace line, fault-line cut, recorder tape), token colours, used only on empty, error and not-found states. No hero art, stock images or mascot.
- D10 Typeface: add IBM Plex Serif medium (self-hosted woff2, OFL, `next/font/local`, same route as ADR-110), used only for the failure line and empty-state headlines.
- D11 Login and invite are rebuilt inside the product (tokens, type, brand mark, one sentence, side gutter at 375 px, keyboard path). The landing page waits for Phase 20.
- D12 Compare opens with a divergence line and the same fault-line cut over the aligned steps, with the summary tiles collapsed into one mono line of deltas; analytics opens with "where the money was wasted" (spend on failed runs and retries by agent and model, from the cost engine). Everything else on those pages only inherits the styling.

Also settled earlier: scope may include new features, decorative-but-purposeful motion, new fonts, dependencies and illustration, provided each new feature was grilled (done above). Motion language: 120-200 ms, ease-out `cubic-bezier(0.2, 0, 0, 1)`, transform and opacity only, no springs or bounce, reduced motion gives a static complete page.

## Design notes

- Palette (light default, dark follows system; every pair verified at 4.5:1 or better when the tokens are built): bg `#eef1f4`/`#12161b`, surface `#ffffff`/`#1a2027`, ink `#171b21`/`#e8ecf1`, muted `#566272`/`#a3adb9`, accent `#2a5f8f`/`#7db3e6`, fail `#b3261e`/`#ff8a80`, retry and wait `#8a5a00`/`#ffcb6b`. Old token names stay as aliases.
- Removed accessories: the seven-box metric strip (duration, cost and tokens become one mono line), outlined pills, most upper-case micro-labels, the always-open filter chip row (collapsed into one menu with a count).
- Motion: fault line scales in over 280 ms once on opening a failed run; headline crossfade 120 ms on state change; jump-to-error scrolls then fades a 600 ms outline; inspector 160 ms slide of 8 px with fade; live rows fade in over 180 ms with a 4 px rise and never stagger; row hover is an instant background; the recording dot loses its pulse (reduced motion off anyway).
- Brand mark: three SVG options drawn for the user to choose from before it ships.

## Known issues considered

Pulled in: KI-119, KI-120, KI-124, KI-125, KI-126 (shell debt the redesign touches), KI-127 (replace the fixed wait in the 220 KB log case and diagnose the human-verdict spec when specs may change). Stays deferred: KI-117, KI-118, KI-121, KI-122, KI-123 (test-infrastructure items with no UI effect); KI-121 gets its screenshots as part of step 8.

## Acceptance criteria

1. Five-second test: on the failed fixture run at 1440 px and 375 px the first-error marker, retry count and cost-after figures are visible in the first viewport (Playwright assertion).
2. First-error fields are returned by the run summary, equal a from-scratch recomputation over the events (rebuild test), and are identical for owner and viewer.
3. All six headline states render with skeleton, error and withheld variants (component tests).
4. The fault line appears on timeline and waterfall at the first error; rows above are quiet; red is used only for failure (token-use test).
5. Findings and evaluations reachable within two clicks from the run page.
6. Top-bar search opens with `/`, shows inline results, matches the query grammar, and a viewer's search never matches content (extends ADR-101 tests); facets edit the query string and the URL.
7. Runs list shows the failure line for failed, timed-out and blocked runs; "Needs attention" facet works; compare checkboxes appear only when comparing.
8. First-run empty state shows the setup guide and switches to the run when the first event arrives (E2E).
9. Compare opens with the divergence line; analytics opens with wasted spend, reconciled with the cost engine (test).
10. Login and invite have a side gutter at 375 px, a keyboard path and axe clean.
11. Axe serious/critical violations are zero on every page; every text pair is at least 4.5:1; reduced motion produces a static complete page (zero-motion contract extended).
12. Existing tests pass; every modified existing test is listed in the plan with its reason and keeps or strengthens its assertions.
13. `scripts/quality.sh full` green in CI; adversarial review has no open P0/P1.

## Verification plan

Focused unit and component tests per step; Playwright (fixtures and API-backed) for the five-second test, the search and the empty state; axe and before/after screenshots at 1440 px and 375 px; migration check (empty to head and previous head to new head) if the summary fields are stored; my own gate run after every step, then Fable review.

## Risks

- Moving findings changes where people look; covered by acceptance 5.
- Server-computed fields add a contract, a migration (or projection) and a rebuild path; covered by acceptance 2 and an ADR.
- Large scope: build in the order below and keep each step shippable; if review shows it too large, step 8-10 (list, search, compare, analytics, login) can split into a follow-up PR without breaking the core.

## Ordered steps (one commit each)

1. Tokens, status grammar, type scale, Plex Serif, brand-mark options (ADR for the direction).
2. API: first-error fields in the run summary, rebuild path, ADR, contract and `schema.d.ts`.
3. Run header: failure line, cost-of-failure pair, six states, identity line.
4. Timeline and waterfall fault line; quiet and tinted rows.
5. Findings and evaluations moved into the inspector; context column collapsed by default.
6. Motion layer per the spec above; zero-motion contract extended.
7. Runs list redesign with failure lines, facet column and top-bar search.
8. Setup-guide empty state and the illustration system.
9. Compare and analytics headlines; styling propagation to experiments and settings.
10. Login and invite rebuilt.
11. Verification, screenshots, KI updates, closing evidence, review, fixes, push and PR.

# Design pass 2: the six-step pipeline

Status: Prepared 2026-10-16 (not started). Starts ONLY after Phase 19 (19b and 19c) is fully done (user instruction). Source prompts, untouched: `docs/design/design-pass-2-prompts.md`. Trigger: the user found the current UI "very convoluted" aesthetically after running the Phase 21/22 design.

How to use this file: each step below is sized for one agent run. It names its inputs, the filled-in prompt, the deliverables, an acceptance check and the hand-off. Steps run in order; each reads the previous step's output. Steps 1 to 3 and 5's spec half are documents; step 4 starts changing code (tokens), step 5 builds screens, step 6 stress-tests, verifies and opens the PR. Decisions that the prompts say to ask the user about (for example prompt 4's "ask exactly ONE clarifying question") are decided by the assistant using its recommendation, recorded in ADR-166 with the alternatives, unless the user is present.

## Product facts (the placeholders, filled in once for every step)

- **Product:** Agent Black Box. A flight recorder, debugger and control plane for AI agents.
- **Type:** web app, desktop-first, must work at 375 px. Light and dark follow the system setting.
- **Industry:** developer tools / AI-agent observability.
- **Users:** developers and AI engineers who run coding and other agents (high technical comfort, short on time when something failed), plus tech leads and security reviewers who approve risky actions and read findings.
- **Core problem:** people cannot trust or debug what their agents did. Agent Black Box records every run so they can see exactly what happened, where it first went wrong, what it cost, and control what agents may do.
- **Key user actions:** (1) understand a run and its first failure fast; (2) compare runs and versions; (3) review findings and approve or deny risky actions; (4) set up alerts, API keys and limits; (5) manage retention, quotas and access.
- **Inspiration (assistant's recommendation, user may change):** Linear (restraint and hierarchy), Sentry (issue-first reading), Honeycomb and Datadog traces (what to show, but far less dense), Vercel and Stripe dashboards (clarity of settings).
- **Vibe words (recommendation):** calm, precise, instrument-like, editorial restraint. Fewer boxes, clearer hierarchy, one thing to look at first on every page.
- **Brand assets today:** a placeholder mark (option A of three in `docs/design/brand-mark-options.md`), IBM Plex Sans/Mono/Serif self-hosted, the palette and motion contract in `docs/design/ui-direction.md` (ADR-111).
- **Hard constraints:** WCAG AA minimum, aim for AAA on body text; light and dark; status never conveyed by colour alone; red is reserved for run failure unless step 1 justifies a change in ADR-166; fonts self-hosted and no third-party requests; plain CSS (no Tailwind, no component library); no new heavy dependencies; zero motion under `prefers-reduced-motion`; strict nonce CSP stays; existing tests are not weakened (copy-driven assertions may change, each listed with the reason).
- **Current state:** the screens listed below, `docs/design/ui-direction.md`, screenshots in `docs/screenshots/`, and the user's remark that it "looks very convoluted".
- **Target expansion and locales (step 6):** English only today; stress at +30%, +50% and +70% as a proxy for German, Finnish and Spanish.

### Screen inventory (derive the final list from `apps/web/src/app` in step 2)

Public: `/login`, `/invite`, no-access notice. Workspace shell `/w/[workspace]`: Overview (dashboard), Projects/[project] (Runs list, Run detail with tabs for events, evidence, findings, waterfall, replay, evaluations; Run compare; Analytics; Experiments and experiment detail), Security (findings), Approvals, Alerts (Rules, Destinations, History), Settings (Members, API keys, Audit, Policies, Pricing, Quotas, Security, Sessions, Retention). Cross-cutting: top bar and `/` search, setup guide, error/empty/loading states, the "trace expired" run states.

---

## Step 1: Direction (taste before pixels)

- **Prompt:** Prompt 4 in `design-pass-2-prompts.md`, filled with the product facts above.
- **Agent:** a strong design-reasoning model (Opus or Fable); read-only apart from writing docs.
- **Inputs:** `docs/design/ui-direction.md`, `docs/decisions/ADR-111*`, `docs/design/concepts/`, current screenshots (`docs/screenshots/phase-22`, `phase-19a`, `phase-19b`), and a short written audit of WHY it feels convoluted (count the number of distinct boxes, colours, type sizes, nav items and simultaneous stat blocks on the dashboard and the run page; the audit is evidence, not opinion).
- **Do:** the design read (one line), the three dials with justification (this is a data-dense tool for people under pressure: expect variance 3-4, motion 3-4, density 5-6 as a starting point; justify any change), the anti-default lock, the taste system (aesthetic name, ONE signature element, palette roles in OKLCH, type pairing, primary-viewport layout idea with ASCII, motion intent, voice cues, non-goals), and the first-build checklist.
- **Deliverables:** `docs/design/pass-2/01-direction.md` and ADR-166 "Design pass 2 direction" (decisions, alternatives rejected, which ADR-111 rules are kept or amended).
- **Acceptance:** one direction, not a moodboard; every dial justified; an explicit list of what is removed or demoted from the current UI; contradictions with ADR-111 called out and resolved.
- **Hand-off:** the taste system is the rubric for steps 2 to 6 and for the final review.

## Step 2: Structure (information architecture, flows, simplification)

- **Prompt:** Prompt 1 sections 1, 2 and 8 (IA, user flows, structural recommendations), plus the audit from step 1.
- **Inputs:** step 1 output, the route tree under `apps/web/src/app`, the rail and top-bar components, `docs/IMPLEMENTATION_PLAN.md` feature list.
- **Do:** the real sitemap derived from the code (not invented), the navigation recommendation (rail vs top vs a lighter rail; what to merge, demote or hide behind progressive disclosure: e.g. fold Approvals/Alerts/Security into one "Attention" area? decide with reasons), flows for onboarding (first sign-in and the setup guide), the core action (open a failed run and find the first error), comparing runs, approving an action, setting up an alert, error recovery (expired session, forbidden, offline), settings; decision points and every error/edge path; the screens likely to cause confusion or drop-off; accessibility notes per screen.
- **Deliverables:** `docs/design/pass-2/02-ia-and-flows.md` with a before/after sitemap and a list of removals.
- **Acceptance:** every current route appears exactly once, either kept, merged, moved or removed with a reason; the new nav has fewer top-level items than today unless justified.
- **Hand-off:** the new IA fixes the screen list for step 3.

## Step 3: Screens, wireframes and visual hierarchy

- **Prompts:** Prompt 2 (ASCII wireframes) and the visual-hierarchy template at the end of Prompt 5, with Prompt 1 section 3 (screen-by-screen breakdown).
- **Inputs:** steps 1 and 2.
- **Do:** for each priority screen: purpose, key elements, primary and secondary actions, navigation, then an ASCII wireframe at desktop (1440), tablet (768) and mobile (375) with the breakpoint reflow, and the visual-hierarchy spec (content priority, visual weight, grid and zones, type hierarchy, colour and contrast use, scanning path, focal point, responsive rules) with measurements. Priority order: Overview, Runs list, Run detail (the headline, fault line, tabs), Compare, Approvals, Alerts, Security, Settings page pattern (one pattern for all nine tabs), Login and no-access.
- **Deliverables:** `docs/design/pass-2/03-screens-and-wireframes.md`.
- **Acceptance:** one dominant focal point per screen; the same settings-page pattern reused; wireframes for the three widths; each screen states what was removed versus today.
- **Hand-off:** wireframes are the layout target for step 5.

## Step 4: Visual foundations (colour, type, tokens) and the first code change

- **Prompt:** Prompt 5 sections A and B (colour system, type scale, font pairing, hierarchy rules, token naming).
- **Inputs:** step 1 palette roles and type pairing, current CSS variables (`apps/web/src/app/*.css`, `globals.css`), the self-hosted fonts.
- **Do:** the primitive scale and semantic tokens (including status colours, dark-mode mapping with notes where a simple inversion fails), a contrast compliance table whose numbers are COMPUTED by a script (`scripts/` or a test, not estimated), the type scale (display to overline), font pairing and licensing (free fonts only, self-hosted; any change of typeface must keep the no-third-party-request rule), line-length and weight rules, and the token naming schema in Style Dictionary form (`apps/web/design-tokens/tokens.json`). Then IMPLEMENT the tokens: replace the existing CSS variables with the new ones behind the same names where possible, keep both themes working, add a test that fails if a token pair drops below its target contrast.
- **Deliverables:** `docs/design/pass-2/04-colour-type-tokens.md`, `apps/web/design-tokens/tokens.json`, the CSS token layer, the contrast test and its generated table.
- **Acceptance:** all text/background pairs meet AA (AAA for body text where the spec says so) in both themes; the app still builds and every existing test passes; screenshots of two screens before/after.
- **Hand-off:** tokens are the only source of colour and type for step 5.

## Step 5: Copy and states, then build the screens

- **Prompt:** Prompt 1 sections 4 to 7 (microcopy, errors, empty states, loading and transitions) for the spec half; then implementation.
- **Inputs:** steps 1 to 4, the existing copy in the components and tests.
- **Spec half (document first):** `docs/design/pass-2/05-microcopy-and-states.md`: per screen the title, supporting text, CTA labels, field labels and helper text; every error (validation, network, permission, session expired, quota and rate-limit refusals, trace expired, retention) with headline, body and recovery CTA; every empty state (first-time and zero-result) with illustration description, headline, supporting copy and CTA; loading and transition copy. Voice follows step 1's cues and the repo's rules (plain, no jargon, never echo content, no em-dash-heavy marketing tone).
- **Build half (in order, committing after each batch):** 5a shell, navigation, top bar, Login/no-access and the dashboard; 5b Runs list, Run detail with its tabs and the headline/fault line, Compare; 5c Approvals, Alerts, Security, Analytics, Experiments; 5d the Settings pages on the single settings pattern, including Quotas, Sessions and Retention. Use the tokens only; reuse and delete components rather than layering; remove what step 2 removed. Motion follows the intent set in step 1 and the existing zero-motion contract.
- **Deliverables:** the copy spec, the implemented screens, updated fixtures and component tests (assertions that depended on old copy change, each listed with the reason), new Playwright specs where a screen's structure changed, screenshots in `docs/screenshots/phase-pass2/` (restore unrelated churned screenshots).
- **Acceptance:** every screen in the new IA works at 1440, 768 and 375; axe is clean; console clean; keyboard flow intact; `pnpm test:e2e --workers=2` green; no existing behaviour tests weakened.
- **Hand-off:** a buildable app for step 6.

## Step 6: Length-expansion stress test, verification, review and PR

- **Prompt:** Prompt 3 (length-expansion stress test), then the repo's verify and review loop.
- **Inputs:** the built app and the copy spec.
- **Do:** list the high-risk strings (including those with placeholders: run names, agent slugs, workspace and project names, long error codes, counts), then actually test them: a pseudo-localisation switch for tests that expands strings by 30%, 50% and 70% (and a very long unbroken token), and a Playwright spec that walks every screen at 1440 and 375 under each expansion checking for horizontal overflow, clipped buttons, overlapping text and broken tables; apply fixes from the prompt's recommended-fixes list (wrap rules, max-widths, adaptive labels, tooltips only where truncation is acceptable). Then run the full web gates (tsc, eslint, prettier, vitest, build, fixtures Playwright with axe in light and dark and reduced motion), the real-API e2e scripts that drive the UI (auth, alerts, hardening, retention), and look at every screenshot. A Fable design review uses step 1's taste system and the user's "convoluted" complaint as the rubric (is there one focal point per screen, did the box and colour counts go down, is anything still decorative without purpose), plus an accessibility and code pass. Fix findings, re-verify.
- **Deliverables:** `docs/design/pass-2/06-stress-test-report.md`, the review record in `docs/plans/completed/`, the pull request (opened, never merged; the user merges) with before/after screenshots and the decision log (ADR-166).
- **Acceptance:** no overflow at +70% on any screen; zero open P0/P1 in the review; CI green; the PR description states what is least certain so the user can steer.

---

## Rules for every step

- **Base branch and order:** branch from the tip of the Phase 19 work once it is done (stack on 19c if it is not yet merged) and open the PR against `main` after those merge. Never work on `main`.
- **Heavy runs:** web suites and Playwright run under the shared mutex (`until mkdir /private/tmp/abb-heavy.lock 2>/dev/null; do sleep 20; done; trap 'rmdir /private/tmp/abb-heavy.lock' EXIT; <command>`), `--workers=2`.
- **Evidence:** every claim of "works" is a command and its observed output (VERIFIED/UNVERIFIED vocabulary from `AGENTS.md`); UI changes are exercised in a browser, not just built.
- **Never:** read or print `.env`; push without being asked to open the PR; merge; add attribution lines to commits or PR text; weaken, skip or delete a failing test.
- **Decisions:** when the user is away, choose the simplest production-quality option, record it in ADR-166 with the alternative, and say so in the report.
- **Definition of done for the whole pass:** the PR is open with CI green, the review record is filed, the before/after screenshots are attached, and the PROJECT_STATE note says what the user should look at first.

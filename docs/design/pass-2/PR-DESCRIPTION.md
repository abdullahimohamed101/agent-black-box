# Design pass 2: One Red Line

Redesign of the web app's look and navigation (ADR-166, `docs/design/pass-2/`). Branch `feature/design-pass-2`, stacked on the
Phase 19 work. **No API, schema, event-contract or migration changes**; `pnpm gen:api:check` is clean and no file under
`apps/api`, `packages` or `integrations` changed.

## Direction

The user found the UI busy and convoluted. ADR-166 sets "One Red Line": a calm, editorial-technical surface whose only
signature is the fault line. Red appears twice in the product, the 2 px fault line (with its label) and the status word Failed,
Timed out or Blocked; no red fills, tints, counts or bars. Plex Sans and Plex Mono only, seven type steps, weights 400 and 600,
sentence-case heads, eleven colour roles, 4 px controls, one hairline, no shadows, one tab component, one table, one notice.

## Before and after

| Screen | Before | After |
| --- | --- | --- |
| Runs (was Overview + Runs) | ![before](../../screenshots/phase-21/after/dashboard-1440-light.png) | ![after](../../screenshots/pass-2/step5a-runs-1440-light.png) |
| Failed run | ![before](../../screenshots/pass-2/step4-before-run-1440-light.png) | ![after](../../screenshots/pass-2/step5b-failed-1440-light.png) |
| Compare | ![before](../../screenshots/phase-21/after/compare-1440.png) | ![after](../../screenshots/pass-2/step5b-compare-1440-light.png) |
| Approvals | ![before](../../screenshots/phase-14/approvals-detail-1440-light.png) | ![after](../../screenshots/pass-2/step5c-review-approvals-detail-1440-light.png) |
| Alerts (now under Settings) | ![before](../../screenshots/phase-17/alerts-rules-1440-light.png) | ![after](../../screenshots/pass-2/step5c-settings-alerts-rules-1440-light.png) |
| Quotas (a Settings tab) | ![before](../../screenshots/phase-19a/quotas-1440-light.png) | ![after](../../screenshots/pass-2/step5c-settings-quotas-1440-light.png) |
| Analytics | ![before](../../screenshots/phase-7/analytics.png) | ![after](../../screenshots/pass-2/step5d-analytics-cost-1440-light.png) |
| An experiment | ![before](../../screenshots/phase-21/after/experiment-detail-1440.png) | ![after](../../screenshots/pass-2/step5d-experiment-controlled-1440-light.png) |
| Phone: Runs | ![before](../../screenshots/pass-2/step4-before-runs-375-light.png) | ![after](../../screenshots/pass-2/step5a-runs-375-light.png) |

More: `docs/screenshots/pass-2/` (every screen at 1440, 768 and 375, light and dark) and the counts in
`docs/design/pass-2/06-consistency-report.md`.

## Decision log highlights (full list: ADR-166)

- **One top bar replaces the left rail** and its switcher boxes: brand, Runs, Review, Analytics, Experiments, scope, search,
  person. Below 900 px the links fold into a Menu button. `nav[aria-label="Primary"]` keeps its name.
- **Overview is merged into Runs**: one sentence above the list instead of a dashboard of tiles. Old URLs redirect.
- **Review has tabs**: Approvals and Security findings are two tabs of one page (routes unchanged).
- **Alerts moved under Settings** (`/alerts` redirects and keeps `?tab=`).
- **One Settings pattern** for all ten tabs: grouped sub-navigation, title, one-sentence lede, hairline sections, no cards;
  Quotas' cards and progress bars became one row per quota.
- **Run page**: a title line, the headline sentence, five figures (the rest under Details), five tabs, a Steps column; the
  context column and the health dimensions are gone from the page.
- **Analytics and Experiments**: one headline sentence per report; an experiment states its winner in words (never "significant",
  never a cause).
- Motion keeps the fault-line draw and the inspector slide; the reduced-motion contract is unchanged.

## Test evidence (run in this branch, fixtures mode)

- `tsc --noEmit`, `eslint --max-warnings=0`, `prettier --check`, `pnpm tokens:check`, `pnpm gen:api:check`: clean.
- Vitest: 1721 passed. `pnpm build`: succeeds.
- Playwright (fixtures, `--workers=2`): 820 passed, 111 skipped (the real-API specs). Includes axe in light and dark at 1440, 768
  and 375, reduced motion, console clean, no sideways scroll.
- **Length-expansion stress test** (`apps/web/e2e/stress-expansion.spec.ts`): 27 screens x 3 widths x 2 schemes, each at +30%,
  +50%, +70% and +70% with unbroken names: 162 passed. It found and fixed five real breakages
  (`docs/design/pass-2/07-stress-test-report.md`).
- No existing assertion was weakened; assertions that depended on old copy were changed in step 5 with the reason listed in
  the step commits.

## UNVERIFIED

- **Real-API Playwright specs** edited mechanically and not run here (no API, worker or database in this environment):
  `e2e/security-real.spec.ts` and the other step 5c specs (KI-287), `e2e/analytics.spec.ts` (KI-293). They may need a selector
  fix on their first run through `scripts/*-e2e.sh`.
- The API and Python suites were not re-run (no change in them).
- A real locale: the stress test is a proxy (KI-297).

## Least certain (please steer)

- Top bar replacing the rail, and Overview folded into Runs: the biggest change to habits.
- Dropping health dimensions from the run page (they stay in the API).
- The tab strip scrolls without a visible hint on a phone (KI-298).

## Known issues filed

KI-276 to KI-300 in `docs/KNOWN_ISSUES.md`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

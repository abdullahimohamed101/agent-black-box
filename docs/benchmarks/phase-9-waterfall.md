# Phase 9 waterfall: browser-side cost

Status: recorded 2026-10-10 on the implementer's machine (Apple M5 Pro, Node v26.10.0, Chrome via Playwright, `next build` +
`next start`). The numbers are a record, not a tight gate: CI runners are shared, so the asserted gates are generous.

Workload: the stress fixture (`stressRun`, `apps/web/src/fixtures/runs.ts`): 10,000 events, **2,838 spans** (the plan estimated
about 3,900; the fixture's own mix gives 2,838, still above the 2,000-span auto-collapse threshold), one root agent span with
the others as children (depth 2, so auto-collapse has little to fold on this fixture; the DOM bound is what is exercised).

## Derivation and layout (vitest, `apps/web/tests/waterfall-perf.test.ts`, median of 5, VERIFIED)

| Step | Time | Gate |
| --- | --- | --- |
| `deriveSpans` from scratch, 10,000 events | 7.8 ms | < 1,500 ms |
| `SpanIndex.update` for one 500-event live batch (median of 20 batches) | 0.3 ms | < 1,500 ms |
| `buildTree` (2,838 spans) | 3.2 to 3.4 ms | sum of the three below < 1,500 ms |
| `treeRows` | 0.3 ms | |
| `criticalPath` | 0.6 to 0.7 ms | |

The incremental index does one O(n) id walk per events change (the same order as `mergeEvents`, KI-035) and re-derives only the
spans whose member list changed; the 20-batch equality test (`tests/spans.test.ts`) pins it to a from-scratch derivation.

## Interaction (Playwright `e2e/fixtures.spec.ts`, 10,000-event run in the waterfall, VERIFIED)

| Measure | Result | Gate |
| --- | --- | --- |
| `[role=row]` elements mounted | at most 80 (asserted) | <= 80 |
| Script time of a zoom key press up to the next animation frame, median of 5 | 16.1 and 16.5 ms in two runs (samples 1.2 to 17.4 ms) | < 100 ms |

The zoom figure is dominated by the wait for the next frame (about 16.7 ms at 60 Hz); the key handler and re-render take a
few milliseconds. Re-run: `cd apps/web && pnpm exec next build && pnpm exec playwright test e2e/fixtures.spec.ts`; the spec prints
`waterfall zoom step ...` and the vitest prints `waterfall-perf {...}` (`pnpm exec vitest run tests/waterfall-perf.test.ts
--disableConsoleIntercept`).

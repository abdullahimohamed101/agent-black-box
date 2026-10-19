import { test, type Page } from "@playwright/test";
import {
  BASE,
  CODING,
  FAILED,
  MULTI,
  VIEWER,
  enterReplay,
  firstErrorIndex,
  openRun,
  seekTo,
} from "./replay";
import { baselineCodingRun } from "../src/fixtures/codingRuns";
import { failureRetryRun, multiAgentRun } from "../src/fixtures/runs";
import { shotsDir } from "./shots-dir";

// Phase 12 screenshot set (docs/screenshots/phase-12): replay at the start, at the first error, at the end, the waterfall
// mid-play and a viewer's replay, at 1440 and 375 px in both colour schemes. Not an assertion suite. Only runs when
// SHOTS_PHASE12 is set, so the normal e2e run never rewrites committed screenshots.
const OUT = shotsDir("phase-12");
test.skip(!process.env.SHOTS_PHASE12, "set SHOTS_PHASE12=1 to take the screenshot set");

const failed = failureRetryRun();
const multi = multiAgentRun();
const coding = baselineCodingRun();

async function shot(page: Page, name: string) {
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(250);
  await page.screenshot({ path: `${OUT}/${name}.png` });
}

for (const scheme of ["light", "dark"] as const) {
  for (const [w, h] of [
    [1440, 900],
    [375, 812],
  ] as const) {
    test.describe(`${w} ${scheme}`, () => {
      test.use({ viewport: { width: w, height: h } });
      test.beforeEach(async ({ page }) => {
        await page.emulateMedia({ colorScheme: scheme, reducedMotion: "reduce" });
      });
      const name = (n: string) => `${n}-${w}-${scheme}`;

      test("failed run: start, first error, end", async ({ page }) => {
        await openRun(page, BASE, FAILED);
        await enterReplay(page);
        await shot(page, name("replay-start"));
        await seekTo(page, firstErrorIndex(failed));
        await shot(page, name("replay-first-error"));
        await seekTo(page, failed.events.length - 1);
        await shot(page, name("replay-end"));
      });

      test("multi-agent waterfall mid-play", async ({ page }) => {
        await openRun(page, BASE, MULTI, "?view=waterfall");
        await enterReplay(page);
        await seekTo(page, Math.floor(multi.events.length / 2));
        await shot(page, name("replay-waterfall-mid"));
      });

      test("viewer", async ({ page }) => {
        await openRun(page, VIEWER, CODING);
        await enterReplay(page);
        await seekTo(page, Math.floor(coding.events.length / 2));
        await shot(page, name("replay-viewer"));
      });
    });
  }
}

import { expect, test, type Page } from "@playwright/test";
import { BASE, STRESS, enterReplay, openRun, scrub } from "./replay";

// Acceptance 8 (fixtures build): the 10,000-event run scrubs start to end fast and plays at 5x without freezing the page.
// The bounds are generous (CI machines vary); the measured numbers are attached to the report as annotations.
test.use({ viewport: { width: 1440, height: 900 } });

/**
 * Move the scrub bar the way a drag does (native value, then an `input` event) and wait until the page has drawn the
 * new playhead: the footer counts the events shown, so it changes only after the whole page has re-rendered. Returns
 * milliseconds from the event to the first frame that shows it.
 */
function jump(page: Page, target: number): Promise<number> {
  return page.evaluate(async (to) => {
    const el = document.querySelector<HTMLInputElement>("input.replay-scrub")!;
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    const want = `${(to + 1).toLocaleString("en-US")} of`;
    const t0 = performance.now();
    set.call(el, String(to));
    el.dispatchEvent(new Event("input", { bubbles: true }));
    for (;;) {
      await new Promise((r) => requestAnimationFrame(() => r(null)));
      const done =
        el.value === String(to) &&
        (document.querySelector('[data-testid="progress"]')?.textContent ?? "").includes(want);
      if (done) return performance.now() - t0;
      if (performance.now() - t0 > 10_000) return Infinity;
    }
  }, target);
}

test("a scrub jump from start to end settles in well under half a second", async ({ page }) => {
  await openRun(page, BASE, STRESS);
  await enterReplay(page);
  const last = 9_999;
  await expect(scrub(page)).toHaveAttribute("max", String(last));
  const targets = [last, 0, 5_000, last, 100, 7_500, 0, last, 2_500, 0, 6_000, last];
  const times: number[] = [];
  for (const t of targets) times.push(await jump(page, t));
  const sorted = [...times].sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)]!;
  const report = `median ${median.toFixed(0)}, max ${sorted.at(-1)!.toFixed(0)}, all ${times.map((t) => t.toFixed(0)).join(" ")}`;
  test.info().annotations.push({ type: "scrub jumps (ms)", description: report });
  console.log(`scrub jumps (ms): ${report}`);
  for (const [i, t] of times.entries()) expect(t, `jump ${i} to ${targets[i]}`).toBeLessThan(500);
  expect(median).toBeLessThan(150);
  // and the header at the end is the whole run's
  await expect(page.getByTestId("replay-status")).toContainText("Completed");
});

async function probe(page: Page, ms: number) {
  return page.evaluate(async (span) => {
    const long: number[] = [];
    const po = new PerformanceObserver((l) => l.getEntries().forEach((e) => long.push(e.duration)));
    try {
      po.observe({ entryTypes: ["longtask"] });
    } catch {
      // not supported: the frame gaps below still bound it
    }
    let last = performance.now();
    let maxGap = 0;
    let frames = 0;
    const end = last + span;
    await new Promise<void>((resolve) => {
      const tick = (now: number) => {
        maxGap = Math.max(maxGap, now - last);
        last = now;
        frames++;
        if (now < end) requestAnimationFrame(tick);
        else resolve();
      };
      requestAnimationFrame(tick);
    });
    po.disconnect();
    return { maxGap, frames, longest: Math.max(0, ...long), longTasks: long.length };
  }, ms);
}

for (const view of ["timeline", "waterfall"] as const) {
  test(`5x playback keeps the page responsive (${view})`, async ({ page }) => {
    await openRun(page, BASE, STRESS, view === "waterfall" ? "?view=waterfall" : "");
    await enterReplay(page);
    await page.getByRole("button", { name: "5x" }).click();
    await page.getByRole("button", { name: "Play", exact: true }).click();
    const r = await probe(page, 3_000);
    const report = `max frame gap ${r.maxGap.toFixed(0)} ms, ${r.frames} frames, longest task ${r.longest.toFixed(0)} ms, ${r.longTasks} long tasks`;
    test.info().annotations.push({ type: `5x ${view}`, description: report });
    console.log(`5x ${view}: ${report}`);
    expect(r.maxGap).toBeLessThan(700);
    expect(r.longest).toBeLessThan(500);
    // it really advanced, and a click on Pause is honoured promptly
    const t0 = Date.now();
    await page.getByRole("button", { name: "Pause", exact: true }).click();
    await expect(page.getByRole("button", { name: "Play", exact: true })).toBeVisible();
    expect(Date.now() - t0).toBeLessThan(2_000);
    expect(Number(await scrub(page).inputValue())).toBeGreaterThan(50);
  });
}

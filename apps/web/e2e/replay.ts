import { expect, type Page } from "@playwright/test";
import type { FixtureRun } from "../src/fixtures/builder";

// Shared by the Phase 12 replay specs (fixtures build).
export const BASE = "/w/demo/projects/all";
export const VIEWER = "/w/viewer-only/projects/all";
export const RUN = (n: number) => `run_${String(n).padStart(26, "0")}`;
/** The failed fixture run (retries, one first error), the multi-agent run (skewed clocks), the 10,000-event run. */
export const FAILED = RUN(2);
export const MULTI = RUN(7);
export const STRESS = RUN(6);
export const CODING = RUN(11);

export const scrub = (page: Page) => page.getByRole("slider", { name: "Replay position" });
export const replayButton = (page: Page) =>
  page.getByRole("button", { name: "Replay", exact: true });
export const transport = (page: Page) => page.getByRole("region", { name: "Replay controls" });

/** Open a run and wait until every event has loaded (the footer stops saying "Loading events"). */
export async function openRun(page: Page, base: string, runId: string, query = "") {
  await page.goto(`${base}/runs/${runId}${query}`);
  await expect(page.getByTestId("progress")).toContainText(/\d[\d,]* of \d[\d,]* events|spans/, {
    timeout: 20_000,
  });
  await expect(page.getByTestId("progress")).not.toContainText("Loading events", {
    timeout: 20_000,
  });
}

export async function enterReplay(page: Page) {
  await replayButton(page).click();
  await expect(transport(page)).toBeVisible();
}

/** Move the playhead to an event index through the scrub bar, as a person dragging it would. */
export async function seekTo(page: Page, index: number) {
  await scrub(page).fill(String(index));
  await expect(scrub(page)).toHaveValue(String(index));
}

export async function figure(page: Page, label: string): Promise<string> {
  // one of the five readouts, or one that moved into Details (model calls, tool calls, files changed): its dd is the dt's sibling
  return page
    .locator('dl[aria-label="Run summary"] dt, #run-details dt')
    .filter({ hasText: new RegExp(`^${label}$`) })
    .first()
    .locator("xpath=following-sibling::dd[1]")
    .innerText();
}

/** Header figures at an event index, computed from the raw fixture events (not from the code under test). */
export function expectedAt(f: FixtureRun, i: number) {
  const so = f.events.slice(0, i + 1);
  const bad = (e: (typeof so)[number]) =>
    e.status === "error" || e.status === "timeout" || e.event_type.endsWith(".failed");
  let cost = 0;
  for (const e of so) {
    const c = e.attributes["cost.estimated_usd"];
    if (typeof c === "number") cost += c;
  }
  return {
    cost: Number(cost.toFixed(6)),
    retries: so.filter((e) => e.event_type === "retry.attempted").length,
    errors: so.filter(bad).length,
  };
}

export const firstErrorIndex = (f: FixtureRun) =>
  f.events.findIndex((e) => e.event_id === f.run.first_error!.event_id);

export const overflowX = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

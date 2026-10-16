import { expect, test } from "@playwright/test";
import { BASE, FAILED, MULTI, enterReplay, openRun, seekTo, transport } from "./replay";
import { multiAgentRun } from "../src/fixtures/runs";

// Acceptance 9 (fixtures build): on the multi-agent run, whose tester's clock runs behind (so its timestamps go backwards
// against the canonical order), spans appear and grow with the playhead, the agent tree follows the current agent and the
// transport says that timing across agents is approximate.
const multi = multiAgentRun();
test.use({ viewport: { width: 1440, height: 900 } });

const spanCount = async (page: import("@playwright/test").Page) => {
  const text = (await page.locator(".wf-count").textContent()) ?? "0 spans";
  return Number(/(\d[\d,]*) spans/.exec(text)![1]!.replace(/,/g, ""));
};

test("spans appear when the playhead reaches them and open ones grow", async ({ page }) => {
  await openRun(page, BASE, MULTI, "?view=waterfall");
  await enterReplay(page);
  await seekTo(page, 3);
  const early = await spanCount(page);
  const open = page.locator(".wf-row[data-unfinished]").first();
  const id = await open.getAttribute("id");
  expect(id).toBeTruthy();
  const width = async () =>
    parseFloat(
      await page
        .locator(`#${id}`)
        .locator(".wf-bar")
        .evaluate((el) => (el as HTMLElement).style.width),
    );
  const w1 = await width();
  await seekTo(page, Math.floor(multi.events.length / 2));
  const mid = await spanCount(page);
  await expect(page.locator(`#${id}`)).toHaveAttribute("data-unfinished", "true");
  const w2 = await width();
  await seekTo(page, multi.events.length - 1);
  const all = await spanCount(page);
  expect(early).toBeLessThan(mid);
  expect(mid).toBeLessThan(all);
  expect(all).toBe(new Set(multi.events.map((e) => e.span_id).filter(Boolean)).size);
  expect(w2).toBeGreaterThan(w1);
  // the same span, drawn closed, is as long as it ends up being
  const w3 = await width();
  expect(w3).toBeGreaterThanOrEqual(w2);
  // an open span says it is in progress
  await seekTo(page, Math.floor(multi.events.length / 2));
  await expect(page.getByRole("treegrid")).toContainText("in progress");
});

test("the agent tree highlights the agent that emitted the current event", async ({ page }) => {
  await openRun(page, BASE, MULTI);
  await enterReplay(page);
  const tree = page.getByTestId("agent-tree");
  const seen = new Set<string>();
  for (const i of [0, 4, 9, 14, 20, 28, multi.events.length - 1]) {
    if (i >= multi.events.length) continue;
    await seekTo(page, i);
    const agent = multi.events[i]!.agent_id;
    seen.add(agent);
    const current = tree.locator('[aria-current="true"]');
    await expect(current).toHaveCount(1);
    await expect(current).toContainText(agent);
  }
  expect(seen.size).toBeGreaterThan(2);
});

test("the skew note shows for the run with disagreeing clocks and not for a plain one", async ({
  page,
}) => {
  await openRun(page, BASE, MULTI);
  await enterReplay(page);
  await expect(transport(page)).toContainText("Timing across agents is approximate.");
  await openRun(page, BASE, FAILED);
  await enterReplay(page);
  await expect(transport(page)).not.toContainText("approximate");
});

test("the fault line and the current row appear on the waterfall as the playhead arrives", async ({
  page,
}) => {
  await openRun(page, BASE, MULTI, "?view=waterfall");
  await enterReplay(page);
  const errAt = multi.events.findIndex((e) => e.event_id === multi.run.first_error?.event_id);
  expect(errAt).toBeGreaterThan(0);
  await seekTo(page, errAt - 1);
  await expect(page.locator(".wf-fault-line")).toHaveCount(0);
  await seekTo(page, errAt);
  await expect(page.locator(".wf-fault-line")).toHaveCount(1);
  await expect(page.locator('.wf-row[aria-current="step"]')).toHaveCount(1);
});

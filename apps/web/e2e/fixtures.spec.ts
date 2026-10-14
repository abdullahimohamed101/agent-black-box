import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { openContext, settleAnimations } from "./motion";

const BASE = "/w/demo/projects/all";
// Fixture runs: ten earlier ones, the two coding-agent runs (Phase 11) and the blocked run (Phase 22 review P1-3).
const RUN_COUNT = 18;
const RUN = (n: number) => `run_${String(n).padStart(26, "0")}`;
const SHOTS = "../../docs/screenshots/phase-4";
const SHOTS10 = "../../docs/screenshots/phase-10";

async function axe(page: Page) {
  await settleAnimations(page);
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual(
    [],
  );
}

test("dashboard summarises the project", async ({ page }) => {
  await page.goto(BASE);
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  await expect(page.getByText("Success rate")).toBeVisible();
  await expect(page.getByText("Running agents")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Recent failures" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Migrate billing schema" }).first()).toBeVisible();
  await axe(page);
  await page.screenshot({ path: `${SHOTS}/dashboard.png`, fullPage: true });
});

test("runs list filters by status and keeps state in the URL", async ({ page }) => {
  await page.goto(`${BASE}/runs`);
  await expect(page.getByRole("table")).toBeVisible();
  const all = await page.locator("tbody tr").count();
  expect(all).toBe(RUN_COUNT);
  await page.getByLabel("Failed").check();
  await expect(page).toHaveURL(/status=FAILED/);
  await expect(page.locator("tbody tr")).toHaveCount(3); // billing migration, failure chain, wrong fix
  await expect(page.locator("tbody tr").first()).toContainText("Failed");
  await page.getByLabel("Agent").fill("nobody");
  await expect(page.getByText("No runs match these filters")).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(page.locator("tbody tr")).toHaveCount(RUN_COUNT);
  // the findings column reads as text: count and worst severity, or a dash with plain words
  const looping = page.locator("tbody tr", { hasText: "Resolve flaky docs search" });
  await expect(looping.getByTestId("findings-cell")).toContainText("1 · info");
  await expect(
    page.locator("tbody tr", { hasText: "Fix login session expiry" }).getByTestId("findings-cell"),
  ).toContainText("No findings");
  await axe(page);
  await page.screenshot({ path: `${SHOTS}/runs-list.png`, fullPage: true });
});

test("search: chips from the server parse, matched column, facets, refusal and keyboard use (fixtures)", async ({
  page,
}) => {
  const SHOTS16 = "../../docs/screenshots/phase-16";
  await page.goto(`${BASE}/runs`);
  const box = page.getByRole("searchbox", { name: "Search" });
  await box.fill("tool:run_migration");
  await box.press("Enter");
  await expect(page.getByRole("list", { name: "Search terms" })).toContainText(
    "tool:run_migration",
  );
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await expect(page.getByTestId("matched").first()).toContainText("tool:run_migration");
  await expect(page.getByRole("region", { name: "Narrow the results" })).toBeVisible();
  await expect(page).toHaveURL(/q=tool%3Arun_migration/);
  await axe(page);
  await page.screenshot({ path: `${SHOTS16}/fixtures-search-results.png`, fullPage: true });
  // a facet appends a term; a chip removes one
  await page.getByRole("button", { name: /^Failed/ }).click();
  await expect(page.getByRole("list", { name: "Search terms" })).toContainText("status:FAILED");
  await page.getByRole("button", { name: "Remove status:FAILED" }).click();
  await expect(page.getByRole("list", { name: "Search terms" })).not.toContainText("status:FAILED");
  // a query the server rejects is explained, with a caret, never shown as "no runs"
  await box.fill("tool:run_migration nope:1");
  await box.press("Enter");
  await expect(page.locator(".search-problem")).toContainText("could not be understood");
  await expect(page.getByText("No runs match")).toHaveCount(0);
  await axe(page);
  await page.screenshot({ path: `${SHOTS16}/fixtures-search-error.png`, fullPage: true });
  await box.fill("status:failed");
  await box.press("Enter");
  // three fixture runs failed: the billing migration, the failure chain (Phase 10) and the wrong fix (Phase 11)
  await expect(page.locator("tbody tr")).toHaveCount(3);
  // mobile: no horizontal scroll
  await page.setViewportSize({ width: 375, height: 812 });
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
});

test("failed run: headline, first error, drawer, keyboard navigation and focus return", async ({
  page,
}) => {
  await page.goto(`${BASE}/runs/${RUN(2)}`);
  await expect(page.getByTestId("headline")).toContainText(/Failed at step \d+/);
  await expect(page.getByTestId("headline-detail")).toContainText("2 retries");
  await expect(page.getByTestId("first-error")).toContainText("run_migration");
  await expect(page.getByTestId("progress")).toContainText("events shown");

  const timeline = page.getByRole("listbox", { name: /Run timeline/ });
  await timeline.focus();
  await page.keyboard.press("j");
  await expect(page.getByRole("option", { selected: true })).toContainText("run.started");
  await page.keyboard.press("k");
  await page.keyboard.press("e");
  const drawer = page.getByRole("dialog");
  await expect(drawer).toContainText("ConnectionTimeout");
  await expect(drawer.getByRole("heading", { name: /Error/ })).toBeVisible();
  await expect(drawer.getByRole("button", { name: "Close details" })).toBeFocused();
  await axe(page);
  await page.screenshot({ path: `${SHOTS}/run-failed-drawer.png`, fullPage: true });
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  await expect(timeline).toBeFocused();
});

test("filters and groups act on the timeline", async ({ page }) => {
  await page.goto(`${BASE}/runs/${RUN(2)}`);
  await expect(page.getByTestId("progress")).toContainText("events shown");
  const before = await page.getByRole("option").count();
  await page.getByLabel("Errors only").check();
  await expect(page.getByRole("option")).toHaveCount(4);
  await page.getByLabel("Errors only").uncheck();
  await page.getByRole("button", { name: "Collapse groups" }).click();
  expect(await page.getByRole("option").count()).toBeLessThan(before);
  await page.getByRole("button", { name: "Expand groups" }).click();
  expect(await page.getByRole("option").count()).toBe(before);
  // LLM drawer shows model fields and the lazily loaded payload.
  await page
    .getByRole("option", { name: /llm\.request\.completed/ })
    .first()
    .click();
  await expect(page.getByRole("dialog")).toContainText("Model call");
  await expect(page.getByRole("dialog").getByLabel("Event payload")).toBeVisible();
});

test("successful and in-progress runs read at a glance", async ({ page }) => {
  await page.goto(`${BASE}/runs/${RUN(1)}`);
  await expect(page.getByTestId("headline")).toContainText("Completed in");
  await expect(page.getByTestId("first-error")).toHaveCount(0);
  await page.screenshot({ path: `${SHOTS}/run-success.png`, fullPage: true });
  await page.goto(`${BASE}/runs/${RUN(4)}`);
  await expect(page.getByTestId("headline")).toContainText("Running");
  await page.goto(`${BASE}/runs/${RUN(5)}`);
  await expect(page.getByTestId("headline")).toContainText("Awaiting approval");
});

test("looping run shows a probable loop finding whose evidence jump selects the event", async ({
  page,
}) => {
  await page.goto(`${BASE}/runs/${RUN(8)}`);
  await openContext(page); // Phase 22 D3: findings live in the context panel, closed by default
  const panel = page.getByTestId("findings");
  await expect(panel.getByRole("heading", { name: "Findings" })).toBeVisible();
  await expect(panel.getByTestId("findings-state")).toContainText("Analysis is current");
  const loop = panel.getByTestId("finding");
  await expect(loop).toHaveCount(1);
  await expect(loop.getByRole("heading", { level: 3 })).toHaveText(
    "Probable: the agent may be looping",
  );
  await expect(loop).toContainText("Probable");
  // one line per finding: the explanation sits behind Details, the health dimensions behind their own disclosure
  await expect(loop.getByLabel("Pattern")).toBeHidden();
  await loop.getByText("Details", { exact: true }).click();
  await expect(loop.getByLabel("Pattern")).toContainText("tool:search_docs");
  await panel.getByText(/^Health:/).click();
  await expect(panel.getByTestId("health")).toContainText("Loops");
  await expect(page.getByTestId("progress")).toContainText("events shown");
  await loop.getByRole("button", { name: "Show evidence" }).click();
  const drawer = page.getByRole("dialog");
  await expect(drawer).toBeVisible();
  await expect(drawer).toContainText("tool.call.completed");
  await expect(page.getByRole("option", { selected: true })).toContainText("tool.call.completed");
  await axe(page);
  await page.screenshot({ path: `${SHOTS10}/run-findings-loop.png`, fullPage: true });
});

test("failed run: the failure chain lists linked events and each one opens", async ({ page }) => {
  await page.goto(`${BASE}/runs/${RUN(10)}`);
  await openContext(page);
  const chain = page.getByTestId("finding").filter({ hasText: "the run failed" });
  await chain.getByText("Details", { exact: true }).click();
  await expect(chain).toContainText("Observed");
  await expect(chain.getByRole("listitem")).toHaveCount(4);
  await expect(page.getByTestId("finding").filter({ hasText: "Probable:" })).toHaveCount(1);
  await expect(page.getByTestId("progress")).toContainText("events shown");
  await chain.getByRole("button", { name: "mutation · file.modified" }).click();
  await expect(page.getByRole("dialog")).toContainText("file.modified");
  await page.screenshot({ path: `${SHOTS10}/run-findings-chain.png`, fullPage: true });
});

test("analysis states are stated: processing and not analysed", async ({ page }) => {
  await page.goto(`${BASE}/runs/${RUN(4)}`);
  await openContext(page);
  await expect(page.getByTestId("findings-state")).toContainText("Analysing");
  await page.goto(`${BASE}/runs/${RUN(5)}`);
  await openContext(page);
  await expect(page.getByTestId("findings-state")).toContainText("Not analysed yet");
  await expect(page.getByTestId("health")).toContainText("No data");
});

test("unknown run shows a not-found state", async ({ page }) => {
  await page.goto(`${BASE}/runs/run_00000000000000000000000999`);
  await expect(page.locator('[data-state="error"]')).toContainText("Not found");
});

test("10,000-event run renders a bounded number of rows", async ({ page }) => {
  await page.goto(`${BASE}/runs/${RUN(6)}`);
  const progress = page.getByTestId("progress");
  await expect(progress).toContainText(/10,000 of 10,000 events shown/, { timeout: 45_000 });
  const rows = page.getByRole("option");
  expect(await rows.count()).toBeLessThan(80);
  const size = Number(await rows.first().getAttribute("aria-setsize"));
  expect(size).toBeGreaterThan(5_000);
  await page.getByRole("listbox").focus();
  await page.keyboard.press("End");
  await expect(page.getByRole("option", { selected: true })).toContainText("run.completed");
  expect(await rows.count()).toBeLessThan(80);
  await page.screenshot({ path: `${SHOTS}/run-stress-end.png` });
});

test("mobile layout has no horizontal page scroll", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  for (const path of [
    BASE,
    `${BASE}/runs`,
    `${BASE}/runs/${RUN(2)}`,
    `${BASE}/runs/${RUN(10)}`,
    `${BASE}/runs/${RUN(7)}?view=waterfall`,
  ]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    expect(overflow, path).toBeLessThanOrEqual(0);
  }
  await page.screenshot({ path: `${SHOTS}/run-mobile.png`, fullPage: true });
});

test("security headers are set and the CSP breaks nothing", async ({ page }) => {
  const problems: string[] = [];
  page.on("console", (m) => m.type() === "error" && problems.push(m.text()));
  page.on("pageerror", (e) => problems.push(e.message));
  const res = await page.goto(`${BASE}/runs/${RUN(2)}`);
  const h = res!.headers();
  expect(h["content-security-policy"]).toContain("frame-ancestors 'none'");
  expect(h["content-security-policy"]).toContain("object-src 'none'");
  expect(h["x-content-type-options"]).toBe("nosniff");
  expect(h["referrer-policy"]).toBe("no-referrer");
  await expect(page.getByTestId("first-error")).toBeVisible();
  const api = await page.request.get("/api/abb/v1/runs");
  expect(api.headers()["cache-control"]).toContain("no-store");
  expect(api.headers()["x-content-type-options"]).toBe("nosniff");
  expect(problems).toEqual([]);
});

// ---- Phase 9: multi-agent waterfall ----------------------------------------------------------------------------------
const WSHOTS = "../../docs/screenshots/phase-9";

test("multi-agent waterfall: lanes, nesting under skew, critical path, synchronized selection, keyboard", async ({
  page,
}) => {
  await page.goto(`${BASE}/runs/${RUN(7)}`);
  await openContext(page);
  await expect(page.getByTestId("agent-tree")).toContainText("Agents (5)");
  await page.getByRole("radio", { name: "Waterfall" }).check();
  await expect(page).toHaveURL(/view=waterfall/);
  const grid = page.getByRole("treegrid");
  await expect(grid).toBeVisible();
  await expect(page.getByTestId("progress")).toContainText("events in");

  // the tester ran in another process with a clock behind its parent: nested under the coder, flagged
  const rowOf = (agent: string) =>
    grid
      .getByRole("row")
      .filter({ has: page.locator(".wf-chip", { hasText: new RegExp(`^${agent}$`) }) });
  const coder = rowOf("coder").filter({ hasText: "coder" }).first();
  const tester = rowOf("tester").first();
  const level = async (row: typeof coder) => Number(await row.getAttribute("aria-level"));
  expect(await level(tester)).toBe((await level(coder)) + 1);
  await expect(tester.locator('[data-flag="skewed"]')).toHaveText("clock skew");
  await expect(coder.locator('[data-flag="skewed"]')).toHaveCount(0);
  await expect(page.getByText(/Critical path/).first()).toBeVisible();
  expect(await grid.locator('[role="row"][data-critical]').count()).toBeGreaterThan(0);

  // lanes: one per agent
  await page.getByRole("button", { name: "Lanes" }).click();
  await expect(grid.locator(".wf-lane")).toHaveCount(5);
  await axe(page);
  await page.getByRole("button", { name: "Tree" }).click();
  await page.screenshot({ path: `${WSHOTS}/waterfall-multi-agent.png`, fullPage: true });

  // keyboard only: move, open the drawer, close it, focus returns to the grid
  await grid.focus();
  await page.keyboard.press("j");
  await page.keyboard.press("j");
  await page.keyboard.press("Enter");
  const drawer = page.getByRole("dialog");
  await expect(drawer).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  await expect(grid).toBeFocused();

  // selection is shared: the selected span's opener is the selected row of the timeline
  const selectedSpanText = await grid
    .locator('[role="row"][aria-selected="true"] .wf-name')
    .innerText();
  await page.getByRole("radio", { name: "Timeline" }).check();
  await expect(page.getByRole("option", { selected: true })).toBeVisible();
  await expect(page).not.toHaveURL(/view=waterfall/);
  expect(selectedSpanText.length).toBeGreaterThan(0);
  await axe(page);

  // the first-error jump lands in whichever view is active
  await page.getByRole("radio", { name: "Waterfall" }).check();
  await page.getByRole("button", { name: "Show it" }).click();
  await expect(page.getByRole("dialog")).toContainText("HttpError");
  await expect(grid.locator('[role="row"][aria-selected="true"]')).toContainText("search_docs");
  await axe(page);
});

test("agent filter narrows the timeline and dims the waterfall", async ({ page }) => {
  await page.goto(`${BASE}/runs/${RUN(7)}`);
  await openContext(page);
  await page
    .getByTestId("agent-tree")
    .getByRole("button", { name: /^reviewer/ })
    .click();
  await expect(page.getByTestId("progress")).toContainText(/^\d+ of \d+ events shown/);
  expect(await page.locator(".tl-agent").allInnerTexts()).toEqual(
    expect.arrayContaining(["reviewer"]),
  );
  expect(new Set(await page.locator(".tl-agent").allInnerTexts())).toEqual(new Set(["reviewer"]));
  await page.getByRole("radio", { name: "Waterfall" }).check();
  expect(await page.locator('[role="row"][data-dim]').count()).toBeGreaterThan(0);
});

test("a deep link to the waterfall works and the failure run's root span is finished", async ({
  page,
}) => {
  await page.goto(`${BASE}/runs/${RUN(2)}?view=waterfall`);
  await expect(page.getByRole("treegrid")).toBeVisible();
  await expect(page.getByRole("radio", { name: "Waterfall" })).toBeChecked();
  await expect(page.getByRole("treegrid")).not.toContainText("no end recorded");
  await expect(page.getByRole("treegrid")).toContainText("error");
});

test("10,000-event run in the waterfall: bounded rows, auto-collapse, zoom stays cheap", async ({
  page,
}, testInfo) => {
  await page.goto(`${BASE}/runs/${RUN(6)}?view=waterfall`);
  await expect(page.getByTestId("progress")).toContainText(/10,000 events in/, {
    timeout: 45_000,
  });
  const grid = page.getByRole("treegrid");
  await expect(grid).toBeVisible();
  expect(await page.locator('[role="row"]').count()).toBeLessThanOrEqual(80);
  const spanCount = Number(
    (await page.locator(".wf-count").innerText()).replace(/[^0-9]/g, "").slice(0, 6),
  );
  expect(spanCount).toBeGreaterThan(2000);
  await expect(page.locator(".wf-count")).toContainText("deeper levels start collapsed");

  const times = await page.evaluate(async () => {
    const el = document.querySelector<HTMLElement>('[role="treegrid"]')!;
    el.focus();
    const out: number[] = [];
    for (let i = 0; i < 5; i++) {
      const t = performance.now();
      el.dispatchEvent(new KeyboardEvent("keydown", { key: i % 2 ? "-" : "+", bubbles: true }));
      await new Promise((r) => requestAnimationFrame(() => r(null)));
      out.push(performance.now() - t);
    }
    return out;
  });
  const median = [...times].sort((a, b) => a - b)[2]!;
  testInfo.annotations.push({
    type: "zoom-step-ms",
    description: `median ${median.toFixed(1)} of ${times.map((x) => x.toFixed(1)).join(", ")}`,
  });
  console.log(
    `waterfall zoom step (10,000-event run): median ${median.toFixed(1)} ms; ${times.map((x) => x.toFixed(1)).join(", ")}`,
  );
  expect(median).toBeLessThan(100);
  expect(await page.locator('[role="row"]').count()).toBeLessThanOrEqual(80);
});

test("evaluations: every state, the judge badge, hostile text as text, and the viewer's withheld view (fixtures)", async ({
  page,
}) => {
  const SHOTS11 = "../../docs/screenshots/phase-11";
  const alerts: string[] = [];
  page.on("dialog", (d) => {
    alerts.push(d.message());
    void d.dismiss();
  });
  await page.goto(`${BASE}/runs/${RUN(12)}`);
  await openContext(page);
  const section = page.getByTestId("evaluations");
  await expect(section.getByRole("heading", { name: "Evaluations", level: 2 })).toBeVisible();
  // four latest results and one earlier (failed) one inside "Earlier results"
  await expect(section.getByTestId("evaluation")).toHaveCount(5);
  for (const state of ["pending", "running", "completed"])
    await expect(section.locator(`[data-testid=evaluation][data-state=${state}]`)).toHaveCount(1);
  await expect(section.locator("[data-testid=evaluation][data-state=failed]")).toHaveCount(2);
  await expect(section.getByTestId("evaluation-error").first()).toContainText("TIMEOUT");
  await expect(section.getByTestId("evaluation-error").last()).toContainText("JOB_FAILED");
  await expect(section.getByTestId("judge-badge")).toHaveText("model judge, not ground truth");
  await expect(section.getByRole("button", { name: "Run evaluator" })).toBeVisible();
  await expect(section.getByRole("form", { name: "Record a verdict" })).toBeVisible();
  await axe(page);
  await section.scrollIntoViewIfNeeded();
  await section.screenshot({ path: `${SHOTS11}/fixtures-evaluations-wrong-fix.png` });

  // the baseline carries a hostile explanation: it is text, never markup
  await page.goto(`${BASE}/runs/${RUN(11)}`);
  await openContext(page);
  await expect(page.getByTestId("evaluation")).toHaveCount(4);
  await expect(page.getByTestId("explanation").first()).toContainText("<img src=x onerror=");
  expect(await page.locator("[data-testid=evaluations] img").count()).toBe(0);
  // Show evidence opens the cited test run in the drawer
  await page.getByRole("button", { name: "Show evidence" }).click();
  await expect(page.getByRole("dialog")).toContainText("shell.command");
  await page.keyboard.press("Escape");
  expect(alerts).toEqual([]);

  // a viewer sees scores and labels, never free text, and no controls
  await page.goto(`/w/viewer-only/projects/all/runs/${RUN(11)}`);
  await openContext(page);
  const viewer = page.getByTestId("evaluations");
  await expect(viewer.getByTestId("evaluation")).toHaveCount(4);
  await expect(viewer.getByTestId("explanation-withheld")).toHaveCount(2);
  await expect(viewer).not.toContainText("onerror");
  await expect(viewer.getByRole("button", { name: "Run evaluator" })).toHaveCount(0);
  await expect(viewer.getByRole("form", { name: "Record a verdict" })).toHaveCount(0);
  await axe(page);
  await viewer.screenshot({ path: `${SHOTS11}/fixtures-evaluations-viewer.png` });
});

test("runs list: pick two runs to compare (fixtures)", async ({ page }) => {
  await page.goto(`${BASE}/runs`);
  const picks = page.getByRole("checkbox", { name: /^Compare / });
  // Phase 22 D8: the tick boxes exist only once the person starts comparing
  await expect(picks).toHaveCount(0);
  await page.getByRole("button", { name: "Compare", exact: true }).click();
  await expect(picks).toHaveCount(RUN_COUNT);
  await expect(page.getByTestId("compare-link")).toHaveCount(0);
  await picks.nth(0).check();
  await picks.nth(1).check();
  await expect(page.getByTestId("compare-bar")).toContainText("2 of 2 runs picked.");
  await expect(picks.nth(2)).toBeDisabled();
  await expect(page.getByTestId("compare-link")).toHaveAttribute(
    "href",
    /\/runs\/compare\?left=run_\w+&right=run_\w+$/,
  );
  await axe(page);
  await page.getByRole("button", { name: "Clear picks" }).click();
  await expect(picks.nth(2)).toBeEnabled();
  await page.setViewportSize({ width: 375, height: 812 });
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
});

test("compare: divergence, keyboard, notices and the viewer's metadata-only view (fixtures)", async ({
  page,
}) => {
  const SHOTS11 = "../../docs/screenshots/phase-11";
  const url = (b: string) => `${b}/runs/compare?left=${RUN(11)}&right=${RUN(12)}`;
  await page.goto(url(BASE));
  await expect(page.getByRole("heading", { name: "Compare runs", level: 1 })).toBeVisible();
  await expect(page.getByTestId("compare-deltas")).toBeVisible();
  await page.getByText("All measures").click(); // the summary table sits behind a disclosure now
  await expect(page.getByTestId("compare-summary")).toBeVisible();
  await expect(page.getByTestId("aligned-steps")).toBeVisible();
  await expect(page.getByTestId("divergence-flag")).toHaveCount(1);
  await expect(page.getByTestId("outcome-flag")).toHaveCount(1);
  await expect(page.getByTestId("metadata-notice")).toHaveCount(0);
  await expect(page.getByTestId("compare-files")).toContainText("Only in the left run");
  await page.getByRole("button", { name: "Jump to divergence" }).click();
  await expect(page.locator("[data-divergence=true]")).toBeFocused();
  await expect(page.getByTestId("aligned-live")).toContainText("Divergence:");
  await page.keyboard.press("ArrowDown");
  await expect(page.getByTestId("aligned-row").nth(1)).toBeVisible();
  await axe(page);
  await page.screenshot({ path: `${SHOTS11}/fixtures-compare-owner.png`, fullPage: true });

  // a viewer's comparison is metadata only and says so; the file paths are withheld
  await page.goto(url("/w/viewer-only/projects/all"));
  // Phase 22 D12: the measures table is behind a disclosure; the one-line deltas lead the page.
  await expect(page.getByTestId("compare-deltas")).toBeVisible();
  await page.getByText("All measures").click();
  await expect(page.getByTestId("metadata-notice")).toContainText("metadata only");
  await expect(page.getByTestId("compare-files")).toHaveAttribute("data-withheld", "true");
  await expect(page.getByText("Only in the left run")).toHaveCount(0);
  await axe(page);
  await page.screenshot({ path: `${SHOTS11}/fixtures-compare-viewer.png`, fullPage: true });

  // no runs picked: a plain empty state; and a phone has no horizontal scroll
  await page.goto(`${BASE}/runs/compare`);
  await expect(page.getByText("Pick two runs to compare")).toBeVisible();
  await page.goto(url(BASE));
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(page.getByTestId("aligned-steps")).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
});

test("experiments: list, the control banner, warnings, metrics and forms by role (fixtures)", async ({
  page,
}) => {
  const SHOTS11 = "../../docs/screenshots/phase-11";
  const alerts: string[] = [];
  page.on("dialog", (d) => {
    alerts.push(d.message());
    void d.dismiss();
  });
  await page.goto(`${BASE}/experiments`);
  await expect(page.getByRole("heading", { name: "Experiments", level: 1 })).toBeVisible();
  await expect(page.getByTestId("experiment-list").getByRole("row")).toHaveCount(3);
  await expect(page.getByRole("navigation", { name: "Primary" })).toContainText("Experiments");
  await axe(page);
  // the second experiment: two settings differ, so it is not controlled; its hostile name is text
  await page.getByRole("link", { name: /not.*controlled/ }).click();
  await expect(page.getByTestId("control-banner")).toContainText("Not controlled");
  await expect(page.getByTestId("control-banner")).toContainText("model, prompt");
  await expect(page.getByTestId("warnings")).toContainText("More than one setting differs");
  expect(await page.locator("h1 b").count()).toBe(0);
  await expect(page.getByRole("form", { name: "Add a variant" })).toBeVisible();
  await axe(page);
  // the first: controlled, with metrics and a link to compare with the baseline run
  await page.goto(`${BASE}/experiments`);
  await page.getByRole("link", { name: "Session fix: second patch" }).click();
  await expect(page.getByTestId("control-banner")).toContainText("Controlled");
  await expect(page.getByTestId("metrics")).toContainText("Cost, 95th percentile");
  await page.getByRole("link", { name: "Compare with baseline run" }).click();
  await expect(page.getByTestId("aligned-steps")).toBeVisible();
  await page.goBack();
  await axe(page);
  await page.screenshot({ path: `${SHOTS11}/fixtures-experiment.png`, fullPage: true });
  expect(alerts).toEqual([]);

  // a viewer reads the same page without forms or the evaluate button
  await page.goto("/w/viewer-only/projects/all/experiments");
  await page.getByRole("link", { name: "Session fix: second patch" }).click();
  await expect(page.getByTestId("control-banner")).toBeVisible();
  await expect(page.getByRole("form", { name: "Add a variant" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Evaluate all" })).toHaveCount(0);
  await axe(page);
});

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { settleAnimations } from "./motion";
import { signIn, workspace } from "./session";

// Runs only through scripts/multiagent-e2e.sh: the multi-agent example (threads, an asyncio task and a subprocess whose
// clock is 1.5 s behind) already wrote its run through the SDK into a real API; the built web server on :3104 reads
// it through its proxy. The owner session sees everything; the viewer session sees structure but no withheld content.
const runId = process.env.E2E_MULTIAGENT_RUN_ID;
const viewerToken = process.env.E2E_VIEWER_SESSION_TOKEN;
test.skip(!runId || !viewerToken, "needs scripts/multiagent-e2e.sh");
test.use({ baseURL: "http://localhost:3104", viewport: { width: 1400, height: 1000 } });
const SHOTS = "../../docs/screenshots/phase-9";
const COMMAND = "pytest -q"; // the tester's shell command: a content attribute a VIEWER must not see
const WITHHELD = "content withheld for your role";

const open = async (page: Page, view = "waterfall") => {
  await page.goto(
    `/w/${workspace()}/projects/all/runs/${runId}${view === "waterfall" ? "?view=waterfall" : ""}`,
  );
  await expect(page.getByTestId(view === "waterfall" ? "waterfall" : "agent-tree")).toBeVisible();
};
const rowOf = (page: Page, agent: string) =>
  page
    .getByRole("treegrid")
    .getByRole("row")
    .filter({ has: page.locator(".wf-chip", { hasText: new RegExp(`^${agent}$`) }) });
const level = async (row: ReturnType<typeof rowOf>) =>
  Number(await row.first().getAttribute("aria-level"));

async function axe(page: Page) {
  await settleAnimations(page);
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual(
    [],
  );
}

test.describe("as the workspace owner", () => {
  test.beforeEach(async ({ context, baseURL }) => signIn(context, baseURL!));

  test("the waterfall nests the skewed tester under the coder and highlights the critical path", async ({
    page,
  }) => {
    await open(page);
    const grid = page.getByRole("treegrid");
    await expect(page.getByTestId("agent-tree")).toContainText("Agents (5)");
    for (const agent of ["planner", "researcher", "coder", "tester", "reviewer"])
      await expect(rowOf(page, agent).first()).toBeVisible();

    // the tester's own clock is 1.5 s behind (it starts before its parent) yet it nests one level under the coder
    const coder = rowOf(page, "coder").filter({ hasText: "coder" }).first();
    const tester = rowOf(page, "tester").first();
    expect(await level(tester)).toBe((await level(coder)) + 1);
    await expect(tester.locator('[data-flag="skewed"]')).toHaveText("clock skew");
    await expect(coder.locator('[data-flag="skewed"]')).toHaveCount(0);
    // researcher and coder were delegated by the same span: siblings
    expect(await level(rowOf(page, "researcher"))).toBe(await level(coder));

    await expect(page.getByText(/Critical path/).first()).toBeVisible();
    expect(await grid.locator('[role="row"][data-critical]').count()).toBeGreaterThan(0);

    await page.getByRole("button", { name: "Lanes" }).click();
    await expect(grid.locator(".wf-lane")).toHaveCount(5);
    await axe(page);
    await page.getByRole("button", { name: "Tree" }).click();
    await page.screenshot({ path: `${SHOTS}/waterfall-real-run.png`, fullPage: true });
  });

  test("the agent tree lists five agents and the failed tool is found from either view", async ({
    page,
  }) => {
    await open(page, "timeline");
    const tree = page.getByTestId("agent-tree");
    await expect(tree).toContainText("Agents (5)");
    for (const agent of ["planner", "researcher", "coder", "tester", "reviewer"])
      await expect(tree.getByRole("button", { name: new RegExp(`^${agent}`) })).toBeVisible();
    await tree.getByRole("button", { name: /^researcher/ }).click();
    expect(new Set(await page.locator(".tl-agent").allInnerTexts())).toEqual(
      new Set(["researcher"]),
    );
    await tree.getByRole("button", { name: /^researcher/ }).click(); // toggles the filter off
    await page.getByRole("tab", { name: "Waterfall" }).click();
    await page.getByRole("button", { name: "Go to first error" }).click();
    await expect(
      page.getByRole("treegrid").locator('[role="row"][aria-selected="true"]'),
    ).toContainText("search_docs");
  });

  test("the owner can open the tester's command in the drawer", async ({ page }) => {
    await open(page);
    await rowOf(page, "tester").filter({ hasText: COMMAND }).first().click();
    await expect(page.getByRole("dialog")).toContainText(COMMAND);
  });
});

test.describe("as a viewer", () => {
  test.beforeEach(async ({ context, baseURL }) => signIn(context, baseURL!, viewerToken));

  test("sees the whole structure but none of the withheld text", async ({ page }) => {
    await open(page);
    const grid = page.getByRole("treegrid");
    for (const agent of ["planner", "researcher", "coder", "tester", "reviewer"])
      await expect(rowOf(page, agent).first()).toBeVisible();
    const coder = rowOf(page, "coder").filter({ hasText: "coder" }).first();
    expect(await level(rowOf(page, "tester"))).toBe((await level(coder)) + 1);
    await expect(rowOf(page, "tester").first().locator('[data-flag="skewed"]')).toHaveText(
      "clock skew",
    );
    expect(await grid.locator('[role="row"][data-critical]').count()).toBeGreaterThan(0);

    // rows, tooltips, lanes and the timeline: the command and the delegation payload text never appear
    await rowOf(page, "tester").first().hover();
    const everything = async () =>
      (await page.locator("body").innerText()) + (await page.content());
    expect(await everything()).not.toContain(COMMAND);
    await page.getByRole("button", { name: "Lanes" }).click();
    expect(await everything()).not.toContain(COMMAND);
    await page.getByRole("tab", { name: "Timeline" }).click();
    expect(await everything()).not.toContain(COMMAND);
    // the API's own placeholder is never shown as text either: the UI words the notice itself
    expect(await everything()).not.toContain("[withheld]");
    // the command's row says why it is blank
    await page.getByRole("tab", { name: "Waterfall" }).click();
    await expect(page.getByTestId("waterfall")).toContainText(WITHHELD);
    await axe(page);
    await page.screenshot({ path: `${SHOTS}/waterfall-viewer.png`, fullPage: true });
  });

  test("the drawer opened by a viewer shows no command", async ({ page }) => {
    await open(page);
    await rowOf(page, "tester").first().click();
    const drawer = page.getByRole("dialog");
    await expect(drawer).toBeVisible();
    await expect(drawer).not.toContainText(COMMAND);
    await expect(drawer).not.toContainText("[withheld]");
  });

  test("a viewer's drawer says the payload is withheld, not what it holds", async ({ page }) => {
    await open(page);
    await page.getByRole("tab", { name: "Timeline" }).click();
    // The example captures full payloads (delegation input, tool and shell output): at least one event
    // has a payload, and for a viewer its drawer must say so without showing it or the API's placeholder.
    const options = page.locator('[role="listbox"] [role="option"]:not([aria-label^="Group"])');
    const drawer = page.getByRole("dialog");
    let withheldDrawers = 0;
    const total = await options.count();
    for (let i = 0; i < total; i++) {
      await options.nth(i).click();
      await expect(drawer).toBeVisible();
      await expect(drawer).not.toContainText(COMMAND);
      await expect(drawer).not.toContainText("[withheld]");
      // wait for the payload section to settle (loaded, withheld or absent) before counting
      await expect(
        drawer
          .getByText(/No payload was captured/)
          .or(drawer.getByTestId("payload-withheld"))
          .or(drawer.getByLabel("Event payload")),
      ).toBeVisible();
      if ((await drawer.getByTestId("payload-withheld").count()) > 0) withheldDrawers++;
      await page.keyboard.press("Escape");
      await expect(drawer).toBeHidden();
    }
    expect(withheldDrawers).toBeGreaterThan(0);
  });
});

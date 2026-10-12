import { expect, test, type Page } from "@playwright/test";
import { withArtifacts } from "./artifacts";
import { openContext } from "./motion";
import { fixtureRuns } from "../src/fixtures";
import { API_WITHHELD, isContentAttribute } from "../src/fixtures/withhold";

// A viewer (no payload.read) must see the withheld wording and none of the captured content, in every view. The
// fixtures server now projects events and spans like the API, so these fail if a command, a path, a title, a data-*
// value or the API's marker reaches the page. Fixtures build; the owner workspace is the control.
const RUN = (n: number) => `run_${String(n).padStart(26, "0")}`;
const VIEWER = "/w/viewer-only/projects/all";
const OWNER = "/w/demo/projects/all";
const WITHHELD_TEXT = "content withheld for your role";

function secretsOf(n: number): string[] {
  const f = fixtureRuns().find((x) => x.run.id === RUN(n))!;
  const out = new Set<string>();
  for (const e of f.events)
    for (const [k, v] of Object.entries(e.attributes))
      if (isContentAttribute(k) && typeof v === "string" && v.length >= 3) out.add(v);
  const eventTypes = new Set(f.events.map((e) => e.event_type));
  for (const s of f.spans)
    if (s.name && ["shell", "file", "git"].includes(s.kind ?? "") && !eventTypes.has(s.name))
      out.add(s.name);
  return [...out];
}

/** Text and every attribute value in the document, plus the HTML itself: where a leak could hide. */
async function leaks(page: Page, secrets: string[]): Promise<string[]> {
  const surface = await page.evaluate(() => {
    const parts = [document.body.innerText, document.body.textContent ?? ""];
    for (const el of document.querySelectorAll("*"))
      for (const a of el.attributes) parts.push(a.value);
    return parts.join("\n");
  });
  const html = await page.content();
  return [API_WITHHELD, ...secrets].filter(
    (s) => surface.includes(s) || html.includes(s) || html.includes(s.replace(/&/g, "&amp;")),
  );
}

test.describe("viewer sees no captured content", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("the control: the owner does see the commands and paths", async ({ page }) => {
    await page.goto(`${OWNER}/runs/${RUN(11)}`);
    await expect(page.getByTestId("headline")).toBeVisible();
    await page.waitForLoadState("networkidle");
    const secrets = secretsOf(11);
    expect(secrets.length).toBeGreaterThan(3);
    expect((await leaks(page, secrets)).length).toBeGreaterThan(0);
  });

  for (const [name, n, view] of [
    ["coding run, timeline", 11, "timeline"],
    ["coding run, waterfall", 11, "waterfall"],
    ["coding run, evidence", 11, "evidence"],
    ["multi-agent run, timeline", 7, "timeline"],
    ["multi-agent run, waterfall", 7, "waterfall"],
  ] as const) {
    test(name, async ({ page }) => {
      await withArtifacts(page);
      await page.goto(`${VIEWER}/runs/${RUN(n)}?view=${view}`);
      await expect(page.getByTestId("progress")).toContainText("event", { timeout: 15_000 });
      await page.waitForLoadState("networkidle");
      expect(await leaks(page, secretsOf(n))).toEqual([]);
      expect(await page.locator("body").innerText()).toContain(WITHHELD_TEXT);
    });
  }

  test("inspector on commands and files, the story and the agent tree", async ({ page }) => {
    await page.goto(`${VIEWER}/runs/${RUN(11)}`);
    const list = page.getByRole("listbox", { name: /Run timeline/ });
    await expect(list).toBeVisible();
    await page.waitForLoadState("networkidle");
    const secrets = secretsOf(11);
    const options = list.getByRole("option");
    const count = await options.count();
    let opened = 0;
    for (let i = 0; i < count && opened < 6; i++) {
      const o = options.nth(i);
      if ((await o.getAttribute("aria-label"))?.startsWith("Group")) continue;
      if (!/Shell|Files & git/.test((await o.textContent()) ?? "")) continue;
      await o.click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await page.waitForLoadState("networkidle");
      expect(await leaks(page, secrets), `inspector ${i}`).toEqual([]);
      await page.keyboard.press("Escape");
      opened++;
    }
    expect(opened).toBeGreaterThan(2);
    // the story steps open the inspector on content-bearing events too
    await openContext(page);
    const steps = page.getByRole("complementary", { name: "Run context" }).getByRole("button");
    for (const i of [0, 1, 2]) {
      if (i >= (await steps.count())) break;
      await steps.nth(i).click();
      await page.waitForLoadState("networkidle");
      expect(await leaks(page, secrets), `story ${i}`).toEqual([]);
    }
    await page.goto(`${VIEWER}/runs/${RUN(7)}`);
    await openContext(page);
    await expect(page.getByTestId("agent-tree")).toBeVisible();
    expect(await leaks(page, secretsOf(7))).toEqual([]);
  });

  test("the context column, opened with the Context button", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto(`${VIEWER}/runs/${RUN(11)}`);
    await expect(page.getByRole("listbox", { name: /Run timeline/ })).toBeVisible();
    await openContext(page);
    await page.waitForLoadState("networkidle");
    expect(await leaks(page, secretsOf(11))).toEqual([]);
  });
});

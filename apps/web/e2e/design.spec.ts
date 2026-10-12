import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// Phase 21 design contract (fixtures build): the application frame, fonts, no third-party requests, axe in both colour
// schemes and no horizontal scroll on a phone, on every kind of page. Objective checks only; the paired screenshots
// (e2e/shots.spec.ts) carry the subjective half.
const BASE = "/w/demo/projects/all";
const RUN = (n: number) => `run_${String(n).padStart(26, "0")}`;
const ROUTES: [string, string][] = [
  ["overview", BASE],
  ["runs list", `${BASE}/runs`],
  ["failed run", `${BASE}/runs/${RUN(2)}`],
  ["multi-agent waterfall", `${BASE}/runs/${RUN(7)}?view=waterfall`],
  ["findings run", `${BASE}/runs/${RUN(10)}`],
  ["coding run", `${BASE}/runs/${RUN(11)}`],
  ["compare", `${BASE}/runs/compare?left=${RUN(11)}&right=${RUN(12)}`],
  ["analytics", `${BASE}/analytics`],
  ["experiments", `${BASE}/experiments`],
  ["viewer run", `/w/viewer-only/projects/all/runs/${RUN(7)}`],
  ["login", "/login"],
];

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual(
    [],
  );
}

const overflow = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

test.describe("1440x900 frame", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const [name, url] of ROUTES.filter(([n]) => n !== "login")) {
    test(`frame: ${name}`, async ({ page }) => {
      await page.goto(url);
      await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
      const box = async (sel: string) => (await page.locator(sel).first().boundingBox())!;
      const width = await page.evaluate(() => document.documentElement.clientWidth);
      const main = await box("main#main");
      const rail = await box("aside.rail");
      const top = await box("header.topbar");
      // no centred column: content starts at the rail's edge and ends at the viewport's edge
      expect(rail).toMatchObject({ x: 0, width: 224 });
      expect(main.x).toBe(224);
      expect(main.x + main.width).toBe(width);
      expect(top.height).toBe(44);
      await expect(page.locator("main#main")).toHaveAttribute("tabindex", "-1");
    });
  }

  test("frame: the sign-in page has no rail", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByRole("navigation", { name: "Primary" })).toHaveCount(0);
    await expect(page.locator("aside.rail")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: /Sign in/ })).toBeVisible();
  });

  test("focus order: skip link, then the rail, then the page", async ({ page }) => {
    await page.goto(`${BASE}/runs`);
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
    const order = await page.evaluate(() =>
      [...document.querySelectorAll("a[href], button, summary")]
        .map((e) => (e.closest("aside.rail") ? "rail" : e.closest("header") ? "top" : "main"))
        .filter((v, i, a) => i === 0 || a[i - 1] !== v),
    );
    // the skip link sits outside the rail and the top bar (the top bar has no focusable control on a list page)
    expect(order.slice(0, 3)).toEqual(["main", "rail", "main"]);
  });

  test("fonts are self-hosted and applied; no request leaves the origin", async ({ page }) => {
    const origins = new Set<string>();
    page.on("request", (r) => origins.add(new URL(r.url()).origin));
    await page.goto(`${BASE}/runs/${RUN(2)}`);
    await page.evaluate(async () => {
      await Promise.all(
        ['400 1rem "IBMPlexSans"', '600 1rem "IBMPlexSansCondensed"', '400 1rem "IBMPlexMono"'].map(
          (f) => document.fonts.load(f),
        ),
      );
    });
    for (const face of [
      '400 1rem "IBMPlexSans"',
      '600 1rem "IBMPlexSansCondensed"',
      '400 1rem "IBMPlexMono"',
    ])
      expect(await page.evaluate((f) => document.fonts.check(f), face)).toBe(true);
    const fam = (sel: string) =>
      page
        .locator(sel)
        .first()
        .evaluate((e) => getComputedStyle(e).fontFamily);
    expect(await fam("body")).toContain("IBMPlexSans,");
    expect(await fam("h1")).toContain("IBMPlexSansCondensed");
    expect(await fam(".tl-type")).toContain("IBMPlexMono");
    expect(await page.evaluate(() => getComputedStyle(document.body).fontSize)).toBe("13px");
    expect([...origins]).toEqual(["http://localhost:3100"]);
  });
});

for (const scheme of ["dark", "light"] as const) {
  test.describe(`axe, ${scheme}`, () => {
    test.use({ viewport: { width: 1440, height: 900 }, colorScheme: scheme });
    for (const [name, url] of ROUTES)
      test(`${name}`, async ({ page }) => {
        await page.goto(url);
        await page.evaluate(() => document.fonts.ready);
        await axe(page);
      });
  });
}

test.describe("375 px", () => {
  test.use({ viewport: { width: 375, height: 812 } });
  for (const [name, url] of ROUTES)
    test(`no horizontal scroll: ${name}`, async ({ page }) => {
      await page.goto(url);
      await page.evaluate(() => document.fonts.ready);
      expect(await overflow(page)).toBeLessThanOrEqual(0);
    });
  test("the rail is a strip under the top bar and its links stay reachable", async ({ page }) => {
    await page.goto(`${BASE}/runs`);
    const rail = (await page.locator("aside.rail").boundingBox())!;
    expect(rail.x).toBe(0);
    expect(rail.y).toBe(44);
    await expect(
      page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Runs" }),
    ).toBeInViewport();
  });
});

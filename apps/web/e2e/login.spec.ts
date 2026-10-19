import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { shotsDir } from "./shots-dir";

// Phase 22 step 10 (D11): sign-in and invitation are quiet pages with a side gutter, a keyboard path and no axe violations.
// The fixtures server renders the "Fixture mode" variant of the sign-in page; a server with no fixtures (and no reachable
// API) renders the real "Sign in" button: set E2E_LOGIN_URL to its origin to cover that variant (auth-e2e covers the whole
// flow against a real provider).
const SHOTS = shotsDir("phase-22/step-10");
const TOKEN = "T".repeat(43);
const REAL = process.env.E2E_LOGIN_URL;

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual(
    [],
  );
}
const overflow = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
/** Left and right space between the page's content and the window edge. */
const gutters = (page: Page) =>
  page.evaluate(() => {
    const boxes = [...document.querySelectorAll("main > *")].map((e) => e.getBoundingClientRect());
    return {
      left: Math.min(...boxes.map((b) => b.left)),
      right: Math.min(...boxes.map((b) => window.innerWidth - b.right)),
    };
  });

for (const [w, h] of [
  [1440, 900],
  [375, 812],
] as const) {
  for (const scheme of ["light", "dark"] as const) {
    test.describe(`${w}px ${scheme}`, () => {
      test.use({ viewport: { width: w, height: h }, colorScheme: scheme });

      test("sign-in: one sentence, a gutter, the keyboard path, axe clean", async ({ page }) => {
        await page.goto("/login");
        await expect(page.getByRole("heading", { level: 1 })).toHaveText(
          "Sign in to see what your agents did",
        );
        await expect(page.getByRole("navigation", { name: "Primary" })).toHaveCount(0);
        const g = await gutters(page);
        expect(g.left).toBeGreaterThanOrEqual(16);
        expect(g.right).toBeGreaterThanOrEqual(16);
        expect(await overflow(page)).toBeLessThanOrEqual(0);
        // Two Tab stops, each with a visible focus ring.
        await page.keyboard.press("Tab");
        const brand = page.getByRole("link", { name: "Agent BlackBox" });
        await expect(brand).toBeFocused();
        await page.keyboard.press("Tab");
        const action = page.getByRole("link", { name: /^(Continue|Sign in)$/ });
        await expect(action).toBeFocused();
        const ring = await action.evaluate((el) => {
          const s = getComputedStyle(el);
          return [s.outlineStyle, s.outlineWidth];
        });
        expect(ring).toEqual(["solid", "2px"]);
        await axe(page);
        await page.screenshot({ path: `${SHOTS}/login-fixture-${w}-${scheme}.png` });
      });

      test("invitation: the token leaves the address bar, the context is named, axe clean", async ({
        page,
      }) => {
        await page.goto(`/invite#${TOKEN}`);
        await expect(page.getByRole("heading", { level: 1 })).toHaveText("Join a workspace");
        await expect(
          page.getByText("An invitation to a workspace is waiting for you."),
        ).toBeVisible();
        expect(page.url()).not.toContain("#");
        expect(page.url()).not.toContain(TOKEN);
        const g = await gutters(page);
        expect(g.left).toBeGreaterThanOrEqual(16);
        expect(g.right).toBeGreaterThanOrEqual(16);
        expect(await overflow(page)).toBeLessThanOrEqual(0);
        await axe(page);
        await page.screenshot({ path: `${SHOTS}/invite-${w}-${scheme}.png` });
      });

      test("the real sign-in button and an error notice", async ({ browser }) => {
        test.skip(!REAL, "set E2E_LOGIN_URL to a web server that is not in fixtures mode");
        const context = await browser.newContext({
          baseURL: REAL,
          viewport: { width: w, height: h },
          colorScheme: scheme,
        });
        const page = await context.newPage();
        await page.goto("/login");
        const action = page.getByRole("link", { name: "Sign in" });
        await expect(action).toHaveAttribute("href", "/api/auth/login");
        await axe(page);
        await page.screenshot({ path: `${SHOTS}/login-${w}-${scheme}.png` });
        await page.goto("/login?error=rate_limited&return_to=%2Fw%2Facme%2Fprojects%2Fall");
        await expect(page.locator("main [role=alert]")).toContainText("Too many sign-in attempts");
        await expect(page.getByRole("link", { name: "Sign in" })).toHaveAttribute(
          "href",
          "/api/auth/login?return_to=%2Fw%2Facme%2Fprojects%2Fall",
        );
        expect(await overflow(page)).toBeLessThanOrEqual(0);
        await axe(page);
        await page.screenshot({ path: `${SHOTS}/login-error-${w}-${scheme}.png` });
        await context.close();
      });
    });
  }
}

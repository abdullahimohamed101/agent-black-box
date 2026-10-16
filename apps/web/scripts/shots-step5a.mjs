// Design pass 2 step 5a: screenshots of the frame, the Runs home and the pages outside the shell, light and dark, 1440 and 375.
// Usage: node scripts/shots-step5a.mjs [baseUrl] [only]. Needs a running fixtures server (port 3110 by default).
import { chromium } from "@playwright/test";

const base = process.argv[2] ?? "http://localhost:3110";
const only = process.argv[3];
// The sign-in pages need a server that is not in fixtures mode (the form is replaced by "Fixture mode" there).
const authBase = process.env.AUTH_BASE ?? "http://localhost:3111";
const RUN = (n) => `run_${String(n).padStart(26, "0")}`;
const OUT = "../../docs/screenshots/pass-2";
const empty = (page) =>
  page.route("**/api/abb/v1/runs?*", (r) =>
    r.fulfill({ json: { items: [], next_cursor: null, facets: null } }),
  );
const states = [
  ["runs", "/w/demo/projects/all/runs", async () => {}],
  ["runs-filtered", "/w/demo/projects/all/runs?q=status%3Afailed", async () => {}],
  ["runs-attention", "/w/demo/projects/all/runs?status=FAILED,TIMED_OUT,BLOCKED", async () => {}],
  ["runs-empty", "/w/demo/projects/all/runs", empty],
  [
    "runs-zero",
    "/w/demo/projects/all/runs?q=tool%3Anosuchtool",
    async (page) => {
      await page.route("**/api/abb/v1/runs?*q=tool*", (r) =>
        r.fulfill({
          json: {
            items: [],
            next_cursor: null,
            facets: { status: [], agents: [], tools: [], models: [], event_types: [] },
            query: null,
          },
        }),
      );
    },
  ],
  [
    "runs-help",
    "/w/demo/projects/all/runs",
    async () => {},
    async (page) => page.getByRole("button", { name: "Search help" }).click(),
  ],
  [
    "runs-compare",
    "/w/demo/projects/all/runs",
    async () => {},
    async (page) => page.getByRole("button", { name: "Compare" }).click(),
  ],
  [
    "runs-scope",
    "/w/demo/projects/all/runs",
    async () => {},
    async (page) => page.getByRole("button", { name: /demo \/ All projects/ }).click(),
  ],
  [
    "runs-account",
    "/w/demo/projects/all/runs",
    async () => {},
    async (page) => page.getByRole("button", { name: "Account" }).click(),
  ],
  [
    "runs-menu",
    "/w/demo/projects/all/runs",
    async () => {},
    async (page) => page.getByRole("button", { name: "Menu" }).click(),
    [375],
  ],
  [
    "runs-search",
    "/w/demo/projects/all/runs",
    async () => {},
    async (page) => {
      await page.keyboard.press("/");
      await page.keyboard.type("migr");
      await page.waitForTimeout(700);
    },
  ],
  ["login", "/login", async () => {}, null, null, authBase],
  ["login-no-access", "/login?error=no_access", async () => {}, null, null, authBase],
  ["invite", "/invite", async () => {}, null, null, authBase],
  ["no-workspaces-error", "/login?error=rate_limited", async () => {}, null, null, authBase],
  ["runs-768", "/w/demo/projects/all/runs", async () => {}, null, [768]],
  ["not-found", "/w/demo/projects/nope/runs", async () => {}],
  ["approvals", "/w/demo/approvals", async () => {}],
  ["run", `/w/demo/projects/all/runs/${RUN(2)}`, async () => {}],
];
const browser = await chromium.launch({ channel: "chrome" });
for (const scheme of ["light", "dark"]) {
  for (const [w, h] of [
    [1440, 900],
    [768, 1024],
    [375, 812],
  ]) {
    for (const [name, url, setup, act, widths, origin] of states) {
      if (only && !name.includes(only)) continue;
      if (widths ? !widths.includes(w) : w === 768) continue;
      const ctx = await browser.newContext({
        viewport: { width: w, height: h },
        colorScheme: scheme,
        reducedMotion: "reduce",
      });
      const page = await ctx.newPage();
      await setup(page);
      await page.goto((origin ?? base) + url);
      await page.waitForLoadState("networkidle", { timeout: 8000 }).catch(() => {});
      await page.waitForTimeout(300);
      if (act) {
        await act(page);
        await page.waitForTimeout(250);
      }
      await page.screenshot({ path: `${OUT}/step5a-${name}-${w}-${scheme}.png` });
      await ctx.close();
    }
  }
}
await browser.close();

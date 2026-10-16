// Design pass 2 step 5c: screenshots of Review (Approvals, Security findings), every Settings tab (Alerts included) and the person
// menu, light and dark, 1440, 768 and 375. Usage: node scripts/shots-step5c.mjs [baseUrl] [nameFilter]. Needs a running fixtures
// server (port 3110 by default). Some states change the fixture server's in-memory state (a saved retention shortening, a created
// destination): run the whole set once per server start.
import { chromium } from "@playwright/test";

const base = process.argv[2] ?? "http://localhost:3110";
const only = process.argv[3];
const OUT = "../../docs/screenshots/pass-2";
const APR = (n) => `apr_${String(n).padStart(26, "0")}`;
const S = "/w/demo/settings";
const empty = (pattern, body) => (page) =>
  page.route(pattern, (route) => route.fulfill({ json: body }));
let made = 0;
const states = [
  ["review-approvals", "/w/demo/approvals", null, null],
  ["review-approvals-detail", `/w/demo/approvals?approval=${APR(1)}`, null, null],
  ["review-approvals-decided", "/w/demo/approvals?state=decided", null, null, [1440]],
  [
    "review-approvals-empty",
    "/w/demo/approvals",
    empty("**/api/abb/v1/approvals?*", { items: [], next_cursor: null }),
    null,
  ],
  ["review-security", "/w/demo/security", null, null],
  ["review-security-filtered", "/w/demo/security?severity=critical", null, null, [1440]],
  [
    "review-security-clean",
    "/w/demo/security",
    empty("**/api/abb/v1/security/findings*", {
      items: [],
      next_cursor: null,
      facets: { severity: [], detector: [], project: [] },
    }),
    null,
    [1440, 375],
  ],
  ["settings-members", `${S}/members`, null, null],
  ["settings-sessions", `${S}/sessions`, null, null],
  ["settings-api-keys", `${S}/api-keys`, null, null],
  [
    "settings-api-keys-secret",
    `${S}/api-keys`,
    // The fixture API is read-only for keys: answer the create with a made-up token so the once-only panel can be seen.
    (page) =>
      page.route("**/api/abb/v1/api-keys", (route) =>
        route.request().method() === "POST"
          ? route.fulfill({
              status: 201,
              json: {
                key: { key_id: "key_demo", name: "ci-runner", scopes: ["events:write"] },
                token: "abb_demo_0000000000000000000000000000000000000000",
              },
            })
          : route.continue(),
      ),
    async (p) => {
      await p.getByLabel("Name").fill("ci-runner");
      await p.getByRole("button", { name: "Create key" }).click();
      await p.getByTestId("key-token").waitFor();
    },
    [1440, 375],
  ],
  ["settings-policies", `${S}/policies`, null, null],
  ["settings-security", `${S}/security`, null, null],
  ["settings-quotas", `${S}/quotas`, null, null],
  ["settings-retention", `${S}/retention`, null, null],
  [
    "settings-retention-pending",
    `${S}/retention`,
    null,
    async (p) => {
      // The fixture server keeps the saved shortening: after the first screenshot it is already pending.
      if (await p.getByTestId("tier-pending").count()) return;
      await p.getByLabel("Artifacts period").selectOption("days");
      await p.getByLabel("Artifacts days").fill("14");
      await p.getByRole("button", { name: "Save periods" }).click();
      await p.getByRole("button", { name: "Save and shorten" }).click();
      await p.getByTestId("tier-pending").first().waitFor();
      await p
        .getByTestId("tier-pending")
        .first()
        .evaluate((el) => el.scrollIntoView({ block: "center" }));
    },
    [1440, 375],
  ],
  ["settings-pricing", `${S}/pricing`, null, null],
  ["settings-alerts-rules", `${S}/alerts`, null, null],
  ["settings-alerts-destinations", `${S}/alerts?tab=destinations`, null, null],
  ["settings-alerts-history", `${S}/alerts?tab=history`, null, null],
  [
    "settings-alerts-secret",
    `${S}/alerts?tab=destinations`,
    null,
    async (p) => {
      await p.getByRole("button", { name: "Add a destination" }).click();
      await p.getByLabel("Name").fill(`Build webhook ${++made}`);
      await p.getByLabel("Webhook URL").fill("https://hooks.example.com/abb");
      await p.getByRole("button", { name: "Add destination" }).click();
      await p.getByTestId("secret-panel").waitFor();
    },
    [1440, 375],
  ],
  ["settings-audit", `${S}/audit`, null, null],
  [
    "settings-menu-open",
    `${S}/quotas`,
    null,
    async (p) => p.getByRole("button", { name: /^Section:/ }).click(),
    [768, 375],
  ],
  [
    "person-menu",
    "/w/demo/approvals",
    null,
    async (p) => p.locator(".person-button").click(),
    [1440, 375],
  ],
];
const browser = await chromium.launch({ channel: "chrome" });
for (const scheme of ["light", "dark"]) {
  for (const [w, h] of [
    [1440, 900],
    [768, 1024],
    [375, 812],
  ]) {
    for (const [name, url, setup, act, widths] of states) {
      if (only && !name.includes(only)) continue;
      if (
        widths
          ? !widths.includes(w)
          : w === 768 && !/quotas|alerts-rules|approvals$|security$|members/.test(name)
      )
        continue;
      const ctx = await browser.newContext({
        viewport: { width: w, height: h },
        colorScheme: scheme,
        reducedMotion: "reduce",
      });
      const page = await ctx.newPage();
      if (setup) await setup(page);
      await page.goto(base + url);
      await page.waitForLoadState("networkidle", { timeout: 8000 }).catch(() => {});
      await page.waitForTimeout(400);
      if (act) {
        await act(page);
        await page.waitForTimeout(300);
      } else await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: `${OUT}/step5c-${name}-${w}-${scheme}.png`, fullPage: false });
      await ctx.close();
    }
  }
}
await browser.close();

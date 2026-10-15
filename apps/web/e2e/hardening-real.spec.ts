import { expect, test, type Browser, type Page } from "@playwright/test";

// Phase 19a step 7, API-backed. Runs only through scripts/hardening-e2e.sh: two real API processes over one database, a worker,
// the DEVELOPMENT-ONLY fake OIDC provider and the built web server on :3220. A real browser signs in through the OIDC flow and
// (1) walks the main pages with a violation listener installed before any page script runs (zero CSP violations, nonce on every
// inline script), (2) reads the quota usage the script put in the database, (3) ends a second session of the same person from
// the Sessions page, (4) signs in as someone nobody invited and gets the plain notice. The fixtures spec e2e/csp.spec.ts walks
// every route; this one proves the same policy against the real API's data.
const WS = process.env.E2E_WORKSPACE;
const OWNER = process.env.E2E_OWNER_EMAIL;
const PROJECT = process.env.E2E_UI_PROJECT;
const RUN = process.env.E2E_UI_RUN;
const STRANGER = process.env.E2E_UI_STRANGER;
const ORIGIN = `http://localhost:${process.env.E2E_WEB_PORT ?? "3220"}`;
test.skip(!WS || !OWNER || !PROJECT || !RUN || !STRANGER, "needs scripts/hardening-e2e.sh");
test.use({ baseURL: ORIGIN, viewport: { width: 1440, height: 900 } });
test.describe.configure({ mode: "serial" });

declare global {
  interface Window {
    __violations?: string[];
  }
}

async function loginAs(page: Page, email: string) {
  await page.goto("/login");
  await page.getByRole("link", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Fake identity provider" })).toBeVisible();
  await page.getByLabel("Email").fill(email);
  await page.getByRole("button", { name: "Sign in" }).click();
}

async function signedIn(browser: Browser, email: string) {
  const context = await browser.newContext({
    baseURL: ORIGIN,
    viewport: { width: 1440, height: 900 },
  });
  const page = await context.newPage();
  await loginAs(page, email);
  await expect(page).not.toHaveURL(/\/login/);
  return { context, page };
}

let owner: Awaited<ReturnType<typeof signedIn>>;

test.beforeAll(async ({ browser }) => {
  owner = await signedIn(browser, OWNER!);
});
test.afterAll(async () => {
  await owner?.context.close();
});

test("the main pages open with the strict policy and zero violations", async () => {
  const { page } = owner;
  const problems: string[] = [];
  page.on("console", (m) => m.type() === "error" && problems.push(`console: ${m.text()}`));
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  await page.addInitScript(() => {
    window.__violations = [];
    document.addEventListener("securitypolicyviolation", (e) => {
      window.__violations!.push(
        `${e.violatedDirective} blocked=${e.blockedURI || "inline"} source=${e.sourceFile}:${e.lineNumber}`,
      );
    });
  });
  const base = `/w/${WS}`;
  const routes = [
    base,
    `${base}/projects/${PROJECT}/runs`,
    `${base}/projects/${PROJECT}/runs/${RUN}`,
    `${base}/projects/${PROJECT}/analytics`,
    `${base}/projects/${PROJECT}/experiments`,
    `${base}/security`,
    `${base}/alerts`,
    `${base}/approvals`,
    `${base}/settings`,
    `${base}/settings/quotas`,
    `${base}/settings/sessions`,
    `${base}/settings/api-keys`,
    `${base}/settings/policies`,
    `${base}/settings/audit`,
    `${base}/settings/members`,
    `${base}/settings/pricing`,
    `${base}/settings/security`,
  ];
  const seen: string[] = [];
  for (const route of routes) {
    const response = await page.goto(route);
    expect(response, route).not.toBeNull();
    await page.waitForLoadState("networkidle");
    const headers = response!.headers();
    const csp = headers["content-security-policy"] ?? "";
    expect(csp, route).toMatch(/script-src 'self' 'nonce-[^']+' 'strict-dynamic'/);
    expect(csp, route).not.toContain("unsafe-eval");
    expect(headers["x-frame-options"], route).toBe("DENY");
    const state = await page.evaluate(async () => {
      await document.fonts.ready;
      const inline = [...document.scripts].filter((s) => !s.src);
      return {
        violations: window.__violations ?? [],
        inline: inline.length,
        unnonced: inline.filter((s) => !s.nonce).length,
      };
    });
    expect(state.violations, `${route}: policy violations`).toEqual([]);
    expect(state.inline, route).toBeGreaterThan(0);
    expect(state.unnonced, `${route}: inline scripts without the nonce`).toBe(0);
    seen.push(route);
  }
  // Pages are data-driven by the real API: a failed request would show up as a console error or a failed resource.
  expect(problems.filter((p) => !/favicon/.test(p))).toEqual([]);
  expect(seen).toHaveLength(routes.length);
});

test("the quotas page shows real usage: near the run limit, at the event limit, in words", async () => {
  const { page } = owner;
  await page.goto(`/w/${WS}/settings/quotas`);
  await expect(page.getByRole("heading", { name: "Quotas", level: 1 })).toBeVisible();
  await page.getByLabel("Project", { exact: true }).selectOption({ label: PROJECT! });
  const item = (quota: string) => page.locator(`[data-testid=quota-item][data-quota=${quota}]`);
  await expect(page.getByTestId("quota-item")).toHaveCount(4);
  await expect(item("runs_per_day")).toContainText("9 of 10 runs today (90%)");
  await expect(item("runs_per_day")).toContainText("Near limit");
  await expect(item("events_per_day")).toContainText("12 of 12");
  await expect(item("events_per_day")).toContainText("Limit reached");
  await expect(item("events_per_day").getByTestId("quota-refusing")).toContainText(
    "refused until midnight UTC",
  );
  await expect(item("agents")).toContainText("Within limit");
});

test("a second session of the same person is listed and ended from the Sessions page", async ({
  browser,
}) => {
  const second = await signedIn(browser, OWNER!);
  const { page } = owner;
  await page.goto(`/w/${WS}/settings/sessions`);
  await expect(page.getByRole("heading", { name: "Sessions", level: 1 })).toBeVisible();
  await expect(page.getByTestId("session-item").filter({ hasText: "This session" })).toHaveCount(1);
  const count = await page.getByTestId("session-item").count();
  expect(count).toBeGreaterThanOrEqual(2);
  // the other browser is signed in and working
  await second.page.goto(`/w/${WS}/settings/sessions`);
  await expect(second.page.getByRole("heading", { name: "Sessions", level: 1 })).toBeVisible();
  // end every session but this one's two: end the other browser's session (the only row with an End button besides older ones)
  const end = page.getByRole("button", { name: /End session on/ });
  const buttons = await end.count();
  expect(buttons).toBeGreaterThanOrEqual(1);
  await page.getByRole("button", { name: "Sign out everywhere else" }).click();
  await page.getByRole("button", { name: "Confirm sign out everywhere else" }).click();
  await expect(page.getByTestId("session-item")).toHaveCount(1);
  await expect(page.getByTestId("session-status")).toContainText(
    /Signed out \d+ other sessions?\./,
  );
  // the revoked session stops at once: its next navigation goes to the sign-in page
  await second.page.reload();
  await expect(second.page).toHaveURL(/\/login/);
  await second.context.close();
  // this one keeps working
  await page.reload();
  await expect(page.getByRole("heading", { name: "Sessions", level: 1 })).toBeVisible();
});

test("someone nobody invited signs in at the provider and gets the plain notice", async ({
  browser,
}) => {
  const context = await browser.newContext({ baseURL: ORIGIN });
  const page = await context.newPage();
  await loginAs(page, STRANGER!);
  await expect(page).toHaveURL(/\/login\?error=no_access/);
  await expect(page.getByText("No access yet. Ask a workspace owner to invite you.")).toBeVisible();
  expect(await page.content()).not.toContain(STRANGER!);
  const cookies = (await context.cookies(ORIGIN)).map((c) => c.name);
  expect(cookies.filter((n) => /session/.test(n))).toEqual([]);
  await context.close();
});

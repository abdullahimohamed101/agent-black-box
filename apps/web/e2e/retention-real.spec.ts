import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Browser, type Page } from "@playwright/test";
import { settleAnimations } from "./motion";

// Phase 19b step 6, API-backed. Runs only through scripts/retention-e2e.sh: the real API and worker, the retention job having really
// enforced against the data, the DEVELOPMENT-ONLY fake OIDC provider and the built web server on :3230. A real browser signs in as
// the workspace owner and (1) opens the Retention settings page (mode banner, the four tiers with their source, preview, the shorten
// confirmation, "Last passes"), saving a shorter period through the real API; (2) opens a run page in each trace state. Axe has
// nothing serious or critical and the console stays clean on every page.
const WS = process.env.E2E_WORKSPACE;
const OWNER = process.env.E2E_OWNER_EMAIL;
const PROJECT = process.env.E2E_UI_PROJECT;
const AVAILABLE = process.env.E2E_UI_RUN_AVAILABLE;
const EXPIRED = process.env.E2E_UI_RUN_EXPIRED;
const DELETING = process.env.E2E_UI_RUN_DELETING;
const GONE = process.env.E2E_UI_RUN_GONE;
const ORIGIN = `http://localhost:${process.env.E2E_WEB_PORT ?? "3230"}`;
test.skip(
  !WS || !OWNER || !PROJECT || !AVAILABLE || !EXPIRED || !DELETING || !GONE,
  "needs scripts/retention-e2e.sh",
);
test.use({ baseURL: ORIGIN, viewport: { width: 1440, height: 900 } });
test.describe.configure({ mode: "serial" });

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

async function axe(page: Page) {
  await settleAnimations(page);
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual(
    [],
  );
}

function watchConsole(page: Page): string[] {
  const seen: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error" && !/favicon/.test(m.text())) seen.push(m.text());
  });
  page.on("pageerror", (e) => seen.push(e.message));
  return seen;
}

let owner: Awaited<ReturnType<typeof signedIn>>;
test.beforeAll(async ({ browser }) => {
  owner = await signedIn(browser, OWNER!);
});
test.afterAll(async () => {
  await owner?.context.close();
});

const tier = (page: Page, name: string) =>
  page.locator(`[data-testid=retention-tier][data-tier=${name}]`);

test("the Retention page: mode, tiers, last passes, preview, the shorten confirmation, a real save", async () => {
  const { page } = owner;
  const errors = watchConsole(page);
  await page.goto(`/w/${WS}/settings/retention`);
  await expect(page.getByRole("heading", { name: "Retention", level: 1 })).toBeVisible();
  await expect(page.getByTestId("retention-tier")).toHaveCount(4);
  // The installation mode comes from the real API (this stack enforces).
  await expect(page.getByTestId("retention-mode")).toContainText("Retention is enforcing");
  // The periods the script set through the API (14 / 60 / 300 days) are shown as this workspace's choice.
  await expect(tier(page, "artifacts").getByTestId("tier-value")).toHaveText(
    "14 days (set for this workspace)",
  );
  await expect(tier(page, "trace").getByTestId("tier-value")).toHaveText(
    "60 days (set for this workspace)",
  );
  await expect(tier(page, "audit").getByTestId("tier-value")).toContainText("installation default");
  // The passes the job really wrote (enforced), newest first.
  await expect(page.getByTestId("retention-pass").first()).toContainText("Enforced");
  expect(await page.getByTestId("retention-pass").count()).toBeGreaterThan(2);
  await axe(page);

  // Preview the PROPOSED policy against the real data.
  await page.getByLabel("Artifacts period").selectOption("days");
  await page.getByLabel("Artifacts days").fill("10");
  await page.getByRole("button", { name: /Preview/ }).click();
  await expect(page.getByTestId("retention-preview")).toBeVisible();
  await axe(page);

  // Shortening asks first.
  const save = page.getByRole("button", { name: "Save periods" });
  await save.click();
  const confirm = page.getByTestId("retention-confirm");
  await expect(confirm).toBeVisible();
  await expect(confirm).toContainText("Artifacts: from 14 days to 10 days");
  await expect(confirm).toContainText("deleted permanently and cannot be restored");
  await axe(page);
  await page.getByRole("button", { name: "Save and shorten" }).click();
  await expect(page.getByTestId("save-status")).toContainText("Saved.");
  await page.reload();
  await expect(tier(page, "artifacts").getByTestId("tier-value")).toHaveText(
    "10 days (set for this workspace)",
  );
  expect(errors).toEqual([]);
});

test("a period below the floor is refused on the page", async () => {
  const { page } = owner;
  const errors = watchConsole(page);
  await page.goto(`/w/${WS}/settings/retention`);
  await expect(page.getByTestId("retention-tier")).toHaveCount(4);
  await page.getByLabel("Artifacts period").selectOption("days");
  await page.getByLabel("Artifacts days").fill("3");
  await page.getByRole("button", { name: "Save periods" }).click();
  await expect(
    page
      .locator("[role=alert]:not(#__next-route-announcer__)")
      .filter({ hasText: "at least 7 days" }),
  ).toBeVisible();
  await axe(page);
  expect(errors).toEqual([]);
});

const runUrl = (id: string) => `/w/${WS}/projects/${PROJECT}/runs/${id}`;

test("a run page in each trace state: available, being deleted, expired, and gone", async () => {
  const { page } = owner;
  const errors = watchConsole(page);

  await page.goto(runUrl(AVAILABLE!));
  await expect(page.getByTestId("trace-notice")).toHaveCount(0);
  await expect(page.getByTestId("progress")).toBeVisible();
  await axe(page);

  await page.goto(runUrl(DELETING!));
  const deleting = page.getByTestId("trace-notice");
  await expect(deleting).toHaveAttribute("data-state", "deleting");
  await expect(page.getByRole("tab")).toHaveCount(0);
  await axe(page);

  await page.goto(runUrl(EXPIRED!));
  const expired = page.getByTestId("trace-notice");
  await expect(expired).toHaveAttribute("data-state", "expired");
  await expect(expired).toContainText("The trace of this run was removed by the retention policy");
  await expect(page.getByRole("tab")).toHaveCount(0);
  await axe(page);

  // A run whose record is gone (the run-record tier): honest words, no trace request left hanging.
  await page.goto(runUrl(GONE!));
  await expect(page.getByText("This run no longer exists.")).toBeVisible();
  await axe(page);

  // The runs list marks the states in words.
  await page.goto(`/w/${WS}/projects/${PROJECT}/runs`);
  await expect(page.getByText("Trace expired").first()).toBeVisible();
  await axe(page);
  expect(errors.filter((e) => !/status of 404|Failed to load resource/.test(e))).toEqual([]);
});

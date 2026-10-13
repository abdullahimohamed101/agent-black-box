import AxeBuilder from "@axe-core/playwright";
import {
  expect,
  test,
  type Browser,
  type BrowserContext,
  type Locator,
  type Page,
} from "@playwright/test";
import { readFileSync } from "node:fs";
import { settleAnimations } from "./motion";

// Phase 14 step 5, API-backed. Runs only through scripts/policy-e2e.sh: a real API and worker, the DEVELOPMENT-ONLY fake OIDC
// provider, the built web server on :3200 and runs recorded by the real Python SDK against the starter policy. People sign in
// through the real browser flow (an admin who approves, an owner whose own key asked, a developer, a viewer, billing).
// The script starts the demo, waits until it is paused for approval, then runs the tests tagged for that stage.
const WS = process.env.E2E_WORKSPACE;
const EMAILS = {
  owner: process.env.E2E_OWNER_EMAIL,
  admin: process.env.E2E_ADMIN_EMAIL,
  developer: process.env.E2E_DEVELOPER_EMAIL,
  viewer: process.env.E2E_VIEWER_EMAIL,
  billing: process.env.E2E_BILLING_EMAIL,
} as const;
const STATE_FILE = process.env.E2E_STATE;
test.skip(!WS || !EMAILS.admin || !STATE_FILE, "needs scripts/policy-e2e.sh");
test.use({ baseURL: "http://localhost:3200", viewport: { width: 1440, height: 900 } });
test.describe.configure({ mode: "serial" });
const SHOTS = "../../docs/screenshots/phase-14";

type Shared = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
/** What the script's check steps saved for this stage (approval ids, run ids, projects). */
const shared = (): Shared => JSON.parse(readFileSync(STATE_FILE ?? "/dev/null", "utf8")) as Shared;

async function axe(page: Page) {
  await settleAnimations(page);
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual(
    [],
  );
}

/** From the web app's "Sign in" link, through the fake provider's page, back to wherever the app sends us. */
async function loginAs(page: Page, email: string, returnTo: string) {
  await page.goto(`/login?return_to=${encodeURIComponent(returnTo)}`);
  await page.getByRole("link", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Fake identity provider" })).toBeVisible();
  await page.getByLabel("Email").fill(email);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(new RegExp(returnTo.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
}

let browserRef: Browser;
const contexts: BrowserContext[] = [];
test.beforeAll(({ browser }) => {
  browserRef = browser;
});
test.afterAll(async () => {
  for (const c of contexts) await c.close();
});

async function signedIn(
  role: keyof typeof EMAILS,
  returnTo: string,
  options: { colorScheme?: "light" | "dark"; width?: number; height?: number } = {},
): Promise<Page> {
  const context = await browserRef.newContext({
    baseURL: "http://localhost:3200",
    colorScheme: options.colorScheme ?? "light",
    viewport: { width: options.width ?? 1440, height: options.height ?? 900 },
  });
  contexts.push(context);
  const page = await context.newPage();
  await loginAs(page, EMAILS[role] as string, returnTo);
  return page;
}

const runUrl = (run: string) => `/w/${WS}/projects/all/runs/${run}`;
const timelineRow = (page: Page, type: string, nth = 0): Locator =>
  page
    .getByRole("option")
    .filter({
      has: page.locator(".tl-type", { hasText: new RegExp(`^${type.replace(/\./g, "\\.")}$`) }),
    })
    .nth(nth);
const redLooking = (c: string) => {
  const [r, g, b] = (c.match(/\d+/g) ?? []).map(Number) as [number, number, number];
  return r > 150 && g < 80 && b < 80;
};
const noSideScroll = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);

/** The timeline is virtualized; the class filter keeps just the decisions so every one of them is rendered. */
async function onlyDecisions(page: Page) {
  await expect(page.getByRole("listbox", { name: /Run timeline/ })).toBeVisible();
  if (await page.getByLabel("Approvals & policy").isVisible()) return;
  await page.getByRole("button", { name: /^Filter/ }).click();
  for (const name of [
    "Run & agent",
    "Model calls",
    "Tools",
    "Files & git",
    "Shell",
    "Retries & limits",
    "Other",
  ]) {
    const box = page.getByLabel(name, { exact: true });
    if (await box.count()) await box.uncheck();
  }
  await expect(page.getByLabel("Approvals & policy")).toBeChecked();
}

/** The decisions on a finished (or waiting) run page: words, the policy-service marker, and no failure colour on any of them. */
async function expectDecisionWords(page: Page, words: string[]) {
  await onlyDecisions(page);
  for (const word of words)
    await expect(page.locator(".tl-pol").filter({ hasText: word }).first(), word).toBeVisible();
  const colors = await page
    .locator(".tl-pol")
    .evaluateAll((els) => els.map((e) => getComputedStyle(e).color));
  expect(colors.length).toBeGreaterThanOrEqual(words.length);
  for (const c of colors) expect(redLooking(c), `a red-looking colour: ${c}`).toBe(false);
  // The decision rows themselves are never error rows (the agent's own failed tool step is another matter).
  await expect(page.locator("[role=option][data-error] .tl-pol")).toHaveCount(0);
}

// -------------------------------------------------------------------------------------------------------- gate 1

for (const variant of ["approve", "deny"] as const)
  test(`@gate1-${variant} the admin sees the paused run and ${variant === "approve" ? "approves" : "denies"} the push`, async () => {
    const s = shared();
    const approval = s[`gate1_${variant}_approval`] as string;
    const run = s[`gate1_${variant}_run`] as string;
    const page = await signedIn("admin", runUrl(run));
    const problems: string[] = [];
    page.on("console", (m) => m.type() === "error" && problems.push(m.text()));
    page.on("pageerror", (e) => problems.push(e.message));

    // DURING the wait: the headline, the banner with real time left, the decisions so far.
    await expect(page.getByTestId("headline")).toContainText(/^Waiting for approval: github\.push/);
    const detail = page.getByTestId("headline-detail");
    await expect(detail).toContainText(/\d+ (min|s|h)/);
    const banner = page.getByTestId("approval-banner");
    await expect(banner).toContainText("Waiting for a person to approve github.push");
    const bannerText = (await banner.innerText()).replace(/\s+/g, " ");
    const minutes = /· (\d+) min/.exec(bannerText);
    const seconds = /· (\d+) s\b/.exec(bannerText);
    // The starter policy waits 15 minutes; the banner shows what is left of that, not a placeholder.
    expect(
      minutes ? Number(minutes[1]) >= 12 && Number(minutes[1]) <= 15 : seconds !== null,
      bannerText,
    ).toBe(true);
    await expect(page.getByText("Awaiting approval").first()).toBeVisible();
    await expectDecisionWords(page, [
      "Denied by deny-env-read v1",
      "Approval required by approve-push-main v1",
      "Approval requested",
    ]);
    await expect(timelineRow(page, "policy.action.blocked")).toContainText(
      "Recorded by the policy service",
    );
    await expect(timelineRow(page, "approval.requested")).toContainText(
      "Recorded by the policy service",
    );
    if (variant === "approve") {
      await page.screenshot({ path: `${SHOTS}/real-run-waiting.png`, fullPage: false });
      await axe(page);
    }

    // The human: the Approvals page lists the request with capability, resource, policy and version.
    await banner.getByRole("link", { name: "Open Approvals" }).click();
    await expect(page).toHaveURL(new RegExp(`/w/${WS}/approvals\\?approval=${approval}`));
    const panel = page.getByTestId("approval-detail");
    await expect(panel.getByRole("heading", { level: 2, name: "github.push" })).toBeVisible();
    await expect(panel).toContainText("origin main");
    await expect(panel).toContainText("approve-push-main");
    await expect(panel).toContainText("v1");
    await expect(panel).toContainText("Admin or above");
    await expect(panel).toContainText(/\d+ min/);
    const row = page.locator(`#apr-open-${approval}`).locator("xpath=ancestor::tr");
    await expect(row).toContainText("origin main");
    await expect(row).toContainText("approve-push-main");
    await expect(row.getByTestId("time-left")).toHaveText(/\d+ (min|s|h)/);
    await expect(page.getByTestId("approvals-count")).toContainText(/[1-9]/);
    if (variant === "approve") {
      await page.screenshot({ path: `${SHOTS}/real-approvals-detail.png`, fullPage: false });
      await axe(page);
    }

    const comment = variant === "approve" ? "Reviewed in policy-e2e." : "Not on main today.";
    await panel.getByLabel("Comment (optional)").fill(comment);
    await panel.getByRole("button", { name: variant === "approve" ? "Approve" : "Deny" }).click();
    await panel
      .getByRole("button", { name: variant === "approve" ? "Confirm approve" : "Confirm deny" })
      .click();
    await expect(panel.getByTestId("decision-saved")).toHaveText(
      variant === "approve" ? "You approved this request." : "You denied this request.",
    );
    await expect(panel).toContainText(comment);

    // The paused demo proceeds (or gives up): the run page shows the ending, in the policy service's words.
    await page.goto(runUrl(run));
    const ending = variant === "approve" ? "Approval used" : "Denied";
    await expect
      .poll(
        async () => {
          await page.reload();
          await onlyDecisions(page);
          return page.locator(".tl-pol", { hasText: new RegExp(`${ending}$`) }).count();
        },
        { timeout: 60_000, intervals: [1000, 2000] },
      )
      .toBeGreaterThan(0);
    // The agent's own blocked read is a failed tool step, so the run reads "Succeeded after recovering from an error".
    await expect(page.getByTestId("headline")).toContainText(/^(Completed in|Succeeded)/, {
      timeout: 30_000,
    });
    await expect(page.getByTestId("approval-banner")).toHaveCount(0);
    const words =
      variant === "approve"
        ? [
            "Denied by deny-env-read v1",
            "Approval requested",
            "Approved",
            "Approval used",
          ]
        : ["Denied by deny-env-read v1", "Approval requested", "Denied"];
    await expectDecisionWords(page, words);
    for (const type of variant === "approve"
      ? ["approval.granted", "approval.consumed"]
      : ["approval.denied"])
      await expect(timelineRow(page, type)).toContainText("Recorded by the policy service");
    // a file read by the agent has no such marker
    await page.reload();
    await expect(timelineRow(page, "file.read", 0)).not.toContainText("policy service");
    expect(await noSideScroll(page)).toBe(true);
    if (variant === "approve") {
      await page.screenshot({ path: `${SHOTS}/real-run-decisions.png`, fullPage: false });
      await axe(page);
    }
    expect(problems.filter((p) => /418|hydrat/i.test(p))).toEqual([]);
  });

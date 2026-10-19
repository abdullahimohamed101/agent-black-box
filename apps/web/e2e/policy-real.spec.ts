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
import { workspaceHeader } from "./session";
import { shotsDir } from "./shots-dir";

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
const SHOTS = shotsDir("phase-14");

type Shared = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
type Gate6 = { admin_only: string; self_ok: string; secret: string; owner_only: string };
type UiShared = { run: string; approval: string };
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
    await expect(page.getByTestId("approval-banner")).toBeVisible();
    if (variant === "approve") {
      await settleAnimations(page);
      await page.screenshot({ path: `${SHOTS}/real-run-waiting.png`, fullPage: false });
    }
    await expect(page.getByTestId("headline")).toContainText(
      /^Waiting for approval of github\.push/,
    );
    const detail = page.getByTestId("headline-detail");
    await expect(detail).toContainText(/\d+ (min|s|h)/);
    const banner = page.getByTestId("approval-banner");
    await expect(banner).toContainText("Waiting for a person to approve github.push");
    // the time left comes from the approvals read, a moment after the banner itself
    await expect(banner).toContainText(/· \d+ (min|s|h)/);
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
    if (variant === "approve") await axe(page);

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
    // Design pass 2: the five-column list row no longer carries the policy; the open row's request panel names it, with its version.
    await expect(row.locator(`#apr-open-${approval}`)).toHaveAttribute("aria-current", "true");
    await expect(panel.getByText(/approve-push-main/)).toBeVisible();
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
        ? ["Denied by deny-env-read v1", "Approval requested", "Approved", "Approval used"]
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

// -------------------------------------------------------------------------------------------------------- gate 6

const refusal = async (page: Page, approval: string, button: "Approve" | "Deny" = "Approve") => {
  await page.goto(`/w/${WS}/approvals?approval=${approval}`);
  const panel = page.getByTestId("approval-detail");
  await expect(panel.getByRole("heading", { level: 2 })).toBeVisible();
  const answered = page.waitForResponse(
    (r) =>
      r.url().includes(`/v1/approvals/${approval}/decision`) && r.request().method() === "POST",
  );
  await panel.getByRole("button", { name: button, exact: true }).click();
  await panel.getByRole("button", { name: `Confirm ${button.toLowerCase()}` }).click();
  const response = await answered;
  const body = (await response.json()) as { error?: { code?: string } };
  return { panel, status: response.status(), code: body.error?.code };
};

for (const role of ["viewer", "billing"] as const)
  test(`@gate6 ${role}: 'Not available for your role' on Approvals and Policies, and 403 from the APIs`, async () => {
    const page = await signedIn(role, `/w/${WS}/approvals`);
    await expect(page.getByText("Not available for your role")).toBeVisible();
    // Step 5c: Approvals is a tab of Review; a role that may not read approvals has no such tab.
    await expect(page.getByRole("tab", { name: "Approvals" })).toHaveCount(0);
    await page.goto(`/w/${WS}/settings/policies`);
    await expect(page.getByText("Not available for your role")).toBeVisible();
    const headers = await workspaceHeader(page);
    for (const path of [
      "/api/abb/v1/approvals",
      "/api/abb/v1/approvals/pending-count",
      "/api/abb/v1/policies/current",
    ]) {
      const r = await page.request.get(path, { headers });
      expect(r.status(), `${role} ${path}`).toBe(403);
    }
    await axe(page);
  });

test("@gate6 developer: cannot decide a request whose rule needs an admin (APPROVER_ROLE_REQUIRED)", async () => {
  const g = shared().gate6 as Gate6;
  const page = await signedIn("developer", `/w/${WS}/approvals?approval=${g.admin_only}`);
  await expect(page.getByTestId("approval-detail")).toContainText("Admin or above");
  const { panel, status, code } = await refusal(page, g.admin_only);
  expect([status, code]).toEqual([403, "APPROVER_ROLE_REQUIRED"]);
  await expect(panel.getByTestId("decision-error")).toHaveText(
    "Your role cannot decide this request.",
  );
  await expect(panel).toContainText("Waiting");
  await page.screenshot({ path: `${SHOTS}/real-approver-role-required.png` });
  await axe(page);
});

test("@gate6 owner: cannot approve a request made with a key they created (SELF_APPROVAL_FORBIDDEN), can when the rule allows it", async () => {
  const g = shared().gate6 as Gate6;
  const page = await signedIn("owner", `/w/${WS}/approvals?approval=${g.admin_only}`);
  const { panel, status, code } = await refusal(page, g.admin_only);
  expect([status, code]).toEqual([403, "SELF_APPROVAL_FORBIDDEN"]);
  await expect(panel.getByTestId("decision-error")).toHaveText(
    "You cannot decide a request made with your own API key.",
  );
  await page.screenshot({ path: `${SHOTS}/real-self-approval-forbidden.png` });
  await axe(page);
  // the rule `gate-self` has allow_self_approval: the same person may approve this one
  await page.goto(`/w/${WS}/approvals?approval=${g.self_ok}`);
  const allowed = page.getByTestId("approval-detail");
  await expect(allowed).toContainText("notes.write");
  await allowed.getByLabel("Comment (optional)").fill("Mine to approve: the rule allows it.");
  await allowed.getByRole("button", { name: "Approve", exact: true }).click();
  await allowed.getByRole("button", { name: "Confirm approve" }).click();
  await expect(allowed.getByTestId("decision-saved")).toHaveText("You approved this request.");
});

test("@gate6 admin: the approver sees scrubbed arguments, and an owner-only rule refuses an admin", async () => {
  const g = shared().gate6 as Gate6;
  const secrets = [process.env.E2E_AWS ?? "", process.env.E2E_PASSWORD ?? ""];
  expect(secrets.every((s) => s.length > 8)).toBe(true);
  const page = await signedIn("admin", `/w/${WS}/approvals`);
  const bodies: string[] = [];
  page.on("response", async (r) => {
    if (!r.url().includes("/api/abb/")) return;
    try {
      bodies.push(await r.text());
    } catch {
      /* a stream or a navigation: nothing to read */
    }
  });
  await page.goto(`/w/${WS}/approvals?approval=${g.secret}`);
  const panel = page.getByTestId("approval-detail");
  await expect(panel.getByRole("heading", { level: 2, name: "db.connect" })).toBeVisible();
  await expect(panel.locator("pre.apr-args")).toBeVisible();
  const surface = await page.evaluate(() => {
    const parts = [document.body.innerText, document.body.textContent ?? ""];
    for (const el of document.querySelectorAll("*"))
      for (const a of el.attributes) parts.push(a.value);
    return parts.join("\n");
  });
  for (const secret of secrets) {
    expect(surface.includes(secret), "a planted secret is on the page").toBe(false);
    expect(bodies.join("\n").includes(secret), "a planted secret came over the proxy").toBe(false);
  }
  await expect(panel).toContainText(/REDACTED|redact/i);
  await page.screenshot({ path: `${SHOTS}/real-approval-scrubbed.png` });
  await axe(page);
  const { status, code, panel: owner } = await refusal(page, g.owner_only);
  expect([status, code]).toEqual([403, "APPROVER_ROLE_REQUIRED"]);
  await expect(owner.getByTestId("decision-error")).toHaveText(
    "Your role cannot decide this request.",
  );
});

// -------------------------------------------------------------------------------------------- real-stack UI (step 5)

const editor = (page: Page) =>
  page.getByRole("textbox", { name: /^Policy text( \(read only\))?$/ });
const POLICIES = () => `/w/${WS}/settings/policies?project=ui`;
const approvalRows = (page: Page) => page.getByTestId("approval-row");

test("@ui the real validator's line and column messages appear in the editor, and the caret jumps", async () => {
  const page = await signedIn("admin", POLICIES());
  const box = editor(page);
  await expect(box).toHaveValue(/id: gate-push/);
  const base = await box.inputValue();
  await box.fill(base.replace("version: 1", "version: 9"));
  const issues = page.getByTestId("issues");
  await expect(issues.getByRole("button")).toHaveCount(1);
  await expect(issues).toContainText("1:10");
  await expect(issues).toContainText("Only 'version: 1' is supported.");
  await expect(page.getByRole("button", { name: "Publish" })).toBeDisabled();
  await page.screenshot({ path: `${SHOTS}/real-policies-issue.png`, fullPage: false });
  await box.fill(
    base
      .replace("decision: deny", "decision: maybe")
      .replace("decision: require_approval", "decision: perhaps"),
  );
  await expect(issues.getByRole("button").first()).toContainText(/\d+:\d+/);
  await issues.getByRole("button").first().click();
  await expect(box).toBeFocused();
  // restore: nothing was published
  await box.fill(base);
  await expect(page.getByTestId("check-status")).toContainText("Unchanged");
});

test("@ui a real POLICY_VERSION_CONFLICT with two editors in two browser contexts", async () => {
  const a = await signedIn("admin", POLICIES());
  const b = await signedIn("owner", POLICIES());
  for (const page of [a, b]) await expect(editor(page)).toHaveValue(/id: gate-push/);
  const baseA = await editor(a).inputValue();
  await editor(a).fill(`${baseA}\n# edited by the admin\n`);
  await expect(a.getByRole("button", { name: "Publish" })).toBeEnabled();
  await a.getByLabel("Comment (optional)").fill("The admin got there first.");
  await a.getByRole("button", { name: "Publish" }).click();
  await expect(a.getByTestId("published")).toContainText(/Published version \d+\./);
  const published = Number(
    /Published version (\d+)\./.exec(await a.getByTestId("published").innerText())?.[1],
  );
  // B still holds the version it loaded: its publish is refused by the server, and nothing is stored
  await editor(b).fill(`${await editor(b).inputValue()}\n# edited by the owner\n`);
  await b.getByRole("button", { name: "Publish" }).click();
  const conflict = b.getByTestId("conflict");
  await expect(conflict).toContainText(`Someone published a newer version (version ${published}).`);
  await expect(b.getByRole("button", { name: "Publish" })).toBeDisabled();
  await b.screenshot({ path: `${SHOTS}/real-policies-conflict.png`, fullPage: false });
  await axe(b);
  await conflict.getByRole("button", { name: "Reload the latest version" }).click();
  await expect(b.getByTestId("conflict")).toHaveCount(0);
  await expect(editor(b)).toHaveValue(/edited by the admin/);
  await expect(editor(b)).not.toHaveValue(/edited by the owner/);
  await expect(b.getByTestId("version-row").first()).toContainText("The admin got there first.");
});

test("@ui real dry-run sample links open the recorded event (?event=)", async () => {
  const page = await signedIn("admin", POLICIES());
  await expect(editor(page)).toHaveValue(/id: gate-push/);
  await page.getByRole("button", { name: "Dry run" }).click();
  const dry = page.getByTestId("dry-run-result");
  await expect(dry).toContainText("Nothing was changed.");
  await expect(dry.getByRole("table", { name: "By rule" })).toBeVisible();
  const first = dry.getByTestId("dry-sample").first();
  await expect(first).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/real-policies-dry-run.png`, fullPage: true });
  const href = await first.getByRole("link").nth(1).getAttribute("href");
  expect(href).toMatch(/\/runs\/run_\w+\?event=evt_\w+$/);
  await first.getByRole("link").nth(1).click();
  await expect(page).toHaveURL(/\?event=evt_/);
  const event = (href ?? "").split("?event=")[1] ?? "";
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("dialog")).toContainText(event.slice(-8));
  await axe(page);
});

test("@ui a run waiting on a live approval shows the real time left; a viewer reads the same words without it", async () => {
  const ui = shared().ui as UiShared;
  const page = await signedIn("admin", runUrl(ui.run));
  await expect(page.getByTestId("headline")).toContainText(/^Waiting for approval of github\.push/);
  await expect(page.getByTestId("headline-detail")).toContainText(/\d+ (h|min|s)( \d+ min)? left/);
  await expect(page.getByTestId("approval-banner")).toContainText(/· \d+ (h|min|s)/);
  const viewer = await signedIn("viewer", runUrl(ui.run));
  await expect(viewer.getByTestId("headline")).toContainText(
    /^Waiting for approval of github\.push/,
  );
  await expect(viewer.getByTestId("headline-detail")).not.toContainText("left");
  await expect(viewer.getByTestId("approval-banner")).toContainText(
    "Someone with approval rights decides this request.",
  );
});

test("@ui a developer reads policies (read only) and sees the approvals page; billing sees neither", async () => {
  const dev = await signedIn("developer", POLICIES());
  await expect(editor(dev)).toHaveAttribute("readonly", "");
  await expect(dev.getByTestId("readonly-note")).toContainText(
    "You can read policies but not change them.",
  );
  await expect(dev.getByRole("button", { name: "Publish" })).toHaveCount(0);
  await dev.goto(`/w/${WS}/approvals`);
  await expect(approvalRows(dev).first()).toBeVisible();
  await expect(dev.getByRole("tab", { name: "Approvals" })).toBeVisible();
  const billing = await signedIn("billing", POLICIES());
  await expect(billing.getByText("Not available for your role")).toBeVisible();
});

for (const scheme of ["light", "dark"] as const)
  test(`@ui axe and screenshots, ${scheme}, on Approvals and Policies with real data`, async () => {
    const ui = shared().ui as UiShared;
    const page = await signedIn("admin", `/w/${WS}/approvals`, { colorScheme: scheme });
    await expect(approvalRows(page).first()).toBeVisible();
    await axe(page);
    await page.screenshot({ path: `${SHOTS}/real-approvals-list-${scheme}.png`, fullPage: false });
    await page.goto(`/w/${WS}/approvals?state=decided`);
    await expect(approvalRows(page).first()).toBeVisible();
    await axe(page);
    await page.goto(`/w/${WS}/approvals?approval=${ui.approval}`);
    await expect(page.getByTestId("approval-detail")).toContainText("github.push");
    await axe(page);
    await page.goto(POLICIES());
    await expect(editor(page)).toHaveValue(/id: gate-push/);
    await axe(page);
    await page.screenshot({ path: `${SHOTS}/real-policies-editor-${scheme}.png`, fullPage: false });
    await page.getByRole("button", { name: "Dry run" }).click();
    await expect(page.getByTestId("dry-run-result")).toContainText("Nothing was changed.");
    await page.getByRole("button", { name: /^View version 1/ }).click();
    await page.getByRole("button", { name: /^Compare with current/ }).click();
    await expect(page.getByTestId("policy-diff")).toBeVisible();
    await axe(page);
    await page.screenshot({
      path: `${SHOTS}/real-policies-history-${scheme}.png`,
      fullPage: false,
    });
    const phone = await signedIn("admin", `/w/${WS}/approvals`, {
      colorScheme: scheme,
      width: 375,
      height: 812,
    });
    await expect(approvalRows(phone).first()).toBeVisible();
    expect(await noSideScroll(phone)).toBe(true);
    await axe(phone);
  });

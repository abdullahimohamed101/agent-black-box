import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { writeFileSync } from "node:fs";
import { settleAnimations } from "./motion";

// Phase 17 step 4, API-backed. Runs only through scripts/alerts-e2e.sh: a real API and worker, the DEVELOPMENT-ONLY fake OIDC provider,
// the built web server on :3210 and local receivers (a signed webhook receiver that verifies the HMAC, a control port). The owner
// signs in through the real browser flow and drives the Alerts page: a destination, its signing secret shown once, a rule with a
// preview line, a test send; after the script made an alert fire, the History tab shows the delivery.
const WS = process.env.E2E_WORKSPACE;
const EMAIL = process.env.E2E_OWNER_EMAIL;
const RECV = process.env.E2E_RECV;
const TOKEN = process.env.E2E_UI_TOKEN;
const SECRET_FILE = process.env.E2E_UI_SECRET_FILE;
const MAIN_PORT = process.env.E2E_MAIN_PORT ?? "8213";
test.skip(!WS || !EMAIL || !RECV || !TOKEN, "needs scripts/alerts-e2e.sh");
test.use({ baseURL: "http://localhost:3210", viewport: { width: 1440, height: 900 } });
test.describe.configure({ mode: "serial" });
const SHOTS = "../../docs/screenshots/phase-17";
const DESTINATION = "UI webhook";
const RULE = "UI failed runs";

async function axe(page: Page) {
  await settleAnimations(page);
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual(
    [],
  );
}

/** From the web app's "Sign in" link, through the fake provider's page, back to the Alerts page. */
async function loginAs(page: Page, email: string, returnTo: string) {
  await page.goto(`/login?return_to=${encodeURIComponent(returnTo)}`);
  await page.getByRole("link", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Fake identity provider" })).toBeVisible();
  await page.getByLabel("Email").fill(email);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(new RegExp(returnTo.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
}

/** Console errors and failed requests, except the one refusal the test provokes on purpose (a 422 from saving a loopback target). */
const watch = (page: Page) => {
  const problems: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error" && !/Failed to load resource/.test(m.text())) problems.push(m.text());
  });
  page.on("pageerror", (e) => problems.push(e.message));
  page.on("response", (r) => {
    const url = new URL(r.url());
    const provoked =
      r.status() === 422 &&
      r.request().method() === "POST" &&
      url.pathname.endsWith("/alerts/destinations");
    if (r.status() >= 400 && !provoked && !/favicon/.test(url.pathname))
      problems.push(`${r.status()} ${r.request().method()} ${url.pathname}`);
  });
  return problems;
};

const receiverState = async (): Promise<{ requests: Record<string, unknown>[] }> =>
  (await fetch(`${RECV}/state`)).json() as Promise<{ requests: Record<string, unknown>[] }>;

test("@ui-setup destination with the signing secret shown once, rule with a preview, send test", async ({
  page,
}) => {
  const problems = watch(page);
  await loginAs(page, EMAIL as string, `/w/${WS}/alerts?tab=destinations`);
  await expect(page.getByRole("heading", { name: "Alerts", level: 1 })).toBeVisible();

  // a target the server refuses is refused on the form, and nothing is stored
  const open = page.getByRole("button", { name: "Add a destination" });
  await expect(open.or(page.getByLabel("Name"))).toBeVisible(); // the list (then the form) or, on an empty workspace, the form
  if (await open.isVisible()) await open.click();
  await page.getByLabel("Name").fill("UI refused");
  await page.getByLabel(/^Webhook URL/).fill("https://127.0.0.1/hook/x");
  await page.getByRole("button", { name: "Add destination" }).click();
  await expect(page.getByRole("form", { name: "New destination" })).toBeVisible();
  await expect(page.getByTestId("secret-panel")).toHaveCount(0);
  await expect(page.getByRole("row", { name: /UI refused/ })).toHaveCount(0);

  // a real one: HTTPS to the local receiver (the test network file makes the name resolve and trusts its certificate)
  await page.getByLabel("Name").fill(DESTINATION);
  await page
    .getByLabel(/^Webhook URL/)
    .fill(`https://hooks.example.test:${MAIN_PORT}/hook/${TOKEN}`);
  await page.getByRole("button", { name: "Add destination" }).click();
  const panel = page.getByTestId("secret-panel");
  await expect(panel).toBeVisible();
  const secret = await panel.getByTestId("signing-secret").innerText();
  expect(secret).toMatch(/^whsec_/);
  // the receiver is told the secret out of band (as an operator would); the file lets the script scan for it later
  writeFileSync(SECRET_FILE as string, secret, { mode: 0o600 });
  const told = await fetch(`${RECV}/secret`, {
    method: "POST",
    body: JSON.stringify({ key: TOKEN, secret }),
  });
  expect(told.ok).toBe(true);
  await axe(page);
  expect(
    await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage })),
  ).not.toContain(secret);
  expect(page.url()).not.toContain(secret);
  await panel.getByRole("checkbox").check();
  await panel.getByRole("button", { name: "Done" }).click();
  await expect(panel).toHaveCount(0);
  expect(await page.content()).not.toContain(secret);
  await page.reload();
  await expect(page.getByRole("row", { name: new RegExp(DESTINATION) })).toBeVisible();
  const html = await page.content();
  expect(html).not.toContain(secret);
  expect(html).not.toContain(TOKEN as string); // the target is masked in the page
  await page.screenshot({ path: `${SHOTS}/real-destinations.png`, fullPage: true });

  // send test: the receiver gets a signed `test` alert and says the HMAC verifies
  await page.getByRole("button", { name: `Send test to ${DESTINATION}` }).click();
  await expect(page.getByText("Test sent. The destination accepted it.")).toBeVisible();
  const seen = (await receiverState()).requests.filter((r) => r.key === TOKEN);
  expect(seen).toHaveLength(1);
  expect(seen[0]?.signature_ok).toBe(true);
  expect(seen[0]?.timestamp_ok).toBe(true);
  expect(JSON.parse(String(seen[0]?.body)).kind).toBe("test");

  // a rule for failed runs of the ui project, to this destination
  await page.goto(`/w/${WS}/alerts`);
  await page.getByRole("button", { name: "Add a rule" }).click();
  const form = page.getByRole("form", { name: "New rule" });
  await form.getByLabel("Tell me when").selectOption("run_failed");
  await form.getByLabel("Project").selectOption({ label: "ui" });
  await form.getByLabel("Name").fill(RULE);
  await form.getByRole("checkbox", { name: new RegExp(DESTINATION) }).check();
  await axe(page);
  await form.getByRole("button", { name: "Create rule" }).click();
  await expect(page.getByTestId("rule-saved")).toContainText(RULE);
  await expect(page.getByTestId("preview")).toContainText("in the last 7 days");
  await expect(page.getByRole("row", { name: new RegExp(RULE) })).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/real-rules.png`, fullPage: true });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBe(true);
  expect(problems).toEqual([]);
});

test("@ui-history the delivery of the alert that fired is listed with a link to the run", async ({
  page,
}) => {
  const problems = watch(page);
  await loginAs(page, EMAIL as string, `/w/${WS}/alerts?tab=history`);
  const row = page
    .getByTestId("history-row")
    .filter({ hasText: RULE })
    .filter({ hasText: DESTINATION })
    .first();
  await expect(row).toBeVisible();
  await expect(row).toHaveAttribute("data-status", "delivered");
  await expect(row.getByRole("link", { name: "Run" })).toHaveAttribute("href", /\/runs\/run_/);
  await axe(page);
  await page.screenshot({ path: `${SHOTS}/real-history.png`, fullPage: true });
  expect(await page.content()).not.toContain(TOKEN as string);
  expect(problems).toEqual([]);
});

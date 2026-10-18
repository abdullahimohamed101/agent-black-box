import AxeBuilder from "@axe-core/playwright";
import {
  expect,
  test,
  type Browser,
  type BrowserContext,
  type Page,
  type Response,
} from "@playwright/test";
import { settleAnimations } from "./motion";
import { workspaceHeader } from "./session";

// Phase 13 step 4, API-backed. Runs only through scripts/security-e2e.sh: a real API and worker, the DEVELOPMENT-ONLY fake OIDC
// provider, the built web server on :3190 and a run recorded by the real Python SDK that read a `.env`, printed an AWS-style key
// and a private key, force-pushed main, dropped a table, wiped a disk and called a host that is not on the project's allowlist.
// People sign in through the real browser flow: an owner, a viewer and a billing person.
const WS = process.env.E2E_WORKSPACE;
const RUN = process.env.E2E_PLANTED_RUN;
const BENIGN = process.env.E2E_BENIGN_RUN;
const OWNER = process.env.E2E_OWNER_EMAIL;
const VIEWER = process.env.E2E_VIEWER_EMAIL;
const BILLING = process.env.E2E_BILLING_EMAIL;
const planted = JSON.parse(process.env.E2E_PLANTED ?? "{}") as Record<string, string>;
test.skip(!WS || !RUN || !BENIGN || !OWNER || !VIEWER || !BILLING, "needs scripts/security-e2e.sh");
test.use({ baseURL: "http://localhost:3190", viewport: { width: 1440, height: 900 } });
test.describe.configure({ mode: "serial" }); // one owner session builds on the previous step (settings are restored at the end)
const ORIGIN = "http://localhost:3190";
const SHOTS = "../../docs/screenshots/phase-13";
const RUN_URL = () => `/w/${WS}/projects/all/runs/${RUN}`;
const SECRET_STRINGS = () => Object.values(planted); // plant tag, AWS key, PEM body, host, deploy directory

async function axe(page: Page) {
  await settleAnimations(page);
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual(
    [],
  );
}

/** Text, every attribute value and the HTML of the page: where a leak could hide. */
const surface = (page: Page) =>
  page.evaluate(() => {
    const parts = [document.body.innerText, document.body.textContent ?? ""];
    for (const el of document.querySelectorAll("*"))
      for (const a of el.attributes) parts.push(a.value);
    return parts.join("\n");
  });

async function leaksOnPage(page: Page, scope?: string): Promise<string[]> {
  const text = scope
    ? (await page.locator(scope).innerText()) + (await page.locator(scope).innerHTML())
    : (await surface(page)) + (await page.content());
  return SECRET_STRINGS().filter((s) => text.includes(s));
}

/** Records every API response body the page receives, to prove none of them carried the planted values. */
function recordResponses(page: Page): { bodies: string[]; urls: string[] } {
  const seen = { bodies: [] as string[], urls: [] as string[] };
  page.on("response", async (r: Response) => {
    if (!r.url().includes("/api/abb/")) return;
    seen.urls.push(`${r.request().method()} ${new URL(r.url()).pathname}`);
    try {
      seen.bodies.push(await r.text());
    } catch {
      /* a stream or a navigation: nothing to read */
    }
  });
  return seen;
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

async function personAt(
  email: string,
  returnTo: string,
  colorScheme: "light" | "dark" = "light",
): Promise<Page> {
  const context = await browserRef.newContext({
    baseURL: ORIGIN,
    viewport: { width: 1440, height: 900 },
    colorScheme,
  });
  contexts.push(context);
  const page = await context.newPage();
  await loginAs(page, email, returnTo);
  return page;
}

let owner: Page;
// Two page loads a second apart print different relative times ("51s ago", "52s ago"): compare the rest.
const withoutAge = (rows: string[]): string[] => rows.map((r) => r.replace(/\d+\s*\w+ ago/g, "<age> ago"));

let ownerFindings: string[] = [];

test("owner: the run page shows the chip and the Security group, and the evidence opens with its content", async () => {
  owner = await personAt(OWNER!, RUN_URL());
  const errors: string[] = [];
  owner.on("pageerror", (e) => errors.push(String(e)));
  await expect(owner.getByRole("listbox", { name: /Run timeline/ })).toBeVisible();
  const chip = owner.getByTestId("security-chip");
  await expect(chip).toHaveText("6 security findings (most severe: Critical)");
  await chip.click();
  const group = owner.getByTestId("security-group");
  await expect(group).toBeVisible();
  const lines = group.getByTestId("security-finding");
  await expect(lines).toHaveCount(6);
  ownerFindings = await lines.allInnerTexts();
  expect(await leaksOnPage(owner, "[data-testid=security-group]")).toEqual([]);
  await axe(owner);
  await owner.screenshot({ path: `${SHOTS}/real-run-owner-light.png` });
  // the evidence of the secret-file finding is the file.read event: an owner reads its path
  await lines
    .filter({ hasText: "usually holds secrets" })
    .getByRole("button", { name: "Show evidence" })
    .click();
  const dialog = owner.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText(planted.dir!);
  await expect(dialog).not.toContainText(/content withheld/i);
  await owner.screenshot({ path: `${SHOTS}/real-evidence-owner-light.png` });
  // the ordinary run has no chip at all
  await owner.keyboard.press("Escape");
  await owner.goto(`/w/${WS}/projects/all/runs/${BENIGN}`);
  await expect(owner.getByRole("listbox", { name: /Run timeline/ })).toBeVisible();
  await expect(owner.getByTestId("security-chip")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("owner: the workspace Security page lists the findings with facets, paging and a deep link", async () => {
  const page = owner;
  await page.goto(`/w/${WS}/security`);
  await expect(page.getByRole("heading", { name: "Security", level: 1 })).toBeVisible();
  const rows = page.getByTestId("security-row");
  await expect(rows).toHaveCount(10); // one page
  const more = page.getByRole("button", { name: "Load more" });
  await expect(more).toBeVisible();
  await more.click();
  await expect.poll(() => rows.count()).toBeGreaterThan(10);
  expect(await leaksOnPage(page, "main")).toEqual([]);
  await axe(page);
  await page.screenshot({ path: `${SHOTS}/real-security-page-light.png` });
  // facets edit the URL and narrow the rows
  await page.getByRole("button", { name: /^Critical/ }).click();
  await expect(page).toHaveURL(/severity=critical/);
  await expect
    .poll(async () => await page.locator('tr[data-severity="critical"]').count())
    .toBeGreaterThan(0);
  await expect(
    page.locator('tr[data-testid="security-row"]:not([data-severity="critical"])'),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Clear filters" }).first().click();
  // a row opens the planted run with that finding marked and its evidence open
  await page.goto(`/w/${WS}/security?detector=sec.risky_git`);
  const link = page
    .getByTestId("security-row")
    .filter({ hasText: "Security E2E: planted run" })
    .first()
    .getByRole("link");
  await link.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`runs/${RUN}\\?finding=fnd_\\w+`));
  await expect(page.locator('[data-finding-id][data-focus="true"]')).toContainText(
    "a risky git operation was run",
  );
  await expect(page.getByRole("dialog")).toBeVisible();
});

test("owner: the settings page saves a real PUT through the same-origin proxy, a 422 is refused, nothing is echoed", async () => {
  const page = owner;
  await page.goto(`/w/${WS}/settings/security`);
  await expect(page.getByRole("heading", { name: "Security", level: 1 })).toBeVisible();
  await page.getByLabel("Project", { exact: true }).selectOption({ label: "Full" });
  const save = page.getByRole("button", { name: "Save changes" });
  await expect(save).toBeDisabled();
  await expect(page.getByRole("list", { name: "Allowed hosts" })).toContainText("api.example.com");
  await expect(page.getByRole("radio", { name: /^Full/ })).toBeChecked();
  await axe(page);
  await page.screenshot({ path: `${SHOTS}/real-settings-light.png` });
  const put = () =>
    page.waitForResponse(
      (r) => r.request().method() === "PUT" && /security-settings$/.test(new URL(r.url()).pathname),
    );
  // add a host that matches nothing in the planted run: a real PUT, a real 200
  await page.getByLabel("Add a host").fill("*.example.org");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  const added = put();
  await save.click();
  const response = await added;
  expect(response.status()).toBe(200);
  expect(new URL(response.url()).pathname).toMatch(
    /^\/api\/abb\/v1\/projects\/prj_\w+\/security-settings$/,
  );
  const body = (await response.json()) as { destination_allowlist: string[] };
  expect(body.destination_allowlist).toEqual(["*.example.org", "api.example.com"]);
  await expect(page.getByTestId("save-status")).toContainText(
    "Existing runs are re-analysed in the background; recent runs first.",
  );
  // a real 422 from the API (the page refuses these first, so the proxy is called directly), and the value is not echoed
  const projects = (await (
    await page.request.get("/api/abb/v1/projects", { headers: await workspaceHeader(page) })
  ).json()) as {
    items: { id: string; slug: string }[];
  };
  const full = projects.items.find((p) => p.slug === "full")!;
  const headers = {
    ...(await workspaceHeader(page)),
    origin: ORIGIN,
    "content-type": "application/json",
  };
  const bad = await page.request.put(`/api/abb/v1/projects/${full.id}/security-settings`, {
    headers,
    data: { destination_allowlist: ["http://evil.example.com/path"] },
  });
  expect(bad.status()).toBe(422);
  const badText = await bad.text();
  expect(JSON.parse(badText).error.code).toBe("SETTINGS_INVALID");
  expect(badText).not.toContain("evil.example.com");
  const unknown = await page.request.put(`/api/abb/v1/projects/${full.id}/security-settings`, {
    headers,
    data: { muted_detectors: ["sec.not_a_detector"] },
  });
  expect(unknown.status()).toBe(422);
  // put it back: remove the host, a second real PUT
  await page.getByRole("button", { name: "Remove *.example.org" }).click();
  const removed = put();
  await save.click();
  expect((await removed).status()).toBe(200);
  await page.reload();
  await page.getByLabel("Project", { exact: true }).selectOption({ label: "Full" });
  await expect(page.getByRole("list", { name: "Allowed hosts" })).not.toContainText("example.org");
  // the findings of the planted run did not change (the host was never theirs)
  const items = (await (
    await page.request.get(`/api/abb/v1/runs/${RUN}/findings`, {
      headers: await workspaceHeader(page),
    })
  ).json()) as {
    items: { category: string }[];
  };
  expect(items.items.filter((i) => i.category === "security")).toHaveLength(6);
});

test("viewer: the same findings, the evidence says content withheld, and no planted value reaches the page or the network", async () => {
  const page = await personAt(VIEWER!, RUN_URL());
  const seen = recordResponses(page);
  await page.goto(RUN_URL());
  await expect(page.getByRole("listbox", { name: /Run timeline/ })).toBeVisible();
  await expect(page.getByTestId("security-chip")).toHaveText(
    "6 security findings (most severe: Critical)",
  );
  await page.getByTestId("security-chip").click();
  const lines = page.getByTestId("security-group").getByTestId("security-finding");
  await expect(lines).toHaveCount(6);
  expect(withoutAge(await lines.allInnerTexts())).toEqual(withoutAge(ownerFindings)); // identical to the owner's
  await lines
    .filter({ hasText: "usually holds secrets" })
    .getByRole("button", { name: "Show evidence" })
    .click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText(/content withheld/i);
  await expect(dialog).not.toContainText(planted.dir!);
  await page.screenshot({ path: `${SHOTS}/real-evidence-viewer-light.png` });
  await page.keyboard.press("Escape");
  expect(await leaksOnPage(page)).toEqual([]);
  // the workspace page and the run page again after the network has settled
  await page.goto(`/w/${WS}/security`);
  await expect(page.getByTestId("security-row").first()).toBeVisible();
  expect(await leaksOnPage(page, "main")).toEqual([]);
  const viewerRows = await page.getByTestId("security-row").allInnerTexts();
  await owner.goto(`/w/${WS}/security`);
  await expect(owner.getByTestId("security-row").first()).toBeVisible();
  expect(withoutAge(await owner.getByTestId("security-row").allInnerTexts())).toEqual(
    withoutAge(viewerRows),
  );
  await page.goto(`/w/${WS}/projects/all/runs?q=${encodeURIComponent(planted.plant!)}`);
  await page.waitForLoadState("networkidle");
  // (the term is in the search box, because the viewer typed it; what matters is that no run matched it)
  await expect(page.getByText(`Security E2E: planted run`)).toHaveCount(0); // search is no oracle for content
  // settings are not for a viewer, in the page and at the proxy
  await page.goto(`/w/${WS}/settings/security`);
  await expect(page.getByText("Not available for your role")).toBeVisible();
  const projects = (await (
    await page.request.get("/api/abb/v1/projects", { headers: await workspaceHeader(page) })
  ).json()) as {
    items: { id: string; slug: string }[];
  };
  const full = projects.items.find((p) => p.slug === "full")!;
  const denied = await page.request.put(`/api/abb/v1/projects/${full.id}/security-settings`, {
    headers: {
      ...(await workspaceHeader(page)),
      origin: ORIGIN,
      "content-type": "application/json",
    },
    data: { muted_detectors: [] },
  });
  expect([403, 404]).toContain(denied.status());
  // network: every API response the viewer's browser received was free of the planted values
  expect(seen.urls.some((u) => u.includes("/security/findings"))).toBe(true);
  expect(seen.urls.some((u) => u.endsWith("/findings"))).toBe(true);
  const joined = seen.bodies.join("\n");
  expect(SECRET_STRINGS().filter((s) => joined.includes(s))).toEqual([]);
  expect(seen.bodies.length).toBeGreaterThan(10);
});

test("billing: the Security page is not allowed, with the not-allowed state and no findings", async () => {
  const page = await personAt(BILLING!, `/w/${WS}/security`);
  const seen = recordResponses(page);
  await page.goto(`/w/${WS}/security`);
  await expect(page.getByText("Not available for your role")).toBeVisible();
  await expect(page.getByTestId("security-row")).toHaveCount(0);
  await page.screenshot({ path: `${SHOTS}/real-security-billing-light.png` });
  expect(SECRET_STRINGS().filter((s) => seen.bodies.join("").includes(s))).toEqual([]);
});

for (const scheme of ["light", "dark"] as const)
  test(`axe in the ${scheme} scheme on the run page, the Security page and the settings, with real data`, async () => {
    const page = await personAt(OWNER!, `/w/${WS}/security`, scheme);
    await expect(page.getByTestId("security-row").first()).toBeVisible();
    await axe(page);
    await page.screenshot({ path: `${SHOTS}/real-security-page-${scheme}.png` });
    await page.goto(RUN_URL());
    await page.getByTestId("security-chip").click();
    await expect(page.getByTestId("security-group")).toBeVisible();
    await axe(page);
    await page.screenshot({ path: `${SHOTS}/real-run-owner-${scheme}.png` });
    await page.goto(`/w/${WS}/settings/security`);
    await page.getByLabel("Project", { exact: true }).selectOption({ label: "Full" });
    await expect(page.getByRole("button", { name: "Save changes" })).toBeVisible();
    await axe(page);
    await page.screenshot({ path: `${SHOTS}/real-settings-${scheme}.png` });
  });

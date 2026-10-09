import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { signIn } from "./session";

// Runs only through scripts/auth-e2e.sh: a real API (stream re-check every 2 s), the DEVELOPMENT-ONLY fake OIDC provider
// and the built web server on :3160. People sign in through the real browser flow; the only minted session is the
// streamer's (create-session), because the test needs a second signed-in person already waiting on a live page.
const A = process.env.E2E_WORKSPACE;
const B = process.env.E2E_WORKSPACE_B;
const payloadRun = process.env.E2E_RUN_PAYLOAD;
const liveRun = process.env.E2E_RUN_LIVE;
const payloadText = process.env.E2E_PAYLOAD_TEXT;
const streamerSession = process.env.E2E_STREAMER_SESSION;
test.skip(!A || !B || !payloadRun || !liveRun || !payloadText, "needs scripts/auth-e2e.sh");
test.use({ baseURL: "http://localhost:3160" });
test.describe.configure({ mode: "serial" }); // the steps build on each other: one person, one invitation, one session
const SHOTS = "../../docs/screenshots/phase-15";
const ORIGIN = "http://localhost:3160";
const OWNER = process.env.E2E_OWNER_EMAIL!;
const INVITEE = process.env.E2E_INVITEE_EMAIL!;
const STREAMER = process.env.E2E_STREAMER_EMAIL!;

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual(
    [],
  );
}

/** From the web app's "Sign in" link, through the fake provider's page, back to wherever the app sends us. */
async function loginAs(page: Page, email: string) {
  await page.getByRole("link", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Fake identity provider" })).toBeVisible();
  await page.getByLabel("Email").fill(email);
  await page.getByRole("button", { name: "Sign in" }).click();
}

const cookiesOf = (context: BrowserContext) => context.cookies(ORIGIN);

let browserRef: Browser;
let owner: { context: BrowserContext; page: Page };
let inviteLink = "";

test.beforeAll(async ({ browser }) => {
  browserRef = browser;
});
test.afterAll(async () => {
  await owner?.context.close();
});

test("a signed-out visitor is sent to sign in, signs in through the provider and lands where they were going", async () => {
  const context = await browserRef.newContext({ baseURL: ORIGIN });
  const page = await context.newPage();
  owner = { context, page };
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto(`/w/${A}/projects/all`);
  await expect(page).toHaveURL(/\/login\?return_to=/);
  await expect(page.getByRole("heading", { name: /Sign in/ })).toBeVisible();
  await axe(page);
  await page.screenshot({ path: `${SHOTS}/login.png` });

  await loginAs(page, OWNER);
  await expect(page).toHaveURL(new RegExp(`/w/${A}/projects/all$`));
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  await expect(page.getByText(OWNER)).toBeVisible();

  // The cookie is the whole credential and the page's scripts cannot read it.
  expect(await page.evaluate(() => document.cookie)).toBe("");
  const jar = await cookiesOf(context);
  const session = jar.find((c) => c.name === "abb_session");
  expect(session?.httpOnly).toBe(true);
  expect(session?.sameSite).toBe("Lax");
  expect(errors).toEqual([]);
  await axe(page);
});

test("a person in two workspaces switches between them and sees only the one they are in", async () => {
  const { page } = owner;
  await page.goto(`/w/${A}/projects/all/runs`);
  await expect(page.getByText("Auth: payload run")).toBeVisible();
  await page.getByText("Auth Alpha").first().click(); // the switcher is a <details> disclosure
  await page.screenshot({ path: `${SHOTS}/workspace-switcher.png` });
  await page.getByRole("link", { name: "Auth Beta" }).click();
  await expect(page).toHaveURL(new RegExp(`/w/${B}/projects/all`));
  await page.goto(`/w/${B}/projects/all/runs`);
  await expect(page.getByText("Auth: payload run")).toHaveCount(0);
  // a run of the other workspace is indistinguishable from one that never existed
  await page.goto(`/w/${B}/projects/all/runs/${payloadRun}`);
  await expect(page.getByTestId("headline")).toHaveCount(0);
  await expect(page.getByText("Auth: payload run")).toHaveCount(0);
  // a workspace the person does not belong to is a 404 page, not a leak
  const missing = await page.goto(`/w/not-a-workspace-${Date.now()}/projects/all`);
  expect(missing?.status()).toBe(404);
});

test("an owner sees the captured payload", async () => {
  const { page } = owner;
  await page.goto(`/w/${A}/projects/all/runs/${payloadRun}`);
  await page.getByRole("option").filter({ hasText: "run.started" }).first().click();
  const drawer = page.getByRole("dialog");
  await expect(drawer.getByLabel("Event payload")).toContainText(payloadText!);
  await expect(page.getByTestId("payload-withheld")).toHaveCount(0);
});

test("settings: an invitation link and an API key token are each shown once", async () => {
  const { page } = owner;
  await page.goto(`/w/${A}/settings/members`);
  await expect(page.getByRole("heading", { name: "Members" })).toBeVisible();
  await expect(page.getByRole("cell", { name: new RegExp(OWNER) })).toBeVisible();
  await page.locator("form.form").getByRole("textbox").fill(INVITEE);
  await page.locator("form.form").getByRole("combobox").selectOption("DEVELOPER");
  await page.getByRole("button", { name: "Create invitation" }).click();
  const link = page.getByTestId("invite-link");
  await expect(link).toContainText("/invite#");
  inviteLink = (await link.textContent())!.trim();
  await page.screenshot({ path: `${SHOTS}/settings-members-invitation.png`, fullPage: true });
  await axe(page);
  await page.reload();
  await expect(page.getByTestId("invite-link")).toHaveCount(0); // gone for good
  await expect(page.getByRole("cell", { name: INVITEE })).toBeVisible();

  await page.goto(`/w/${A}/settings/api-keys`);
  await page.getByLabel("Name").fill("auth e2e key");
  await page.getByLabel("events:write").uncheck(); // ingestion keys need a project; this one only reads
  await page.getByLabel("runs:read").check();
  await page.getByRole("button", { name: "Create key" }).click();
  const token = page.getByTestId("key-token");
  await expect(token).toContainText("abb_live_");
  await page.screenshot({ path: `${SHOTS}/settings-key-shown-once.png`, fullPage: true });
  await page.getByRole("button", { name: "I have stored it" }).click();
  await expect(page.getByTestId("key-token")).toHaveCount(0);
  await page.reload();
  await expect(page.getByTestId("key-token")).toHaveCount(0);
  await expect(page.getByText("auth e2e key")).toBeVisible();
  await expect(page.locator("body")).not.toContainText("abb_live_");
  await axe(page);

  for (const slug of ["pricing", "audit"]) {
    await page.goto(`/w/${A}/settings/${slug}`);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await axe(page);
  }
  // the administrative actions above are in the audit log
  await page.goto(`/w/${A}/settings/audit`);
  await expect(page.getByText("invitation.create").first()).toBeVisible();
  await expect(page.getByText("api_key.create").first()).toBeVisible();
});

test("an invited person signs in, accepts the invitation and joins as a developer", async () => {
  const context = await browserRef.newContext({ baseURL: ORIGIN });
  const page = await context.newPage();
  try {
    await page.goto(inviteLink.replace(/^https?:\/\/[^/]+/, ""));
    await expect(
      page.getByText(/Sign in with the email address the invitation was sent to/),
    ).toBeVisible();
    expect(page.url()).not.toContain("#"); // the token left the address bar
    await axe(page);
    await loginAs(page, INVITEE);
    await expect(page.getByRole("button", { name: "Accept invitation" })).toBeVisible();
    await page.getByRole("button", { name: "Accept invitation" }).click();
    await expect(page).toHaveURL(new RegExp(`/w/${A}/projects/all`));
    // a developer reads payloads
    await page.goto(`/w/${A}/projects/all/runs/${payloadRun}`);
    await page.getByRole("option").filter({ hasText: "run.started" }).first().click();
    await expect(page.getByRole("dialog").getByLabel("Event payload")).toContainText(payloadText!);
    // the same link is dead now
    const again = await page.request.post("/api/abb/v1/invitations/accept", {
      data: { token: inviteLink.split("#")[1] },
      headers: { origin: ORIGIN, "content-type": "application/json" },
    });
    expect(again.status()).toBe(409); // already a member (and the link was single-use)
  } finally {
    await context.close();
  }
});

test("a role downgrade to viewer takes effect at once: metadata stays, content is withheld", async () => {
  const { page } = owner;
  // sign the invitee in again in their own context first so we can watch their view change
  const context = await browserRef.newContext({ baseURL: ORIGIN });
  const viewer = await context.newPage();
  try {
    await viewer.goto(`/w/${A}/projects/all`);
    await loginAs(viewer, INVITEE);
    await expect(viewer.getByRole("heading", { name: "Dashboard" })).toBeVisible();
    await expect(viewer.getByRole("link", { name: "API keys" })).toHaveCount(0);

    await page.goto(`/w/${A}/settings/members`);
    await page.getByLabel(`Role of ${INVITEE}`).selectOption("VIEWER");
    await expect(page.getByLabel(`Role of ${INVITEE}`)).toHaveValue("VIEWER");

    await viewer.goto(`/w/${A}/projects/all/runs/${payloadRun}`);
    await expect(viewer.getByTestId("headline")).toContainText("Succeeded"); // metadata still there
    await viewer.getByRole("option").filter({ hasText: "run.started" }).first().click();
    const drawer = viewer.getByRole("dialog");
    await expect(drawer.getByTestId("payload-withheld")).toBeVisible();
    await expect(viewer.locator("body")).not.toContainText(payloadText!);
    await viewer.screenshot({ path: `${SHOTS}/viewer-payload-withheld.png`, fullPage: true });
    await axe(viewer);

    // settings the role lacks are absent from the navigation and a clean note when typed in directly
    await viewer.goto(`/w/${A}/settings/members`);
    await expect(
      viewer.getByRole("navigation", { name: "Settings" }).getByRole("link", { name: "API keys" }),
    ).toHaveCount(0);
    await viewer.goto(`/w/${A}/settings/api-keys`);
    await expect(viewer.getByText("Not available for your role")).toBeVisible();
    await axe(viewer);
    // and a direct call is refused by the API itself, not just hidden by the page
    const me = (await (await viewer.request.get("/api/abb/v1/me")).json()) as {
      memberships: { workspace: { id: string; slug: string } }[];
    };
    const ws = me.memberships.find((m) => m.workspace.slug === A)!.workspace.id;
    const keys = await viewer.request.get("/api/abb/v1/api-keys", {
      headers: { "x-abb-workspace": ws },
    });
    expect(keys.status()).toBe(403);
  } finally {
    await context.close();
  }
});

test("removing a person ends the live stream they have open", async () => {
  const { page } = owner;
  const context = await browserRef.newContext({ baseURL: ORIGIN });
  await signIn(context, ORIGIN, streamerSession);
  const streamer = await context.newPage();
  try {
    await streamer.goto(`/w/${A}/projects/all/runs/${liveRun}`);
    await expect(streamer.getByTestId("live-status")).toContainText("Live");

    await page.goto(`/w/${A}/settings/members`);
    const row = page.getByRole("row").filter({ hasText: STREAMER });
    await row.getByRole("button", { name: "Remove" }).click();
    await row.getByRole("button", { name: "Confirm remove" }).click();
    await expect(page.getByRole("row").filter({ hasText: STREAMER })).toHaveCount(0);

    // the API re-checks every 2 s; the page stops asking instead of reconnecting forever
    await expect(streamer.getByTestId("live-status")).toHaveAttribute(
      "data-stream",
      "unauthorized",
      {
        timeout: 20_000,
      },
    );
    await expect(streamer.getByTestId("live-status")).toContainText(
      "your access to this run changed",
    );
    await streamer.screenshot({ path: `${SHOTS}/stream-unauthorized.png`, fullPage: true });
  } finally {
    await context.close();
  }
});

test("signing out ends the session: the cookie is gone and workspace pages redirect to sign-in", async () => {
  const { page, context } = owner;
  await page.goto(`/w/${A}/projects/all`);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/);
  expect((await cookiesOf(context)).find((c) => c.name === "abb_session")).toBeUndefined();
  await page.goto(`/w/${A}/projects/all`);
  await expect(page).toHaveURL(/\/login\?return_to=/);
  expect((await page.request.get("/api/abb/v1/me")).status()).toBe(401);
});

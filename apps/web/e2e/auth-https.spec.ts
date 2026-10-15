import { expect, test, type Browser, type BrowserContext, type Page } from "@playwright/test";

// Runs only through scripts/auth-https-e2e.sh: the built web server behind a self-signed TLS terminator on
// https://localhost:3161, a real API and the DEVELOPMENT-ONLY fake OIDC provider (ADR-156, KI-075).
const ORIGIN = "https://localhost:3161";
const OWNER = process.env.E2E_HTTPS_OWNER_EMAIL;
const ATTACKER = process.env.E2E_HTTPS_ATTACKER_EMAIL;
const WORKSPACE = process.env.E2E_HTTPS_WORKSPACE;
test.skip(!OWNER || !ATTACKER || !WORKSPACE, "needs scripts/auth-https-e2e.sh");
test.use({ baseURL: ORIGIN, ignoreHTTPSErrors: true });
test.describe.configure({ mode: "serial" });

const names = async (context: BrowserContext) => (await context.cookies(ORIGIN)).map((c) => c.name);

async function signInAt(page: Page, email: string) {
  await expect(page.getByRole("heading", { name: "Fake identity provider" })).toBeVisible();
  await page.getByLabel("Email").fill(email);
  await page.getByRole("button", { name: "Sign in" }).click();
}

async function fresh(browser: Browser) {
  const context = await browser.newContext({ baseURL: ORIGIN, ignoreHTTPSErrors: true });
  return { context, page: await context.newPage() };
}

test("the browser holds a __Host- login cookie during sign-in, then a __Host- session", async ({
  browser,
}) => {
  const { context, page } = await fresh(browser);
  await page.goto("/api/auth/login");
  await expect(page.getByRole("heading", { name: "Fake identity provider" })).toBeVisible();
  const login = (await context.cookies(ORIGIN)).find((c) => c.name === "__Host-abb_login");
  expect(login, "the prefixed login cookie is stored while the flow runs").toBeTruthy();
  expect(login).toMatchObject({ secure: true, httpOnly: true, path: "/", sameSite: "Lax" });
  expect(await names(context)).not.toContain("abb_login");
  await signInAt(page, OWNER!);
  await expect(page).toHaveURL(new RegExp(`${ORIGIN}/`));
  await expect(page.getByRole("heading", { name: "Sign in" })).toHaveCount(0);
  const after = await names(context);
  expect(after).toContain("__Host-abb_session");
  expect(after).not.toContain("__Host-abb_login"); // spent on the callback
  await context.close();
});

test("the browser refuses a __Host- cookie written with a Domain or a narrower Path", async ({
  browser,
}) => {
  const { context, page } = await fresh(browser);
  await page.goto("/login");
  const written = await page.evaluate(() => {
    document.cookie = "__Host-abb_login=planted-with-domain-0000; Domain=localhost; Path=/; Secure";
    document.cookie = "__Host-abb_login=planted-with-path-000000; Path=/api/auth; Secure";
    document.cookie = "__Host-abb_login=planted-insecure-000000; Path=/";
    return document.cookie;
  });
  expect(written).not.toContain("__Host-abb_login");
  expect(await names(context)).not.toContain("__Host-abb_login");
  await context.close();
});

test("a callback carrying only a planted abb_login cannot sign a victim in as the attacker", async ({
  browser,
}) => {
  // The attacker signs in at the provider but never opens the callback URL.
  const attacker = await fresh(browser);
  let callbackUrl = "";
  // The provider answers the sign-in form with a redirect to the callback: read it, do not let the browser follow it.
  await attacker.page.route("http://localhost:8168/**", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const res = await route.fetch({ maxRedirects: 0 });
    callbackUrl = res.headers()["location"] ?? "";
    await route.fulfill({ status: 200, contentType: "text/html", body: "<p>held</p>" });
  });
  await attacker.page.goto("/api/auth/login");
  const state = (await attacker.context.cookies(ORIGIN)).find(
    (c) => c.name === "__Host-abb_login",
  )?.value;
  expect(state).toBeTruthy();
  await signInAt(attacker.page, ATTACKER!);
  await expect.poll(() => callbackUrl).toContain("/api/auth/callback");

  // The victim's browser has the old-name cookie planted (a sibling origin could do exactly this) and follows the link.
  const victim = await fresh(browser);
  await victim.page.goto("/login");
  await victim.page.evaluate((s) => {
    document.cookie = `abb_login=${s}; Path=/api/auth; Secure`;
  }, state!);
  await victim.page.goto(callbackUrl);
  await expect(victim.page).toHaveURL(/\/login\?error=login_failed/);
  const stored = await names(victim.context);
  expect(stored.filter((n) => n.includes("session"))).toEqual([]);

  // The refused attempt did not burn the attacker's state: their own browser still completes.
  await attacker.page.goto(callbackUrl);
  expect(await names(attacker.context)).toContain("__Host-abb_session");
  await attacker.context.close();
  await victim.context.close();
});

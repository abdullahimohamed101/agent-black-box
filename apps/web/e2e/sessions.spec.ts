import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { settleAnimations } from "./motion";

// Phase 19a step 5 (fixtures mode): the Sessions settings page. The list comes from src/fixtures/sessions.ts (this session,
// a phone, an unknown device). Revoking changes server-side fixture state that nothing can put back, so the tests that end
// sessions run against a stateful stub of the same two routes instead, and the fixture server is only ever read.
const SHOTS = "../../docs/screenshots/phase-19a";
const PAGE = "/w/demo/settings/sessions";

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
    if (m.type() === "error") seen.push(m.text());
  });
  page.on("pageerror", (e) => seen.push(e.message));
  return seen;
}

type Row = {
  id: string;
  device: string | null;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  idle_expires_at: string;
  current: boolean;
};
const row = (n: number, device: string | null, current = false): Row => ({
  id: `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`,
  device,
  created_at: "2026-10-07T08:00:00Z",
  last_seen_at: "2026-10-07T09:25:00Z",
  expires_at: "2026-10-14T08:00:00Z",
  idle_expires_at: "2026-10-08T09:25:00Z",
  current,
});

/** A stateful stand-in for the three session routes, with a log of the calls the page made. */
async function stubSessions(page: Page, rows: Row[]) {
  const calls: string[] = [];
  await page.route("**/api/abb/v1/auth/sessions**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace("/api/abb", "");
    calls.push(`${req.method()} ${path}`);
    if (req.method() === "GET") return route.fulfill({ json: { items: rows } });
    if (path.endsWith("/revoke-others")) {
      const n = rows.filter((r) => !r.current).length;
      rows.splice(0, rows.length, ...rows.filter((r) => r.current));
      return route.fulfill({ json: { revoked: n } });
    }
    const id = path.split("/").pop();
    const at = rows.findIndex((r) => r.id === id && !r.current);
    if (at < 0)
      return route.fulfill({ status: 404, json: { error: { code: "SESSION_NOT_FOUND" } } });
    rows.splice(at, 1);
    return route.fulfill({ status: 204 });
  });
  return calls;
}

for (const [width, height] of [
  [1440, 900],
  [375, 812],
] as const)
  test.describe(`${width} px`, () => {
    test.use({ viewport: { width, height } });

    test("list: devices, times, the current session in words, and no console noise", async ({
      page,
    }) => {
      const errors = watchConsole(page);
      await page.goto(PAGE);
      await expect(page.getByRole("heading", { name: "Sessions", level: 1 })).toBeVisible();
      const items = page.getByTestId("session-item");
      await expect(items).toHaveCount(3);
      await expect(items.nth(0)).toContainText("Firefox on Linux");
      await expect(items.nth(0)).toContainText("This session");
      await expect(items.nth(0).getByRole("button")).toHaveCount(0);
      await expect(items.nth(1)).toContainText("Safari on iOS");
      await expect(items.nth(2)).toContainText("Unknown device");
      await expect(page.getByRole("link", { name: "Sessions" }).first()).toHaveAttribute(
        "aria-current",
        "page",
      );
      await axe(page);
      expect(errors).toEqual([]);
    });

    test("the page does not scroll sideways", async ({ page }) => {
      await page.goto(PAGE);
      await expect(page.getByTestId("session-item")).toHaveCount(3);
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow).toBeLessThanOrEqual(0);
    });
  });

test.describe("ending sessions", () => {
  test("end one, then sign out everywhere else after a confirmation, from the keyboard", async ({
    page,
  }) => {
    const errors = watchConsole(page);
    const calls = await stubSessions(page, [
      row(1, "Firefox on Linux", true),
      row(2, "Safari on iOS"),
      row(3, null),
      row(4, "Chrome on Windows"),
    ]);
    await page.goto(PAGE);
    await expect(page.getByTestId("session-item")).toHaveCount(4);
    // end the phone
    const end = page.getByRole("button", { name: /End session on Safari on iOS/ });
    await end.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("session-item")).toHaveCount(3);
    await expect(page.getByTestId("session-status")).toContainText("Session ended.");
    // sign out everywhere else asks first
    const everywhere = page.getByRole("button", { name: "Sign out everywhere else" });
    await everywhere.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByText("Sign out 2 other sessions?")).toBeVisible();
    await expect(page.getByText(/This device stays signed in/)).toBeVisible();
    await axe(page);
    await page.getByRole("button", { name: "Cancel" }).click();
    expect(calls.filter((c) => c.startsWith("POST"))).toEqual([]);
    await page.getByRole("button", { name: "Sign out everywhere else" }).click();
    await page.getByRole("button", { name: "Confirm sign out everywhere else" }).click();
    await expect(page.getByTestId("session-item")).toHaveCount(1);
    await expect(page.getByTestId("session-status")).toContainText("Signed out 2 other sessions.");
    await expect(page.getByTestId("session-item").first()).toContainText("This session");
    await expect(page.getByRole("button", { name: /everywhere else/ })).toHaveCount(0);
    expect(calls.filter((c) => c.startsWith("DELETE"))).toEqual([
      "DELETE /v1/auth/sessions/00000000-0000-4000-8000-000000000002",
    ]);
    await axe(page);
    expect(errors).toEqual([]);
  });

  test("a refused change is a banner, not a crash", async ({ page }) => {
    await stubSessions(page, [row(1, "Firefox on Linux", true), row(2, "Safari on iOS")]);
    await page.route("**/api/abb/v1/auth/sessions/0*", (route) =>
      route.request().method() === "DELETE"
        ? route.fulfill({
            status: 429,
            json: {
              error: {
                code: "RATE_LIMITED",
                message: "Too many session changes.",
                retryable: true,
                request_id: "r",
                details: {},
              },
            },
          })
        : route.fallback(),
    );
    await page.goto(PAGE);
    await page.getByRole("button", { name: /End session on Safari on iOS/ }).click();
    await expect(
      page.getByRole("alert").filter({ hasText: "Too many session changes" }),
    ).toBeVisible();
    await expect(page.getByTestId("session-item")).toHaveCount(2);
    await axe(page);
  });
});

test.describe("viewer", () => {
  test("a viewer has the page too: sessions belong to the person, not the role", async ({
    page,
  }) => {
    await page.goto("/w/viewer-only/settings/sessions");
    await expect(page.getByTestId("session-item")).toHaveCount(3);
    await expect(page.getByRole("link", { name: "Sessions" }).first()).toBeVisible();
    await axe(page);
  });
});

test("an error shows a retry", async ({ page }) => {
  let fail = true;
  await page.route("**/api/abb/v1/auth/sessions", (route) =>
    fail
      ? route.fulfill({
          status: 503,
          json: {
            error: {
              code: "API_UNREACHABLE",
              message: "The API could not be reached.",
              retryable: true,
              request_id: "r",
              details: {},
            },
          },
        })
      : route.continue(),
  );
  await page.goto(PAGE);
  await expect(page.getByRole("alert").filter({ hasText: "could not be reached" })).toBeVisible({
    timeout: 15_000,
  });
  fail = false;
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByTestId("session-item")).toHaveCount(3);
});

for (const scheme of ["light", "dark"] as const)
  test.describe(`${scheme} scheme`, () => {
    test.use({ colorScheme: scheme, viewport: { width: 1440, height: 900 } });
    test("axe on the page", async ({ page }) => {
      await page.goto(PAGE);
      await expect(page.getByTestId("session-item")).toHaveCount(3);
      await axe(page);
    });
  });

test.describe("reduced motion", () => {
  test.use({ reducedMotion: "reduce" });
  test("nothing animates on the page", async ({ page }) => {
    await page.goto(PAGE);
    await expect(page.getByTestId("session-item")).toHaveCount(3);
    await page.waitForTimeout(300);
    const running = await page.evaluate(
      () => document.getAnimations().filter((a) => a.playState === "running").length,
    );
    expect(running).toBe(0);
  });
});

test("the no-access notice on the sign-in page is plain words and axe-clean", async ({ page }) => {
  await page.goto("/login?error=no_access");
  await expect(
    page
      .getByRole("alert")
      .filter({ hasText: "No access yet. Ask a workspace owner to invite you." }),
  ).toBeVisible();
  await axe(page);
});

for (const scheme of ["light", "dark"] as const)
  for (const [width, height] of [
    [1440, 900],
    [375, 812],
  ] as const)
    test(`screenshot: ${scheme} ${width}`, async ({ browser }) => {
      const ctx = await browser.newContext({ colorScheme: scheme, viewport: { width, height } });
      const page = await ctx.newPage();
      await page.goto(PAGE);
      await expect(page.getByTestId("session-item")).toHaveCount(3);
      await settleAnimations(page);
      await page.screenshot({ path: `${SHOTS}/sessions-${width}-${scheme}.png`, fullPage: true });
      await ctx.close();
    });

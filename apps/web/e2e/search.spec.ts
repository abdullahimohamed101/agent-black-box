import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { signIn } from "./session";
import { shotsDir } from "./shots-dir";

// Runs only through scripts/auth-e2e.sh (a real API, the built web server on :3160, the worker). The two people are minted
// development sessions (create-session): a workspace OWNER who holds `payload.read` and a VIEWER who does not. Sign-in itself
// is covered by auth.spec.ts.
const A = process.env.E2E_WORKSPACE;
const ownerSession = process.env.E2E_SEARCH_OWNER_SESSION;
const viewerSession = process.env.E2E_SEARCH_VIEWER_SESSION;
const commandText = process.env.E2E_COMMAND_TEXT;
const pathText = process.env.E2E_PATH_TEXT;
test.skip(
  !A || !ownerSession || !viewerSession || !commandText || !pathText,
  "needs scripts/auth-e2e.sh",
);
test.use({ baseURL: "http://localhost:3160" });
const ORIGIN = "http://localhost:3160";
const SHOTS = shotsDir("phase-16");
const RUNS = () => `/w/${A}/projects/all/runs`;

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual(
    [],
  );
}

let browserRef: Browser;
const contexts: BrowserContext[] = [];
test.beforeAll(({ browser }) => {
  browserRef = browser;
});
test.afterAll(async () => {
  for (const c of contexts) await c.close();
});

async function person(token: string): Promise<Page> {
  const context = await browserRef.newContext({ baseURL: ORIGIN });
  contexts.push(context);
  await signIn(context, ORIGIN, token);
  return context.newPage();
}

async function search(page: Page, q: string) {
  const box = page.getByRole("searchbox", { name: "Search runs" });
  await box.fill(q);
  await box.press("Enter");
}

const noOverflow = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

test("an owner finds a run by the text of its command and by its file path", async () => {
  const page = await person(ownerSession!);
  await page.goto(RUNS());
  await expect(page.getByText("Auth: payload run")).toBeVisible();

  await search(page, `command:${commandText!.slice(0, 14)}`); // substring of the command line
  await expect(page.getByRole("link", { name: "Auth: payload run" })).toBeVisible();
  await expect(page.getByText("Auth: live run")).toHaveCount(0);
  await expect(page.getByRole("list", { name: "Search terms" })).toContainText("command:");
  await expect(page).toHaveURL(/q=command%3A/);

  await search(page, "file:*id_rsa");
  await expect(page.getByRole("link", { name: "Auth: payload run" })).toBeVisible();
  await expect(page.getByText("Auth: live run")).toHaveCount(0);

  await search(page, "id_rsa"); // free text reaches captured content for someone who may read it
  await expect(page.getByRole("link", { name: "Auth: payload run" })).toBeVisible();
  await axe(page);
  await page.screenshot({ path: `${SHOTS}/owner-content-search.png`, fullPage: true });

  // the owner's help lists the content fields
  await page.getByRole("button", { name: "Search help" }).click();
  const fields = page.getByRole("list", { name: "Searchable fields" });
  await expect(fields).toContainText("file:");
  await expect(fields).toContainText("command:");

  // the same search for a word that is nowhere says so plainly
  await search(page, "zzz-nothing-matches-this");
  await expect(page.getByText("No runs match these filters")).toBeVisible();
  await axe(page);
});

test("a viewer who types file: or command: is refused, and nothing else changes", async () => {
  const page = await person(viewerSession!);
  await page.goto(RUNS());
  await expect(page.getByText("Auth: payload run")).toBeVisible();
  await page.getByRole("button", { name: "Search help" }).click();
  const fields = page.getByRole("list", { name: "Searchable fields" });
  await expect(fields).toContainText("status:");
  await expect(fields).not.toContainText("file:"); // never offered
  await expect(fields).not.toContainText("command:");

  for (const [q, field] of [
    ["file:id_rsa", "file:"],
    ["command:curl", "command:"],
  ] as const) {
    const responses: number[] = [];
    page.on("response", (r) => {
      if (r.url().includes("/api/abb/v1/runs?") && r.url().includes("q="))
        responses.push(r.status());
    });
    await search(page, q);
    const notice = page.locator(".search-problem");
    await expect(notice).toContainText("Your role cannot search that field");
    await expect(notice).toContainText(field);
    await expect(notice).toContainText("content permission");
    // a refusal is not an empty result, and no run row, chip or facet is shown with it
    await expect(page.getByText("No runs match")).toHaveCount(0);
    await expect(page.getByText("No runs yet")).toHaveCount(0);
    await expect(page.getByRole("table")).toHaveCount(0);
    await expect(page.locator("body")).not.toContainText(pathText!);
    await expect(page.locator("body")).not.toContainText(commandText!);
    expect(responses).toContain(403);
    page.removeAllListeners("response");
  }
  await axe(page);
  await page.screenshot({ path: `${SHOTS}/viewer-refused.png`, fullPage: true });
});

test("a viewer's search over captured text finds nothing, exactly like a nonsense word", async () => {
  const page = await person(viewerSession!);
  await page.goto(RUNS());
  const outcome = async (q: string) => {
    await search(page, q);
    // wait for this query's own answer (its chip), not the previous page that stays up while loading
    await expect(page.getByRole("list", { name: "Search terms" })).toContainText(q);
    await expect(page.getByRole("link", { name: "Auth: payload run" })).toHaveCount(0);
    await expect(page.getByText("No runs match these filters")).toBeVisible();
    // The nav's "Recording" status (a screen-reader-only dot while any run is RUNNING) is live workspace state polled on its own
    // clock, unrelated to the query: leave it out so the two pages are compared on the search outcome alone.
    return (await page.locator("main, body").first().innerText()).replace(/^Recording\n/m, "");
  };
  const secret = await outcome("id_rsa");
  const nonsense = await outcome("zzzqqqxxx");
  // the visible page differs only in what the person typed
  const strip = (t: string, q: string) => t.split(q).join("");
  expect(strip(secret, "id_rsa")).toEqual(strip(nonsense, "zzzqqqxxx"));
  expect(await outcome("curl")).not.toContain("Auth: payload run");
  await page.screenshot({ path: `${SHOTS}/viewer-no-match.png`, fullPage: true });
});

test("a viewer's metadata search works: chips, facets, keyboard only", async () => {
  const page = await person(viewerSession!);
  await page.goto(RUNS());
  const box = page.getByRole("searchbox", { name: "Search runs" });
  await box.focus();
  await page.keyboard.type("status:success agent:auth-agent");
  await page.keyboard.press("Enter");
  const chips = page.getByRole("list", { name: "Search terms" });
  await expect(chips).toContainText("status:success");
  await expect(chips).toContainText("agent:auth-agent");
  await expect(page.getByRole("link", { name: "Auth: payload run" })).toBeVisible();
  await expect(page.getByTestId("matched").first()).toContainText("agent:auth-agent");
  await expect(page.getByRole("region", { name: "Narrow the results" })).toBeVisible();
  await axe(page);
  await page.screenshot({ path: `${SHOTS}/viewer-metadata-search.png`, fullPage: true });

  // keyboard: reach the first chip's remove button with Tab and press Enter
  const remove = page.getByRole("button", { name: "Remove status:success" });
  await remove.focus();
  await expect(remove).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(chips).not.toContainText("status:success");
  await expect(box).toHaveValue("agent:auth-agent");

  // help opens from the keyboard
  await page.getByRole("button", { name: "Search help" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("list", { name: "Searchable fields" })).toBeVisible();

  // a malformed query is explained at its position
  await search(page, "agent:auth-agent bogus:1");
  await expect(page.locator(".search-problem")).toContainText("could not be understood");
  await expect(page.locator(".search-caret")).toBeVisible();
  await axe(page);
  await page.screenshot({ path: `${SHOTS}/viewer-parse-error.png`, fullPage: true });
});

test("no horizontal scroll on a phone, results and refusal", async () => {
  const page = await person(viewerSession!);
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto(RUNS());
  await search(page, "status:success");
  await expect(page.getByRole("link", { name: "Auth: payload run" })).toBeVisible();
  expect(await noOverflow(page)).toBeLessThanOrEqual(0);
  await search(page, "file:x");
  await expect(page.locator(".search-problem")).toBeVisible();
  expect(await noOverflow(page)).toBeLessThanOrEqual(0);
  await page.screenshot({ path: `${SHOTS}/viewer-refused-mobile.png`, fullPage: true });
});

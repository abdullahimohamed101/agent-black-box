import { expect, test, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { enterReplay, openRun, scrub, seekTo } from "./replay";
import { signIn } from "./session";

// API-backed replay for a viewer (Phase 12 acceptance 4). Runs only through scripts/auth-e2e.sh's environment: a real API,
// the built web server on :3160, a workspace OWNER (holds payload.read) and a VIEWER (does not), and a run whose events carry
// a payload, a shell command and a file path. The viewer's replay must show metadata only, fetch nothing of its own and
// leak none of that content; the owner's replay of the same run is the control. (The script's Playwright command lists
// this file: see the plan's closing evidence.)
const A = process.env.E2E_WORKSPACE;
const run = process.env.E2E_RUN_PAYLOAD;
const ownerSession = process.env.E2E_SEARCH_OWNER_SESSION;
const viewerSession = process.env.E2E_SEARCH_VIEWER_SESSION;
const commandText = process.env.E2E_COMMAND_TEXT;
const pathText = process.env.E2E_PATH_TEXT;
const payloadText = process.env.E2E_PAYLOAD_TEXT;
test.skip(
  !A || !run || !ownerSession || !viewerSession || !commandText || !pathText || !payloadText,
  "needs scripts/auth-e2e.sh",
);
test.use({ baseURL: "http://localhost:3160", viewport: { width: 1440, height: 900 } });
const ORIGIN = "http://localhost:3160";
const BASE = () => `/w/${A}/projects/all`;

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

const surface = (page: Page) =>
  page.evaluate(() => {
    const parts = [document.body.innerText, document.body.textContent ?? ""];
    for (const el of document.querySelectorAll("*"))
      for (const a of el.attributes) parts.push(a.value);
    return parts.join("\n");
  });

test("a viewer's replay shows metadata only, fetches nothing and leaks no content", async () => {
  const page = await person(viewerSession!);
  const requests: { method: string; url: string }[] = [];
  page.on("request", (r) => {
    if (!/^(data|blob):/.test(r.url())) requests.push({ method: r.method(), url: r.url() });
  });
  await openRun(page, BASE(), run!);
  await page.waitForLoadState("networkidle");
  const before = requests.length;
  await enterReplay(page);
  const last = Number(await scrub(page).getAttribute("max"));
  expect(last).toBeGreaterThanOrEqual(5);
  for (let i = 0; i <= last; i++) {
    await seekTo(page, i);
    const text = await surface(page);
    for (const s of [commandText!, pathText!, payloadText!])
      expect(text, `leaked at ${i}`).not.toContain(s);
  }
  // the inspector on a step: the withheld wording, no content
  await page.getByRole("option").filter({ hasText: "file.modified" }).first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.locator("body")).toContainText("content withheld for your role");
  const text = await surface(page);
  for (const s of [commandText!, pathText!, payloadText!]) expect(text).not.toContain(s);
  // what the page asked for after replay started: only the one event the inspector was opened on (the existing
  // per-event route the run page always uses), never anything else; every request same-origin GET
  const mine = requests.slice(before).filter((r) => new URL(r.url).pathname !== "/icon.svg");
  for (const r of mine) {
    expect(new URL(r.url).origin).toBe(ORIGIN);
    expect(r.method).toBe("GET");
    expect(new URL(r.url).pathname, r.url).toMatch(/\/v1\/runs\/[^/]+\/events\/[^/]+$/);
  }
  expect(mine.length).toBeLessThanOrEqual(1);
});

test("the control: the owner's replay of the same run shows the command and the path", async () => {
  const page = await person(ownerSession!);
  await openRun(page, BASE(), run!);
  await enterReplay(page);
  await seekTo(page, Number(await scrub(page).getAttribute("max")));
  await expect(page.locator("body")).toContainText(pathText!);
  await expect(page.locator("body")).toContainText(commandText!);
});

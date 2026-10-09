import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { signIn, workspace, workspaceHeader } from "./session";

// Runs only through scripts/coding-e2e.sh: the scripted coding agent already wrote its run through the SDK into a
// real API; the built web server on :3140 reads it through its proxy. The planted secrets come from the script.
const runId = process.env.E2E_CODING_RUN_ID;
const planted: string[] = JSON.parse(process.env.E2E_CODING_PLANTED ?? "[]");
test.skip(!runId || planted.length === 0, "needs scripts/coding-e2e.sh");
test.use({ baseURL: "http://localhost:3140", viewport: { width: 1400, height: 1000 } });
test.beforeEach(async ({ context, baseURL }) => signIn(context, baseURL!));
const SHOTS = "../../docs/screenshots/phase-6";

const open = async (page: Page) => {
  await page.goto(`/w/${workspace()}/projects/all/runs/${runId}`);
  await expect(page.getByTestId("story")).toBeVisible();
};
const step = (page: Page, name: RegExp) =>
  page.getByTestId("story").getByRole("button", { name }).first();
const contentRequests = (page: Page) => {
  const urls: string[] = [];
  page.on("request", (r) => {
    if (/\/api\/abb\/v1\/artifacts\/[^/]+\/content/.test(r.url())) urls.push(r.url());
  });
  return urls;
};
const noSecrets = async (page: Page) => {
  const text = await page.locator("body").innerText();
  for (const s of planted) expect(text, `page shows a planted secret`).not.toContain(s);
};

test("the run reads as a story: read, model, edit, failing tests, retry, edit, passing tests, commit, push", async ({
  page,
}) => {
  await open(page);
  await expect(page.getByTestId("headline")).toContainText("Succeeded");
  await expect(page.getByTestId("headline")).toContainText("1 retry");
  const steps = await page.getByTestId("story").getByRole("listitem").allInnerTexts();
  const flat = steps.map((s) => s.replace(/\s+/g, " ").trim());
  const idx = (re: RegExp) => flat.findIndex((s) => re.test(s));
  expect(idx(/Read app\/session\.py/)).toBeGreaterThanOrEqual(0);
  const fail = idx(/Tests, attempt 1: fail.*1 failed, 5 passed/);
  const retry = idx(/Retry #1/);
  const pass = idx(/Tests, attempt 2: pass.*6 of 6 passed/);
  const edit1 = idx(/Edit \(modified\) app\/session\.py/);
  expect(edit1).toBeGreaterThanOrEqual(0);
  expect(edit1).toBeLessThan(fail);
  expect(fail).toBeLessThan(retry);
  expect(retry).toBeLessThan(pass);
  expect(flat.slice(pass).join(" ")).toMatch(/Commit.*Push origin fix\/oauth-session-expiry/);
  await expect(page.getByTestId("first-error")).toContainText("exit 1");
  await noSecrets(page);
  await page.screenshot({ path: `${SHOTS}/story.png`, fullPage: true });
});

test("a diff opens highlighted, with line numbers, and flags authentication code", async ({
  page,
}) => {
  await open(page);
  await step(page, /Edit \(modified\) app\/session\.py/).click();
  const diff = page.getByTestId("diff");
  await expect(diff).toBeVisible();
  await expect(diff).toContainText("REFRESH_SKEW_SECONDS");
  expect(await diff.locator(".diff-add").count()).toBeGreaterThan(0);
  expect(await diff.locator(".diff-del").count()).toBeGreaterThan(0);
  expect(await diff.locator(".tok-kw").count()).toBeGreaterThan(0);
  await expect(page.getByTestId("sensitive-path")).toContainText("authentication code");
  await expect(page.getByTestId("file-section")).toContainText(/\+1 -1/);
  await page.screenshot({ path: `${SHOTS}/diff.png`, fullPage: true });
  await noSecrets(page);
});

test("a failing test run shows the shell panel, and its output loads only on request", async ({
  page,
}) => {
  const requests = contentRequests(page);
  await open(page);
  await step(page, /Tests, attempt 1: fail/).click();
  const panel = page.getByTestId("shell-panel");
  await expect(panel).toContainText("python -m unittest discover -s tests -t .");
  await expect(panel).toContainText("/workspace");
  await expect(page.getByTestId("exit-code")).toContainText("✕ 1");
  await expect(page.getByTestId("risk-class")).toContainText("R1 Local, reversible change");
  await expect(page.getByTestId("test-result")).toContainText("1 failed");
  await expect(page.getByTestId("test-result")).toContainText(
    "test_refresh_keeps_the_refresh_token",
  );
  expect(requests).toHaveLength(0); // opening the panel fetched no output
  await panel.getByRole("button", { name: /Show stderr/ }).click();
  const stderr = page.getByLabel("stderr");
  await expect(stderr).toContainText("AssertionError");
  await expect(stderr).toContainText("[REDACTED:"); // the planted refresh token in the assertion message
  expect(requests).toHaveLength(1);
  await page.screenshot({ path: `${SHOTS}/shell-failed.png`, fullPage: true });
  await noSecrets(page);
});

test("a ~220 KB log loads in chunks, with the planted secrets redacted", async ({ page }) => {
  const requests = contentRequests(page);
  await open(page);
  await step(page, /Command\s*tail -n 3300/).click();
  const panel = page.getByTestId("shell-panel");
  await expect(panel).toContainText("tail -n 3300 logs/auth-server.log");
  await expect(page.getByTestId("risk-class")).toContainText("R0 Observation only");
  expect(requests).toHaveLength(0);
  await panel.getByRole("button", { name: /Show stdout/ }).click();
  const progress = page.getByTestId("artifact-progress");
  await expect(progress).toContainText("65,536 of");
  const total = Number(
    /of ([\d,]+) bytes/.exec((await progress.textContent()) ?? "")![1]!.replace(/,/g, ""),
  );
  expect(total).toBeGreaterThan(200_000);
  expect(requests).toHaveLength(1);
  await panel.getByRole("button", { name: "Load more" }).click();
  await expect(progress).toContainText("131,072 of");
  expect(requests).toHaveLength(2);
  while (await panel.getByRole("button", { name: "Load more" }).count()) {
    const before = requests.length;
    await panel.getByRole("button", { name: "Load more" }).click();
    await expect.poll(() => requests.length).toBeGreaterThan(before);
    await page.waitForTimeout(100);
  }
  const text = (await page.getByLabel("stdout").textContent()) ?? "";
  expect(text.length).toBeGreaterThan(200_000);
  for (const marker of ["[REDACTED:aws_access_key]", "[REDACTED:private_key]", "[REDACTED:env]"])
    expect(text).toContain(marker);
  expect(requests).toHaveLength(4); // 220 KB at 64 KiB per chunk
  await page.screenshot({ path: `${SHOTS}/shell-large-output.png` });
  for (const s of planted) expect(text).not.toContain(s);
});

test("planted secrets are in no API response the browser can reach", async ({ page }) => {
  await open(page);
  const headers = await workspaceHeader(page);
  const events = await page.request.get(`/api/abb/v1/runs/${runId}/events?limit=500`, { headers });
  const body = await events.text();
  for (const s of planted) expect(body).not.toContain(s);
  const items = (JSON.parse(body) as { items: { attributes: Record<string, unknown> }[] }).items;
  const refs = items.flatMap((e) =>
    Object.values(e.attributes).filter((v) => typeof v === "string" && v.startsWith("artifact://")),
  ) as string[];
  expect(refs.length).toBeGreaterThan(8);
  for (const ref of refs) {
    const id = ref.slice("artifact://".length);
    let offset: number | null = 0;
    while (offset !== null) {
      const r = await page.request.get(
        `/api/abb/v1/artifacts/${id}/content?offset=${offset}&limit=262144`,
        { headers },
      );
      expect(r.status()).toBe(200);
      const chunk = (await r.json()) as { content: string; next_offset: number | null };
      for (const s of planted) expect(chunk.content).not.toContain(s);
      offset = chunk.next_offset;
    }
  }
  // the proxy never lets the browser write or reach anything else
  expect(
    (await page.request.put(`/api/abb/v1/artifacts/${refs[0]!.slice(11)}`, { data: "x" })).status(),
  ).toBeGreaterThanOrEqual(400);
  expect((await page.request.get(`/api/abb/v1/artifacts`)).status()).toBe(404);
});

test("the run page has no serious accessibility violations and no console errors", async ({
  page,
}) => {
  const errors: string[] = [];
  // The app has no favicon yet (a pre-existing 404 that is not a page error).
  page.on(
    "console",
    (m) =>
      m.type() === "error" && !m.location().url.endsWith("/favicon.ico") && errors.push(m.text()),
  );
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("response", (r) => r.status() >= 400 && errors.push(`${r.status()} ${r.url()}`));
  await open(page);
  await step(page, /Edit \(modified\) app\/session\.py/).click();
  await expect(page.getByTestId("diff")).toBeVisible();
  const bad = (
    await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze()
  ).violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes[0]?.html.slice(0, 120)}`)).toEqual([]);
  expect(errors).toEqual([]);
});

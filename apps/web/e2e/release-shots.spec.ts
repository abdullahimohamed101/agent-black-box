import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { settleAnimations } from "./motion";
import { signIn, workspace } from "./session";

/**
 * The release screenshot set (Phase 20, D11): real pages of the real stack that `make demo` seeded, light and dark, 1440x900.
 * Run through scripts/screenshots.sh, which brings up a throwaway demo stack. Gated on SHOTS_DIR so the normal e2e run never
 * writes into docs/screenshots (KI-123).
 *
 * Everything that differs between two runs of the script is normalised, and nothing else is touched:
 *  - the browser clock is frozen at the moment the pending approval was requested plus 90 s, so "3h ago" and "1m ago" are the
 *    same every run (the seeded runs are backdated relative to the demo's own clock);
 *  - the approval's `seconds_left` is recomputed against that frozen clock (otherwise it counts the real minutes between
 *    seeding and the picture);
 *  - absolute timestamps and dates in the text are shifted by one constant, so the intervals between them stay true;
 *  - a measured latency in the inspector ("tool.latency_ms 5000.063") keeps its whole milliseconds: the scripted agent sleeps for
 *    real, so the fraction differs between runs;
 *  - a first-event offset of "+1ms" reads "+0ms" (scripted timestamps jitter by a millisecond);
 *  - ids ("evt_01M5…", and the shortened "run …TG04F1Z7") are replaced by a constant of the same shape.
 * The text of the data itself (names, figures, findings) is never rewritten.
 */
const OUT = process.env.SHOTS_DIR ? path.resolve(process.env.SHOTS_DIR) : "";
test.skip(!OUT, "set SHOTS_DIR to write the release screenshots (scripts/screenshots.sh does)");
test.describe.configure({ mode: "serial" });

const BASE = `/w/${process.env.E2E_WORKSPACE ?? "local"}/projects/all`;
const FAILED = "Answer support ticket T-1047";
const MULTI = "Ship feature: multi-agent";
// Where every absolute timestamp lands: a fixed, plausible day in the demo's own timezone (UTC).
const TIMESTAMP_BASE = Date.parse("2026-03-02T09:00:00Z");
const DAY = 86_400_000;

let frozenNow = 0; // epoch ms the page clock shows
let shiftMs = 0; // added to every absolute timestamp in the text
let dayShiftMs = 0; // the same, in whole days, for a date without a time
const boxes: Record<string, Record<string, Record<string, unknown>>> = {};

test.beforeAll(async ({ browser, baseURL }) => {
  const ctx = await browser.newContext();
  await signIn(ctx, baseURL!);
  const me = (await (await ctx.request.get(`${baseURL}/api/abb/v1/me`)).json()) as {
    memberships: { workspace: { id: string; slug: string } }[];
  };
  const ws = me.memberships.find((m) => m.workspace.slug === workspace())?.workspace;
  if (!ws) throw new Error(`the session is not a member of ${workspace()}`);
  const res = await ctx.request.get(`${baseURL}/api/abb/v1/approvals?state=pending&limit=5`, {
    headers: { "x-abb-workspace": ws.id },
  });
  if (!res.ok())
    throw new Error(`approvals list answered ${res.status()}: ${(await res.text()).slice(0, 200)}`);
  const items = ((await res.json()) as { items: { requested_at: string }[] }).items;
  if (items.length !== 1)
    throw new Error(`the demo should leave exactly one pending approval, found ${items.length}`);
  const requested = Date.parse(items[0]!.requested_at);
  frozenNow = requested + 90_000;
  shiftMs = TIMESTAMP_BASE - Math.floor(requested / 1000) * 1000; // the page shows whole seconds
  dayShiftMs = (Math.floor((requested + shiftMs) / DAY) - Math.floor(requested / DAY)) * DAY;
  await ctx.close();
});

async function prepare(page: Page) {
  await page.clock.setFixedTime(frozenNow);
  // Recompute the countdown against the frozen clock, so it is true at the instant the page shows.
  await page.route(/\/api\/abb\/v1\/approvals(\/apr_[^/?]+|\?.*)$/, async (route) => {
    const res = await route.fetch();
    const patch = (a: { expires_at?: string; seconds_left?: number | null }) => {
      if (a.expires_at && typeof a.seconds_left === "number")
        a.seconds_left = Math.round((Date.parse(a.expires_at) - frozenNow) / 1000);
    };
    const body = await res.json();
    if (Array.isArray(body?.items)) body.items.forEach(patch);
    else patch(body);
    await route.fulfill({ response: res, json: body });
  });
}

/** Rewrite the varying text (see the header) in place, then park the pointer and let everything settle. */
async function normalise(page: Page) {
  await page.evaluate(
    ({ shift, dayShift }) => {
      const pad = (n: number) => String(n).padStart(2, "0");
      const fmt = (ms: number, withTime: boolean, sep: string, z: string) => {
        const d = new Date(ms);
        const day = `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
        return withTime
          ? `${day}${sep}${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}${z}`
          : day;
      };
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        const before = n.nodeValue ?? "";
        const after = before
          .replace(/(\d{4}-\d{2}-\d{2})([ T])(\d{2}:\d{2}:\d{2})(Z?)/g, (_m, d, sep, t, z) =>
            fmt(Date.parse(`${d}T${t}Z`) + shift, true, sep, z),
          )
          .replace(/\b(\d{4}-\d{2}-\d{2})\b(?![ T]\d{2}:)/g, (_m, d) =>
            fmt(Date.parse(`${d}T00:00:00Z`) + dayShift, false, "", ""),
          )
          .replace(
            /\b(evt|spn|trc|run|apr|prj|ws|usr)_[0-9A-Z]{26}\b/g,
            "$1_01DEMO00000000000000000000",
          )
          .replace(/…[0-9A-Z]{8}\b/g, "…K7Q2M4X8");
        const offset = /^\+1ms$/.test(before) ? "+0ms" : null;
        // React renders "run …{id.slice(-8)}" as two text nodes
        const tail =
          offset ??
          (/^[0-9A-Z]{8}$/.test(before) && n.previousSibling?.nodeValue?.endsWith("…")
            ? "K7Q2M4X8"
            : after);
        if (tail !== before) n.nodeValue = tail;
      }
      for (const dd of document.querySelectorAll('[role="dialog"] dd, [role="dialog"] td')) {
        if (
          /^\d+\.\d{3}$/.test(dd.textContent ?? "") &&
          dd.previousElementSibling?.textContent?.endsWith("_ms")
        )
          dd.textContent = (dd.textContent ?? "").replace(/\.\d{3}$/, ".000");
      }
      (document.activeElement as HTMLElement | null)?.blur();
    },
    { shift: shiftMs, dayShift: dayShiftMs },
  );
  await page.mouse.move(1439, 899);
  await page.evaluate(() => document.fonts.ready);
  await settleAnimations(page);
  await page.waitForTimeout(300);
}

async function box(page: Page, locator: ReturnType<Page["locator"]>) {
  const b = await locator.first().boundingBox();
  if (!b) throw new Error("no box for a callout target");
  return {
    x: Math.round(b.x),
    y: Math.round(b.y),
    w: Math.round(b.width),
    h: Math.round(b.height),
  };
}

async function shot(
  page: Page,
  name: string,
  scheme: string,
  targets: Record<string, ReturnType<Page["locator"]>> = {},
) {
  await normalise(page);
  mkdirSync(OUT, { recursive: true });
  const found: Record<string, unknown> = {};
  for (const [k, loc] of Object.entries(targets)) found[k] = await box(page, loc);
  (boxes[name] ??= {})[scheme] = found;
  await page.screenshot({
    path: path.join(OUT, `${name}-${scheme}.png`),
    animations: "disabled",
    caret: "hide",
  });
}

for (const scheme of ["light", "dark"] as const) {
  test.describe(`${scheme} scheme`, () => {
    test.use({
      viewport: { width: 1440, height: 900 },
      colorScheme: scheme,
      reducedMotion: "reduce",
      deviceScaleFactor: 1,
    });
    test.beforeEach(async ({ page, context, baseURL }) => {
      await signIn(context, baseURL!);
      await prepare(page);
    });
    test.afterAll(() => {
      const file = path.join(OUT, "boxes.json");
      let all: typeof boxes = {};
      try {
        all = JSON.parse(readFileSync(file, "utf8"));
      } catch {
        /* first scheme */
      }
      for (const [k, v] of Object.entries(boxes)) all[k] = { ...all[k], ...v };
      writeFileSync(file, JSON.stringify(all, null, 1) + "\n");
    });

    test(`runs list (${scheme})`, async ({ page }) => {
      await page.goto(`${BASE}/runs`);
      await expect(page.locator("tbody tr")).toHaveCount(12);
      await expect(page.getByText("Failed at step 6")).toBeVisible();
      // The findings column fills in as the worker analyses each run: wait for every run that has findings.
      for (const [run, findings] of [
        [FAILED, "3 · critical"],
        ["Answer support ticket T-1046", "1 · critical"],
        ["Fix session refresh bug", "1 · critical"],
        ["Answer support ticket T-1044", "2 · warning"],
        ["Clean up old releases", "3 security findings"],
      ] as const)
        await expect(page.locator("tbody tr", { hasText: run })).toContainText(findings, {
          timeout: 60_000,
        });
      await shot(page, "runs-list", scheme, {
        failedRow: page.locator("tbody tr", { hasText: FAILED }),
        failedWhere: page.getByText(/Failed at step 6/),
        narrow: page.getByRole("region", { name: "Narrow the results" }),
      });
    });

    test(`failed run story (${scheme})`, async ({ page }) => {
      await page.goto(`${BASE}/runs`);
      await page.getByRole("link", { name: FAILED }).click();
      await expect(page.getByTestId("headline")).toContainText("Failed at step 6");
      await page.getByRole("button", { name: "Go to first error" }).click();
      await expect(page.getByRole("dialog")).toContainText("What went wrong");
      await shot(page, "failed-run-story", scheme, {
        headline: page.getByTestId("headline"),
        detail: page.getByTestId("headline-detail"),
        faultRow: page.locator(".timeline [data-first-error]"),
        faultLabel: page.locator(".tl-fault-label"),
        inspector: page.getByRole("dialog"),
      });
    });

    test(`findings (${scheme})`, async ({ page }) => {
      await page.goto(`${BASE}/runs`);
      await page.getByRole("link", { name: FAILED }).click();
      await expect(page.getByTestId("headline")).toContainText("Failed at step 6");
      await page.getByRole("tab", { name: /^Findings/ }).click();
      await expect(page.getByTestId("findings")).toBeVisible();
      await expect(page.getByText("Analysis is current.")).toBeVisible({ timeout: 60_000 });
      await expect(page.getByRole("button", { name: "Show evidence" })).toHaveCount(3);
      await shot(page, "findings", scheme, {
        criticalRow: page.getByTestId("findings").getByText("the run failed: a tool failed"),
        showEvidence: page.getByRole("button", { name: "Show evidence" }),
      });
    });

    test(`cost analytics (${scheme})`, async ({ page }) => {
      test.setTimeout(180_000);
      // The worker rolls the day's spend up a little after the last event; on a fresh stack the report fills in. Take the
      // picture once two reads, a few seconds apart, agree.
      let previous = "";
      await expect(async () => {
        await page.goto(`${BASE}/analytics`);
        const headline = page.getByTestId("report-headline");
        await expect(headline).toContainText("spent in the last 7 days", { timeout: 4000 });
        const now = (await headline.textContent()) ?? "";
        const settled = now === previous;
        previous = now;
        await page.waitForTimeout(3000);
        expect(settled, "the cost report is still changing").toBe(true);
      }).toPass({ timeout: 120_000, intervals: [1000] });
      await expect(page.getByText("Where the money was wasted")).toBeVisible();
      await shot(page, "cost-analytics", scheme, {
        headline: page.getByTestId("report-headline"),
        wasted: page.getByRole("heading", { name: "Where the money was wasted" }).locator(".."),
        daily: page.getByRole("heading", { name: "Daily spend" }).locator(".."),
      });
    });

    test(`multi-agent waterfall (${scheme})`, async ({ page }) => {
      await page.goto(`${BASE}/runs`);
      await page.getByRole("link", { name: MULTI }).click();
      await page.getByRole("tab", { name: "Waterfall", exact: true }).click();
      await expect(page.getByTestId("waterfall")).toBeVisible();
      await page.getByRole("button", { name: "Lanes", exact: true }).click();
      await expect(page.getByRole("button", { name: "Lanes", exact: true })).toHaveAttribute(
        "aria-pressed",
        "true",
      );
      await shot(page, "waterfall-multi-agent", scheme, {
        waterfall: page.getByTestId("waterfall"),
        faultLabel: page.getByText("First error, step 15"),
        criticalBar: page.locator(".wf-bar[data-critical]").first(),
      });
    });

    test(`approval (${scheme})`, async ({ page }) => {
      await page.goto(`/w/${workspace()}/approvals`);
      await expect(page.getByTestId("approval-row")).toHaveCount(1);
      await page.getByRole("link", { name: "github.push" }).click();
      await expect(page.getByRole("button", { name: "Approve", exact: true })).toBeVisible();
      await shot(page, "policy-approval", scheme, {
        row: page.getByTestId("approval-row"),
        timeLeft: page.getByTestId("time-left"),
        detail: page.locator("dl", { hasText: "Capability" }),
      });
    });
  });
}

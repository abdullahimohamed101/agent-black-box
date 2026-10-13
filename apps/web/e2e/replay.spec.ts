import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Request } from "@playwright/test";
import { settleAnimations } from "./motion";
import {
  BASE,
  CODING,
  FAILED,
  RUN,
  VIEWER,
  enterReplay,
  expectedAt,
  figure,
  firstErrorIndex,
  openRun,
  overflowX,
  replayButton,
  scrub,
  seekTo,
  transport,
} from "./replay";
import { failureRetryRun, successRun } from "../src/fixtures/runs";
import { baselineCodingRun } from "../src/fixtures/codingRuns";
import { formatCost } from "../src/lib/format";
import { ReplayPacer } from "../src/lib/replay/pacing";
import { API_WITHHELD, isContentAttribute } from "../src/fixtures/withhold";

// Phase 12 verification (fixtures build): replay makes no request of its own, the success path of D5, the viewer case,
// the keyboard map, the URL, reduced motion, the phone layout and axe. Expected values come from the fixture events,
// computed here, never from the code under test.
const failed = failureRetryRun();
const errIdx = firstErrorIndex(failed);
const play = (page: Page) => page.getByRole("button", { name: "Play", exact: true });
/** Focus on nothing in particular (the page itself), so a shortcut is tried the way a person at the page would. */
const blur = (page: Page) =>
  page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
const pause = (page: Page) => page.getByRole("button", { name: "Pause", exact: true });

test.describe("acceptance 3: network isolation", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("every request is a same-origin GET, and playing from loaded data makes none", async ({
    page,
    context,
    baseURL,
  }) => {
    const origin = new URL(baseURL!).origin;
    const all: { url: string; method: string }[] = [];
    const violations: string[] = [];
    const record = (r: Request) => {
      if (/^(data|blob):/.test(r.url())) return;
      all.push({ url: r.url(), method: r.method() });
    };
    page.on("request", record);
    // Anything that is not a same-origin GET is refused outright, so the test fails loudly rather than reaching out.
    await context.route("**/*", (route) => {
      const req = route.request();
      if (/^(data|blob):/.test(req.url())) return route.continue();
      if (req.method() !== "GET" || new URL(req.url()).origin !== origin) {
        violations.push(`${req.method()} ${req.url()}`);
        return route.abort();
      }
      return route.continue();
    });

    await openRun(page, BASE, FAILED);
    // The browser fetches the tab icon on its own schedule, a little after load: let it land before counting, so that
    // what is counted afterwards is what replay itself does.
    await expect
      .poll(() => all.some((r) => /\/icon\.svg/.test(r.url)), { timeout: 5000 })
      .toBe(true)
      .catch(() => undefined);
    await page.waitForLoadState("networkidle");
    const before = all.length;
    await enterReplay(page);
    await page.getByRole("button", { name: "5x" }).click();
    await play(page).click();
    // plays to the first error and stops there by itself
    await expect(page.getByTestId("first-error")).toBeVisible({ timeout: 15_000 });
    await expect(play(page)).toBeVisible();
    await expect(scrub(page)).toHaveValue(String(errIdx));
    // and on to the end
    await play(page).click();
    await expect(scrub(page)).toHaveValue(String(failed.events.length - 1), { timeout: 20_000 });
    await expect(play(page)).toBeVisible();
    await page.waitForLoadState("networkidle");

    // Chrome re-fetches the tab icon (`/icon.svg`, resource type "other") whenever the address bar's URL changes, and
    // replay writes `?t=` when playback pauses or the person seeks. That is the browser's own request, not the page's:
    // the control test below shows the identical fetch after a plain URL change with replay never entered.
    const isIcon = (r: { url: string }) => new URL(r.url).pathname === "/icon.svg";
    const during = all.slice(before).filter((r) => !isIcon(r));
    const icons = all.slice(before).filter(isIcon).length;
    test.info().annotations.push({
      type: "requests",
      description: `${all.length} in total, ${during.length} by the page after replay started (plus ${icons} browser tab-icon refreshes)`,
    });
    expect(violations).toEqual([]);
    expect(during.map((r) => `${r.method} ${r.url}`)).toEqual([]);
    for (const r of all) {
      expect(new URL(r.url).origin, r.url).toBe(origin);
      expect(r.method, r.url).toBe("GET");
    }
  });
});

test.describe("the tab-icon refresh", () => {
  test("Chrome fetches /icon.svg after any URL change, replay or not (control for the isolation test)", async ({
    page,
  }) => {
    const icons: string[] = [];
    page.on("request", (r) => {
      if (new URL(r.url()).pathname === "/icon.svg") icons.push(r.url());
    });
    await openRun(page, BASE, FAILED);
    await page.waitForTimeout(1500);
    const before = icons.length;
    await page.evaluate(() => window.history.replaceState(null, "", `${location.pathname}?x=1`));
    await expect.poll(() => icons.length, { timeout: 5000 }).toBeGreaterThan(before);
  });
});

test.describe("acceptance 7: the success test (D5)", () => {
  for (const vp of [
    { name: "1440 px", width: 1440, height: 900 },
    { name: "375 px", width: 375, height: 812 },
  ]) {
    test(`a first-time user opens replay, presses play and lands on the first error (${vp.name})`, async ({
      page,
    }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await openRun(page, BASE, FAILED);
      let actions = 0;
      await replayButton(page).click();
      actions++;
      await expect(transport(page)).toBeVisible();
      await play(page).click();
      actions++;
      const started = Date.now();
      // it stops at the first error without a third action
      await expect(page.getByTestId("first-error")).toBeVisible({ timeout: 15_000 });
      await expect(play(page)).toBeVisible();
      const took = Date.now() - started;
      expect(actions).toBeLessThanOrEqual(3);
      expect(took).toBeLessThan(15_000);
      // the data says the same: the clamped gaps up to the first error sum to well under 15 s at 1x
      const pacer = new ReplayPacer(failed.events);
      let planned = 0;
      for (let i = 1; i <= errIdx; i++) planned += pacer.delayBefore(i, 1);
      expect(planned).toBeLessThan(15_000);

      // the headline, cost so far, retries and the fault line all match that moment
      const x = expectedAt(failed, errIdx);
      await expect(scrub(page)).toHaveValue(String(errIdx));
      await expect(page.getByTestId("first-error")).toHaveText(
        /^First error at step \d+ \(\+6\.2 s\): run_migration, ConnectionTimeout\s+Go\s+e$/,
      );
      expect(await figure(page, "Cost")).toBe(formatCost(x.cost));
      expect(await figure(page, "Retries")).toBe(String(x.retries));
      expect(await figure(page, "Errors")).toBe(String(x.errors));
      await expect(page.locator(".tl-fault")).toBeVisible();
      await expect(page.getByTestId("headline-detail")).toContainText(
        `${formatCost(x.cost)} so far`,
      );
      await expect(page.getByTestId("replay-status")).toContainText("as of this moment");
      // nothing past the error is in the list
      const types = await page.locator(".tl-type").allInnerTexts();
      expect(types.length).toBeLessThanOrEqual(errIdx + 1);
      expect(types).not.toContain("retry.attempted");
      expect(types.at(-1)).toBe(failed.events[errIdx]!.event_type);
    });
  }

  test("the first viewport shows header, transport and the start of the timeline", async ({
    page,
  }) => {
    for (const [w, h] of [
      [1440, 900],
      [375, 812],
    ]) {
      await page.setViewportSize({ width: w!, height: h! });
      await openRun(page, BASE, FAILED);
      await enterReplay(page);
      const bar = (await transport(page).boundingBox())!;
      expect(bar.y, `transport top at ${w}`).toBeGreaterThanOrEqual(0);
      expect(bar.y + bar.height, `transport bottom at ${w}`).toBeLessThan(h!);
      const list = (await page.getByRole("listbox", { name: /Run timeline/ }).boundingBox())!;
      expect(list.y, `timeline top at ${w}`).toBeLessThan(h! - 40);
      const row = (await page.getByRole("option").first().boundingBox())!;
      expect(row.y + row.height, `first row visible at ${w}`).toBeLessThanOrEqual(h!);
    }
  });
});

const surface = (page: Page) =>
  page.evaluate(() => {
    const parts = [document.body.innerText, document.body.textContent ?? ""];
    for (const el of document.querySelectorAll("*"))
      for (const a of el.attributes) parts.push(a.value);
    return parts.join("\n");
  });

test.describe("acceptance 4: a viewer's replay is metadata only", () => {
  test.use({ viewport: { width: 1440, height: 900 } });
  const coding = baselineCodingRun();
  const secrets = [
    ...new Set(
      coding.events.flatMap((e) =>
        Object.entries(e.attributes).flatMap(([k, v]) =>
          isContentAttribute(k) && typeof v === "string" && v.length >= 3 ? [v] : [],
        ),
      ),
    ),
  ];

  test("no content text, no extra requests, withheld wording stays", async ({ page }) => {
    const requests: string[] = [];
    page.on("request", (r) => {
      if (!/^(data|blob):/.test(r.url())) requests.push(`${r.method()} ${r.url()}`);
    });
    await openRun(page, VIEWER, CODING);
    await page.waitForLoadState("networkidle");
    const before = requests.length;
    await enterReplay(page);
    expect(secrets.length).toBeGreaterThan(3);
    // walk the run: start, a few steps, the middle, the end
    for (const i of [0, 3, Math.floor(coding.events.length / 2), coding.events.length - 1]) {
      await seekTo(page, i);
      const text = await surface(page);
      expect(text).not.toContain(API_WITHHELD);
      for (const s of secrets) expect(text, `leaked ${s}`).not.toContain(s);
    }
    await page.getByRole("button", { name: "Step back" }).click();
    expect(requests.slice(before).filter((r) => !/\/icon\.svg/.test(r))).toEqual([]);
    // the inspector on a step shows the withheld wording, as the viewer's run page always does
    await page.getByRole("option").last().click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.locator("body")).toContainText("content withheld for your role");
    const text = await surface(page);
    for (const s of secrets) expect(text, `leaked ${s}`).not.toContain(s);
  });
});

test.describe("keyboard", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("Space, arrows, brackets, r and Esc work and do not clash with j, k and e", async ({
    page,
  }) => {
    await openRun(page, BASE, FAILED);
    await blur(page);
    await page.keyboard.press("r");
    await expect(transport(page)).toBeVisible();
    await expect(replayButton(page)).toHaveAttribute("aria-pressed", "true");
    await expect(scrub(page)).toHaveValue("0");
    // Right and Left step by a group (a request's started and completed events are one stop)
    await page.keyboard.press("ArrowRight");
    const afterRight = Number(await scrub(page).inputValue());
    expect(afterRight).toBeGreaterThan(0);
    await page.keyboard.press("ArrowLeft");
    await expect(scrub(page)).toHaveValue("0");
    // brackets change the speed
    await page.keyboard.press("]");
    await expect(page.getByRole("button", { name: "2x" })).toHaveAttribute("aria-pressed", "true");
    await page.keyboard.press("]");
    await expect(page.getByRole("button", { name: "5x" })).toHaveAttribute("aria-pressed", "true");
    await page.keyboard.press("[");
    await expect(page.getByRole("button", { name: "2x" })).toHaveAttribute("aria-pressed", "true");
    // Space plays and pauses
    await page.keyboard.press("Space");
    await expect(pause(page)).toBeVisible();
    await page.keyboard.press("Space");
    await expect(play(page)).toBeVisible();

    // with the timeline focused: j and k move the selection, Space still plays, e jumps to the first error, none of
    // them changes the speed or leaves replay
    await seekTo(page, errIdx + 2);
    const list = page.getByRole("listbox", { name: /Run timeline/ });
    await list.focus();
    await page.keyboard.press("j");
    await page.keyboard.press("j");
    const active = await list.getAttribute("aria-activedescendant");
    expect(active).toBeTruthy();
    await page.keyboard.press("k");
    expect(await list.getAttribute("aria-activedescendant")).not.toBe(active);
    await expect(page.getByRole("button", { name: "2x" })).toHaveAttribute("aria-pressed", "true");
    await page.keyboard.press("Space");
    await expect(pause(page)).toBeVisible();
    await page.keyboard.press("Space");
    await expect(play(page)).toBeVisible();
    await page.keyboard.press("e");
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(scrub(page)).toHaveValue(String(errIdx));
    // Esc closes the inspector first, and only the second Esc leaves replay
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(transport(page)).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(transport(page)).toHaveCount(0);
    await expect(replayButton(page)).toHaveAttribute("aria-pressed", "false");
  });

  test("keys typed into the search box are text, not shortcuts", async ({ page }) => {
    await openRun(page, BASE, FAILED);
    await enterReplay(page);
    // a text field anywhere on the page (the run page has none of its own, so one is added the way a plugin or an
    // extension would): typing in it is text.
    await page.evaluate(() => {
      const input = document.createElement("input");
      input.id = "scratch";
      input.setAttribute("aria-label", "Scratch");
      document.body.appendChild(input);
      input.focus();
    });
    await page.keyboard.type("r e ");
    await expect(page.locator("#scratch")).toHaveValue("r e ");
    await expect(transport(page)).toBeVisible();
    await expect(play(page)).toBeVisible();
  });

  test("Space on a focused control operates that control once", async ({ page }) => {
    await openRun(page, BASE, FAILED);
    await enterReplay(page);
    await page.getByRole("button", { name: "5x" }).focus();
    await page.keyboard.press("Space");
    await expect(page.getByRole("button", { name: "5x" })).toHaveAttribute("aria-pressed", "true");
    await expect(play(page)).toBeVisible(); // Space did not also start playback
    await play(page).focus();
    await page.keyboard.press("Space");
    await expect(pause(page)).toBeVisible();
  });

  test("focus stays on the Replay button on entry and returns to it from the Leave button", async ({
    page,
  }) => {
    await openRun(page, BASE, FAILED);
    await replayButton(page).click();
    await expect(replayButton(page)).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(play(page)).toBeFocused();
    await page.getByRole("button", { name: "Leave replay" }).click();
    await expect(replayButton(page)).toBeFocused();
  });
});

test.describe("acceptance 6: the playhead is the URL", () => {
  test.use({ viewport: { width: 1440, height: 900 } });
  const id = (i: number) => failed.events[i]!.event_id;

  test("?t= opens at that event, survives a reload and coexists with ?view=", async ({ page }) => {
    await openRun(page, BASE, FAILED, `?t=${id(6)}`);
    await expect(scrub(page)).toHaveValue("6");
    await expect(play(page)).toBeVisible(); // opens paused
    await page.reload();
    await expect(scrub(page)).toHaveValue("6");
    await page.getByRole("radio", { name: "Waterfall" }).check();
    await expect.poll(() => new URL(page.url()).searchParams.get("view")).toBe("waterfall");
    expect(new URL(page.url()).searchParams.get("t")).toBe(id(6));
    await seekTo(page, 8);
    await expect.poll(() => new URL(page.url()).searchParams.get("t")).toBe(id(8));
    expect(new URL(page.url()).searchParams.get("view")).toBe("waterfall");
    await page.getByRole("button", { name: "Leave replay" }).click();
    await expect.poll(() => new URL(page.url()).searchParams.get("t")).toBeNull();
    expect(new URL(page.url()).searchParams.get("view")).toBe("waterfall");
  });

  test("back and forward move through the URL's playhead", async ({ page }) => {
    await openRun(page, BASE, FAILED);
    const run = new URL(page.url()).pathname;
    await page.goto(`${BASE}/runs`);
    await page.getByRole("table").waitFor();
    await page.goto(`${run}?t=${id(5)}`);
    await expect(scrub(page)).toHaveValue("5");
    await page.goBack();
    await page.getByRole("table").waitFor();
    await page.goForward();
    await expect(scrub(page)).toHaveValue("5");
  });

  test("a deep link hydrates cleanly (no React #418 or hydration warning)", async ({ page }) => {
    const bad: string[] = [];
    page.on("console", (m) => {
      if (/hydrat|#418|Minified React error/i.test(m.text())) bad.push(m.text());
    });
    page.on("pageerror", (e) => bad.push(e.message));
    await openRun(page, BASE, FAILED, `?t=${id(6)}&view=waterfall`);
    await expect(scrub(page)).toHaveValue("6");
    await page.reload();
    await expect(scrub(page)).toHaveValue("6");
    expect(bad).toEqual([]);
  });

  test("an unknown id degrades to the start with a notice", async ({ page }) => {
    await openRun(page, BASE, FAILED, "?t=evt_does_not_exist");
    await expect(page.getByText(/That moment is not in this run/)).toBeVisible();
    await expect(scrub(page)).toHaveValue("0");
    await page.getByRole("button", { name: "Dismiss" }).click();
    await expect(page.getByText(/That moment is not in this run/)).toHaveCount(0);
  });
});

test.describe("reduced motion", () => {
  test.use({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });

  test("opens paused, updates are instant and nothing animates", async ({ page }) => {
    await openRun(page, BASE, FAILED);
    await enterReplay(page);
    await expect(play(page)).toBeVisible();
    await expect(scrub(page)).toHaveValue("0");
    await seekTo(page, errIdx);
    await expect(page.locator(".tl-fault")).toBeVisible();
    // no animation or transition anywhere on the page at the moment the fault line appears
    expect(await page.evaluate(() => document.getAnimations().length)).toBe(0);
    const fault = await page.evaluate(() => {
      const el = document.querySelector(".tl-fault")!;
      return getComputedStyle(el, "::before").animationName;
    });
    expect(fault).toBe("none");
    // keyboard entry does not autoplay either
    await page.getByRole("button", { name: "Leave replay" }).click();
    await blur(page);
    await page.keyboard.press("r");
    await expect(play(page)).toBeVisible();
  });
});

test.describe("motion allowed: the fault line draws in at the moment it is reached", () => {
  test.use({ viewport: { width: 1440, height: 900 }, reducedMotion: "no-preference" });

  test("the Phase 22 cue plays when the playhead arrives, and not before", async ({ page }) => {
    await openRun(page, BASE, FAILED);
    await enterReplay(page);
    await seekTo(page, errIdx - 1);
    expect(
      await page.evaluate(() =>
        document.getAnimations().some((a) => /fault-draw/.test((a as CSSAnimation).animationName)),
      ),
    ).toBe(false);
    await page.evaluate(() => {
      (window as unknown as { __seen: boolean }).__seen = false;
      const poll = () => {
        if (
          document.getAnimations().some((a) => /fault-draw/.test((a as CSSAnimation).animationName))
        )
          (window as unknown as { __seen: boolean }).__seen = true;
        else requestAnimationFrame(poll);
      };
      requestAnimationFrame(poll);
    });
    await page.getByRole("button", { name: "Step forward" }).click();
    await page.waitForFunction(() => (window as unknown as { __seen: boolean }).__seen, null, {
      timeout: 3000,
    });
  });
});

test.describe("375 px", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("the transport collapses, the options menu works and the page does not scroll sideways", async ({
    page,
  }) => {
    await openRun(page, BASE, FAILED);
    await enterReplay(page);
    const bar = transport(page);
    await expect(bar.getByRole("button", { name: "Play", exact: true })).toBeVisible();
    await expect(bar.getByRole("button", { name: "Step back" })).toBeVisible();
    await expect(bar.getByRole("button", { name: "Step forward" })).toBeVisible();
    await expect(scrub(page)).toBeVisible();
    // speed and the toggles live in the menu
    await expect(bar.getByRole("group", { name: "Playback speed" })).toHaveCount(0);
    const options = bar.getByRole("button", { name: "Options" });
    await options.click();
    await expect(options).toHaveAttribute("aria-expanded", "true");
    await bar.getByRole("button", { name: "5x" }).click();
    await expect(bar.getByRole("button", { name: "5x" })).toHaveAttribute("aria-pressed", "true");
    await expect(bar.getByRole("checkbox", { name: "Pause at first error" })).toBeVisible();
    // Esc closes the menu only; replay stays
    await page.keyboard.press("Escape");
    await expect(options).toHaveAttribute("aria-expanded", "false");
    await expect(options).toBeFocused();
    await expect(bar).toBeVisible();
    expect(await overflowX(page)).toBeLessThanOrEqual(0);
    // touch targets are at least 44 px
    for (const name of ["Play", "Step back", "Step forward"]) {
      const box = (await bar.getByRole("button", { name, exact: true }).boundingBox())!;
      expect(box.height, name).toBeGreaterThanOrEqual(43.5);
      expect(box.width, name).toBeGreaterThanOrEqual(43.5);
    }
    // the Leave button is in the menu
    await options.click();
    await bar.getByRole("button", { name: "Leave replay" }).click();
    await expect(bar).toHaveCount(0);
    expect(await overflowX(page)).toBeLessThanOrEqual(0);
  });

  test("the Replay button shares the tabs\' row, so replay does not make the page taller", async ({
    page,
  }) => {
    await openRun(page, BASE, FAILED);
    const ctx = (await page.getByRole("button", { name: /^Context/ }).boundingBox())!;
    const rep = (await replayButton(page).boundingBox())!;
    const tab = (await page.getByRole("radio", { name: "Timeline" }).locator("..").boundingBox())!;
    expect(Math.abs(rep.y - ctx.y)).toBeLessThan(4);
    expect(rep.y).toBeLessThan(tab.y + tab.height);
    expect(rep.x + rep.width).toBeLessThanOrEqual(375);
  });

  test("replay does not make the page wider in any view", async ({ page }) => {
    await openRun(page, BASE, FAILED);
    await enterReplay(page);
    await seekTo(page, errIdx);
    expect(await overflowX(page)).toBeLessThanOrEqual(0);
    await page.getByRole("radio", { name: "Waterfall" }).check();
    expect(await overflowX(page)).toBeLessThanOrEqual(0);
    await page.getByRole("radio", { name: "Evidence" }).check();
    expect(await overflowX(page)).toBeLessThanOrEqual(0);
  });
});

test.describe("axe", () => {
  test.use({ viewport: { width: 1440, height: 900 } });
  for (const scheme of ["light", "dark"] as const) {
    test(`the run page in replay mode is clean (${scheme})`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme, reducedMotion: "reduce" });
      await openRun(page, BASE, FAILED);
      await enterReplay(page);
      const check = async (label: string) => {
        await settleAnimations(page);
        const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
        const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
        expect(
          bad.map((v) => `${label} ${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`),
        ).toEqual([]);
      };
      await check("start");
      await seekTo(page, errIdx);
      await check("first error");
      await page.getByRole("option").last().click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await check("inspector");
      await page.keyboard.press("Escape");
      await seekTo(page, failed.events.length - 1);
      await page.getByRole("radio", { name: "Waterfall" }).check();
      await check("waterfall");
    });
  }
});

test.describe("the other runs", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("a clean run replays to its summary and never mentions a failure", async ({ page }) => {
    const ok = successRun();
    await openRun(page, BASE, ok.run.id);
    await enterReplay(page);
    await seekTo(page, ok.events.length - 1);
    await expect(page.getByTestId("headline")).toHaveText(/^Completed · step \d+ · replay$/);
    expect(await figure(page, "Cost")).toBe(
      formatCost(ok.run.summary["estimated_cost_usd"] as number),
    );
    await expect(page.getByTestId("first-error")).toHaveCount(0);
    await expect(page.locator(".tl-fault")).toHaveCount(0);
  });

  test("Show it in replay moves the playhead to the first error", async ({ page }) => {
    await openRun(page, BASE, FAILED);
    await enterReplay(page);
    await seekTo(page, errIdx);
    await seekTo(page, 2);
    await page.getByRole("listbox", { name: /Run timeline/ }).focus();
    await page.keyboard.press("e");
    await expect(page.getByRole("dialog")).toContainText("ConnectionTimeout");
    await expect(scrub(page)).toHaveValue(String(errIdx));
  });

  test("RUN() helper agrees with the fixture ids", () => {
    expect(RUN(2)).toBe(failed.run.id);
  });
});

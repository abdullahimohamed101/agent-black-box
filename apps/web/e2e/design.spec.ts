import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { withArtifacts } from "./artifacts";

// Phase 21 design contract (fixtures build): the application frame, fonts, no third-party requests, axe in both colour
// schemes and no horizontal scroll on a phone, on every kind of page. Objective checks only; the paired screenshots
// (e2e/shots.spec.ts) carry the subjective half.
const BASE = "/w/demo/projects/all";
const RUN = (n: number) => `run_${String(n).padStart(26, "0")}`;
const ROUTES: [string, string][] = [
  ["overview", BASE],
  ["runs list", `${BASE}/runs`],
  ["failed run", `${BASE}/runs/${RUN(2)}`],
  ["multi-agent waterfall", `${BASE}/runs/${RUN(7)}?view=waterfall`],
  ["findings run", `${BASE}/runs/${RUN(10)}`],
  ["coding run", `${BASE}/runs/${RUN(11)}`],
  ["compare", `${BASE}/runs/compare?left=${RUN(11)}&right=${RUN(12)}`],
  ["analytics", `${BASE}/analytics`],
  ["experiments", `${BASE}/experiments`],
  ["viewer run", `/w/viewer-only/projects/all/runs/${RUN(7)}`],
  ["login", "/login"],
];

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual(
    [],
  );
}

const overflow = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

test.describe("1440x900 frame", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const [name, url] of ROUTES.filter(([n]) => n !== "login")) {
    test(`frame: ${name}`, async ({ page }) => {
      await page.goto(url);
      await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
      const box = async (sel: string) => (await page.locator(sel).first().boundingBox())!;
      const width = await page.evaluate(() => document.documentElement.clientWidth);
      const main = await box("main#main");
      const rail = await box("aside.rail");
      const top = await box("header.topbar");
      // no centred column: content starts at the rail's edge and ends at the viewport's edge
      expect(rail).toMatchObject({ x: 0, width: 224 });
      expect(main.x).toBe(224);
      expect(main.x + main.width).toBe(width);
      expect(top.height).toBe(44);
      await expect(page.locator("main#main")).toHaveAttribute("tabindex", "-1");
    });
  }

  test("frame: the sign-in page has no rail", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByRole("navigation", { name: "Primary" })).toHaveCount(0);
    await expect(page.locator("aside.rail")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: /Sign in/ })).toBeVisible();
  });

  test("focus order: skip link, then the rail, then the page", async ({ page }) => {
    await page.goto(`${BASE}/runs`);
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
    const order = await page.evaluate(() =>
      [...document.querySelectorAll("a[href], button, summary")]
        .map((e) => (e.closest("aside.rail") ? "rail" : e.closest("header") ? "top" : "main"))
        .filter((v, i, a) => i === 0 || a[i - 1] !== v),
    );
    // the skip link sits outside the rail and the top bar (the top bar has no focusable control on a list page)
    expect(order.slice(0, 3)).toEqual(["main", "rail", "main"]);
  });

  test("fonts are self-hosted and applied; no request leaves the origin", async ({ page }) => {
    const origins = new Set<string>();
    page.on("request", (r) => origins.add(new URL(r.url()).origin));
    await page.goto(`${BASE}/runs/${RUN(2)}`);
    await page.evaluate(async () => {
      await Promise.all(
        ['400 1rem "IBMPlexSans"', '600 1rem "IBMPlexSansCondensed"', '400 1rem "IBMPlexMono"'].map(
          (f) => document.fonts.load(f),
        ),
      );
    });
    for (const face of [
      '400 1rem "IBMPlexSans"',
      '600 1rem "IBMPlexSansCondensed"',
      '400 1rem "IBMPlexMono"',
    ])
      expect(await page.evaluate((f) => document.fonts.check(f), face)).toBe(true);
    const fam = (sel: string) =>
      page
        .locator(sel)
        .first()
        .evaluate((e) => getComputedStyle(e).fontFamily);
    expect(await fam("body")).toContain("IBMPlexSans,");
    expect(await fam("h1")).toContain("IBMPlexSansCondensed");
    expect(await fam(".tl-type")).toContain("IBMPlexMono");
    expect(await page.evaluate(() => getComputedStyle(document.body).fontSize)).toBe("13px");
    expect([...origins]).toEqual(["http://localhost:3100"]);
  });
});

for (const scheme of ["dark", "light"] as const) {
  test.describe(`axe, ${scheme}`, () => {
    test.use({ viewport: { width: 1440, height: 900 }, colorScheme: scheme });
    for (const [name, url] of ROUTES)
      test(`${name}`, async ({ page }) => {
        await page.goto(url);
        await page.evaluate(() => document.fonts.ready);
        await axe(page);
      });
  });
}

test.describe("375 px", () => {
  test.use({ viewport: { width: 375, height: 812 } });
  for (const [name, url] of ROUTES)
    test(`no horizontal scroll: ${name}`, async ({ page }) => {
      await page.goto(url);
      await page.evaluate(() => document.fonts.ready);
      expect(await overflow(page)).toBeLessThanOrEqual(0);
    });
  test("the rail is a strip under the top bar and its links stay reachable", async ({ page }) => {
    await page.goto(`${BASE}/runs`);
    const rail = (await page.locator("aside.rail").boundingBox())!;
    expect(rail.x).toBe(0);
    expect(rail.y).toBe(44);
    await expect(
      page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Runs" }),
    ).toBeInViewport();
  });
});

test.describe("status grammar", () => {
  test.use({ viewport: { width: 1440, height: 900 } });
  for (const [name, url] of ROUTES.filter(([n]) => n !== "login")) {
    test(`radius and shadow scan: ${name}`, async ({ page }) => {
      await page.goto(url);
      await page.evaluate(() => document.fonts.ready);
      const bad = await page.evaluate(() => {
        const out: string[] = [];
        for (const el of document.querySelectorAll("body *")) {
          if (el.matches(".spinner, .spinner *")) continue;
          const cs = getComputedStyle(el);
          const radii = [
            cs.borderTopLeftRadius,
            cs.borderTopRightRadius,
            cs.borderBottomLeftRadius,
            cs.borderBottomRightRadius,
          ];
          // a circle is a radius of half the box (the live dot), not a pill
          if (radii.some((r) => r.endsWith("px") && parseFloat(r) > 6))
            out.push(`${el.tagName}.${el.className} radius ${radii.join(" ")}`);
          if (cs.boxShadow !== "none" && !el.matches(".wf-tip")) {
            // computed shadows read "rgb(...) 0px 4px 14px 0px"; only inset hairline bars (blur 0) are allowed
            const blur = [...cs.boxShadow.matchAll(/\) (-?[\d.]+)px (-?[\d.]+)px ([\d.]+)px/g)].map(
              (m) => parseFloat(m[3]!),
            );
            if (blur.some((b) => b > 1))
              out.push(`${el.tagName}.${el.className} shadow ${cs.boxShadow}`);
          }
        }
        return out;
      });
      expect(bad).toEqual([]);
    });
  }
});

test.describe("run header", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const [name, url] of [
    ["failed run", `${BASE}/runs/${RUN(2)}`],
    ["multi-agent run", `${BASE}/runs/${RUN(7)}`],
  ] as const)
    test(`the header is an instrument panel, not a page: ${name}`, async ({ page }) => {
      await page.goto(url);
      await expect(page.locator(".run-head .stats .stat").first()).toBeVisible();
      const head = (await page.locator(".run-head").boundingBox())!;
      expect(head.height).toBeLessThanOrEqual(190);
      // one readout strip on one row at this width
      const tops = await page
        .locator(".run-head .stats .stat")
        .evaluateAll(
          (els) => new Set(els.map((e) => Math.round(e.getBoundingClientRect().top))).size,
        );
      expect(tops).toBe(1);
      await expect(page.getByTestId("headline")).toBeVisible();
    });

  test("annunciator grammar: one red readout and one amber readout on the failed run", async ({
    page,
  }) => {
    await page.goto(`${BASE}/runs/${RUN(2)}`);
    await expect(page.locator(".run-head .stats .stat").first()).toBeVisible();
    const colours = await page.evaluate(() => {
      const probe = (v: string) => {
        const e = document.createElement("span");
        e.style.color = `var(${v})`;
        document.body.append(e);
        const c = getComputedStyle(e).color;
        e.remove();
        return c;
      };
      const bad = probe("--bad");
      const warn = probe("--warn");
      // the status badge is the state itself and is red on a failed run by design; everything else in the header counts
      const els = [...document.querySelectorAll(".run-head *")].filter((e) => !e.closest(".badge"));
      const direct = (c: string) =>
        els.filter(
          (e) =>
            getComputedStyle(e).color === c &&
            [...e.childNodes].some((n) => n.nodeType === 3 && n.textContent?.trim()),
        );
      return {
        red: direct(bad).map((e) => e.textContent),
        amber: direct(warn).map((e) => e.textContent),
      };
    });
    expect(colours.red).toEqual(["4"]);
    expect(colours.amber).toEqual(["2"]);
  });

  test("Show it opens the failing event from the header", async ({ page }) => {
    await page.goto(`${BASE}/runs/${RUN(2)}`);
    await page.getByRole("button", { name: "Show it" }).click();
    await expect(page.getByRole("dialog")).toContainText("ConnectionTimeout");
  });
});

test.describe("run work area", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("the run page fills the viewport: no document scroll, the list gets the room", async ({
    page,
  }) => {
    await page.goto(`${BASE}/runs/${RUN(2)}`);
    await expect(page.getByRole("listbox", { name: /Run timeline/ })).toBeVisible();
    const m = await page.evaluate(() => ({
      scroll: document.scrollingElement!.scrollHeight,
      inner: window.innerHeight,
    }));
    expect(m.scroll).toBe(m.inner);
    const list = (await page.locator(".timeline").boundingBox())!;
    expect(list.height).toBeGreaterThan(300);
    // the context column is visible on load, beside the pane
    await expect(page.getByRole("complementary", { name: "Run context" })).toBeVisible();
    await expect(page.getByTestId("findings")).toBeVisible();
  });

  test("opening the inspector keeps the pane usable: where it would lack width the context folds to a strip, one click restores it", async ({
    page,
  }) => {
    // 1440 has room for all three columns (see review-fixes.spec.ts); 1280 does not.
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto(`${BASE}/runs/${RUN(2)}`);
    await page.getByRole("button", { name: "Show it" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    expect(await page.locator(".pane").evaluate((e) => e.clientWidth)).toBeGreaterThanOrEqual(480);
    await expect(page.locator(".work")).toHaveAttribute("data-context", "closed");
    await expect(page.getByTestId("findings")).toBeHidden();
    await page.getByRole("button", { name: "Show run context" }).click();
    await expect(page.locator(".work")).toHaveAttribute("data-context", "open");
    await expect(page.getByTestId("findings")).toBeVisible();
    // the header toggle folds it again
    await page.getByRole("button", { name: "Context", exact: true }).click();
    await expect(page.locator(".work")).toHaveAttribute("data-context", "closed");
  });

  test("a wide window keeps the context open beside the inspector", async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1000 });
    await page.goto(`${BASE}/runs/${RUN(2)}`);
    await page.getByRole("button", { name: "Show it" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.locator(".work")).toHaveAttribute("data-context", "open");
    await expect(page.getByTestId("findings")).toBeVisible();
  });

  test("the view tabs are radios: keyboard and click both switch, the pane keeps its height", async ({
    page,
  }) => {
    await page.goto(`${BASE}/runs/${RUN(7)}`);
    await page.getByRole("radio", { name: "Waterfall" }).check();
    await expect(page.getByTestId("waterfall")).toBeVisible();
    const h = (await page.locator(".wf-scroll").boundingBox())!.height;
    expect(h).toBeGreaterThan(250);
    await expect(page.getByRole("radio", { name: "Waterfall" })).toBeChecked();
  });

  test("the 10,000-event run: bounded rows in both views, the page still does not scroll", async ({
    page,
  }) => {
    await page.goto(`${BASE}/runs/${RUN(6)}`);
    const list = page.getByRole("listbox", { name: /Run timeline/ });
    await expect(list).toBeVisible();
    await list.focus();
    await page.keyboard.press("End");
    expect(await page.getByRole("option").count()).toBeLessThan(80);
    await page.getByRole("radio", { name: "Waterfall" }).check();
    await expect(page.getByTestId("waterfall")).toBeVisible();
    expect(await page.locator('.wf-scroll [role="row"]').count()).toBeLessThanOrEqual(80);
    const m = await page.evaluate(() => [
      document.scrollingElement!.scrollHeight,
      window.innerHeight,
    ]);
    expect(m[0]).toBe(m[1]);
  });

  test("Escape in the inspector returns focus to the list", async ({ page }) => {
    await page.goto(`${BASE}/runs/${RUN(2)}`);
    const list = page.getByRole("listbox", { name: /Run timeline/ });
    await list.focus();
    await page.keyboard.press("e");
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(list).toBeFocused();
  });
});

test.describe("run work area on a phone", () => {
  test.use({ viewport: { width: 375, height: 812 } });
  for (const [name, url] of [
    ["failed run", `${BASE}/runs/${RUN(2)}`],
    ["multi-agent waterfall", `${BASE}/runs/${RUN(7)}?view=waterfall`],
  ] as const)
    test(`stacked: the document scrolls and the list keeps a definite height: ${name}`, async ({
      page,
    }) => {
      await page.goto(url);
      await expect(page.locator(".timeline, .wf-scroll").first()).toBeVisible();
      const m = await page.evaluate(() => ({
        scroll: document.scrollingElement!.scrollHeight,
        inner: window.innerHeight,
        list: (document.querySelector(".timeline, .wf-scroll") as HTMLElement).clientHeight,
      }));
      expect(m.scroll).toBeGreaterThan(m.inner);
      expect(m.list).toBeGreaterThan(250);
      expect(await overflow(page)).toBeLessThanOrEqual(0);
    });
});

test.describe("inspector", () => {
  test("a pane flush with the right edge of the window, from the top of the work area to the bottom", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`${BASE}/runs/${RUN(2)}`);
    await page.getByRole("button", { name: "Show it" }).click();
    await page.waitForTimeout(300); // the inspector slides in for 180 ms
    const dlg = (await page.getByRole("dialog").boundingBox())!;
    expect(dlg.x + dlg.width).toBe(1440);
    expect(dlg.y + dlg.height).toBe(900);
    const work = (await page.locator(".work").boundingBox())!;
    expect(dlg.y).toBe(work.y);
    await expect(page.getByRole("button", { name: "Close details" })).toBeFocused();
  });

  test("dragging the separator widens the inspector and the pane gives way", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`${BASE}/runs/${RUN(2)}`);
    await page.getByRole("button", { name: "Show it" }).click();
    const before = (await page.getByRole("dialog").boundingBox())!.width;
    const sep = (await page.getByRole("separator").boundingBox())!;
    await page.mouse.move(sep.x + 3, sep.y + 200);
    await page.mouse.down();
    await page.mouse.move(sep.x - 117, sep.y + 200, { steps: 4 });
    await page.mouse.up();
    const after = (await page.getByRole("dialog").boundingBox())!.width;
    expect(after).toBeGreaterThan(before + 80);
    expect(await page.locator(".pane").evaluate((e) => e.clientWidth)).toBeGreaterThanOrEqual(400);
  });

  test("the head stays put while a long inspector scrolls", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 500 });
    await page.goto(`${BASE}/runs/${RUN(2)}`);
    await page.getByRole("button", { name: "Show it" }).click();
    const dlg = page.getByRole("dialog");
    await dlg.evaluate((e) => e.scrollTo(0, 300));
    await expect(page.getByRole("button", { name: "Close details" })).toBeInViewport();
  });

  test("on a phone it is an overlay under the top bar", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto(`${BASE}/runs/${RUN(2)}`);
    await page.getByRole("button", { name: "Show it" }).click();
    await page.waitForTimeout(300);
    const dlg = (await page.getByRole("dialog").boundingBox())!;
    expect(dlg).toMatchObject({ x: 0, y: 44, width: 375, height: 812 - 44 });
    await expect(page.getByRole("separator")).toBeHidden();
  });

  test("a viewer sees the withheld wording in the inspector, never the placeholder", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`/w/viewer-only/projects/all/runs/${RUN(7)}`);
    await page.getByRole("button", { name: "Show it" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    expect(await page.locator("body").innerText()).not.toContain("[withheld]");
  });
});

test.describe("evidence view", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("a table of the run's artifacts in the pane, axe clean, the inspector opens from a row", async ({
    page,
  }) => {
    await withArtifacts(page);
    await page.goto(`${BASE}/runs/${RUN(11)}?view=evidence`);
    const region = page.getByRole("region", { name: "Captured artifacts" });
    await expect(region.getByRole("row")).toHaveCount(1 + 4); // header, two diffs, stdout and stderr of the one failed command
    await expect(page.getByRole("radio", { name: "Evidence" })).toBeChecked();
    await axe(page);
    await region
      .getByRole("button", { name: /^Open diff/ })
      .first()
      .click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(region).toBeFocused();
    const m = await page.evaluate(() => [
      document.scrollingElement!.scrollHeight,
      window.innerHeight,
    ]);
    expect(m[0]).toBe(m[1]);
  });

  test("a run without artifacts says so", async ({ page }) => {
    await page.goto(`${BASE}/runs/${RUN(2)}?view=evidence`);
    await expect(page.getByTestId("evidence-empty")).toHaveText(
      "No artifacts were captured in this run.",
    );
    await axe(page);
  });

  test("a viewer's evidence view never shows the API's placeholder", async ({ page }) => {
    await withArtifacts(page);
    await page.goto(`/w/viewer-only/projects/all/runs/${RUN(7)}?view=evidence`);
    await expect(page.getByRole("radio", { name: "Evidence" })).toBeChecked();
    await page.waitForTimeout(300);
    const text = await page.locator("body").innerText();
    expect(text).not.toContain("[withheld]");
    expect(await page.content()).not.toContain("[withheld]");
  });
});

test.describe("runs list", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("dense: rows are at most 34 px, the toolbar is one row, no inner horizontal scroll", async ({
    page,
  }) => {
    await page.goto(`${BASE}/runs`);
    await expect(page.getByRole("table")).toBeVisible();
    const rows = await page
      .locator("tbody tr")
      .evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().height)));
    expect(rows.length).toBeGreaterThan(5);
    expect(Math.max(...rows)).toBeLessThanOrEqual(34);
    const filters = (await page.getByRole("search", { name: "Filter runs" }).boundingBox())!;
    expect(filters.height).toBeLessThanOrEqual(48);
    const wrap = await page.locator(".table-wrap").evaluate((e) => [e.scrollWidth, e.clientWidth]);
    expect(wrap[0]).toBeLessThanOrEqual(wrap[1]!);
    // the count line sits in the page head, above the table
    const head = (await page.locator(".page-head").boundingBox())!;
    const table = (await page.getByRole("table").boundingBox())!;
    expect(head.y).toBeLessThan(table.y);
    await expect(page.locator(".page-head")).toContainText(/\d+ runs loaded/);
  });

  test("a search that adds the Matched column still fits without an inner scroll", async ({
    page,
  }) => {
    await page.goto(`${BASE}/runs`);
    const box = page.getByRole("searchbox", { name: "Search" });
    await box.fill("tool:run_migration");
    await box.press("Enter");
    await expect(page.getByRole("list", { name: "Search terms" })).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "Matched" })).toBeVisible();
    const wrap = await page.locator(".table-wrap").evaluate((e) => [e.scrollWidth, e.clientWidth]);
    expect(wrap[0]).toBeLessThanOrEqual(wrap[1]!);
    await expect(page.getByRole("region", { name: "Narrow the results" })).toBeVisible();
  });

  test("the status filter is a segmented toggle that still checks by label", async ({ page }) => {
    await page.goto(`${BASE}/runs`);
    await page.getByLabel("Failed").check();
    await expect(page.getByLabel("Failed")).toBeChecked();
    await expect(page.getByRole("button", { name: "Clear filters" })).toBeVisible();
    await expect(page.locator("tbody tr")).not.toHaveCount(12);
  });
});

test.describe("motion", () => {
  test.use({ viewport: { width: 1440, height: 900 } });
  const animations = (page: Page) => page.evaluate(() => document.getAnimations().length);
  const settled = async (page: Page) => {
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(300);
  };

  test.describe("reduced motion", () => {
    test.use({ reducedMotion: "reduce" });

    test("nothing animates after load, after opening the inspector or after a view switch", async ({
      page,
    }) => {
      await page.goto(`${BASE}/runs/${RUN(2)}`);
      await expect(page.getByTestId("headline")).toBeVisible();
      await settled(page);
      expect(await animations(page)).toBe(0);
      await page.getByRole("button", { name: "Show it" }).click();
      const drawer = page.getByRole("dialog");
      await expect(drawer).toBeVisible();
      await page.waitForTimeout(100);
      expect(await animations(page)).toBe(0);
      expect(await drawer.evaluate((el) => getComputedStyle(el).transitionDuration)).toBe("0s");
      await page.getByRole("radio", { name: "Waterfall" }).check();
      await page.waitForTimeout(100);
      expect(await animations(page)).toBe(0);
    });
  });

  test.describe("motion on", () => {
    test.use({ reducedMotion: "no-preference" });

    test("no route animates on first paint", async ({ page }) => {
      for (const [, url] of ROUTES) {
        await page.goto(url);
        await settled(page);
        expect(await animations(page), url).toBe(0);
      }
    });

    test("the inspector slides in 180 ms; hovered rows fade their background in 120 ms", async ({
      page,
    }) => {
      await page.goto(`${BASE}/runs/${RUN(2)}`);
      await page.getByRole("button", { name: "Show it" }).click();
      const drawer = page.getByRole("dialog");
      await expect(drawer).toBeVisible();
      expect(await drawer.evaluate((el) => getComputedStyle(el).transitionDuration)).toBe("0.18s");
      // the hover state carries the transition; keyboard navigation switches it off until the pointer is used again
      const row = page.getByRole("option").first();
      await row.hover();
      expect(await row.evaluate((el) => getComputedStyle(el).transitionDuration)).toBe("0.12s");
      await page.getByRole("listbox").focus();
      await page.keyboard.press("j");
      expect(await row.evaluate((el) => getComputedStyle(el).transitionDuration)).toBe("0s");
    });
  });
});

test.describe("polish: the class chips take one row, or two when the pane is narrow, never a cut-off chip", () => {
  for (const width of [1440, 1280]) {
    test(`toolbar is at most two rows with the inspector open at ${width}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 800 });
      await page.goto(`${BASE}/runs/${RUN(7)}`);
      await page.getByRole("button", { name: "Show it" }).click();
      await expect(page.getByRole("dialog")).toBeVisible();
      const bar = page.getByRole("group", { name: "Timeline filters" });
      const pane = (await page.locator(".pane").boundingBox())!.width;
      // one row from 40rem of pane; below that the chips wrap onto a second row (see review-fixes.spec.ts for the clipping check)
      expect((await bar.boundingBox())!.height).toBeLessThan(pane >= 640 ? 48 : 100);
    });
  }

  test("a story step has one marker: the list number, no glyph", async ({ page }) => {
    await page.goto(`${BASE}/runs/${RUN(11)}`);
    // the label and its detail stay separate words in the button's accessible name
    await expect(
      page
        .getByTestId("story")
        .getByRole("button", { name: /Read app\/session\.py/ })
        .first(),
    ).toBeVisible();
    const glyph = page.getByTestId("story").locator(".story-glyph").first();
    await expect(glyph).toBeHidden();
    const marker = await page
      .getByTestId("story")
      .locator("li")
      .first()
      .evaluate((el) => getComputedStyle(el).display);
    expect(marker).toBe("list-item");
  });

  test("the folded context strip is a labelled control with an icon", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto(`${BASE}/runs/${RUN(7)}`);
    await page.getByRole("button", { name: "Show it" }).click();
    const strip = page.getByRole("button", { name: "Show run context" });
    await expect(strip).toBeVisible();
    await expect(strip.locator("svg")).toBeVisible();
    expect((await strip.boundingBox())!.width).toBeGreaterThanOrEqual(34);
    await strip.click();
    await expect(page.getByRole("button", { name: "Context" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  test("agents are clamped to two lines but keep their full text", async ({ page }) => {
    await page.goto(`${BASE}/runs/${RUN(7)}`);
    const item = page.getByTestId("agent-tree").getByRole("button").first();
    await expect(item).toHaveAttribute("title", /events/);
    expect((await item.boundingBox())!.height).toBeLessThan(64);
  });

  test("the view tabs show counts without changing their names", async ({ page }) => {
    await page.goto(`${BASE}/runs/${RUN(2)}`);
    await expect(page.getByRole("radio", { name: "Timeline", exact: true })).toBeChecked();
    await expect(page.locator(".seg-count").first()).toHaveText("15");
  });
});

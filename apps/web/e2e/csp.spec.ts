import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { settleAnimations } from "./motion";

// Phase 19a step 4 (fixtures mode, ADR-155): the strict Content-Security-Policy breaks nothing. Every page of the
// app is opened at desktop and phone width with a violation listener installed before any page script runs; there
// must be zero violations, zero console errors, fonts and styles applied, and axe still clean. The route list is
// derived from src/app (every directory with a page.tsx), so a new page is covered the day it is added.

const APP = fileURLToPath(new URL("../src/app", import.meta.url));
const RUN = `run_${"2".padStart(26, "0")}`;
const EXPERIMENT = `exp_${"1".padStart(26, "0")}`;
const PARAMS: Record<string, string> = {
  workspace: "demo",
  project: "all",
  runId: RUN,
  experimentId: EXPERIMENT,
};

function pageDirectories(dir: string, parts: string[] = []): string[][] {
  const found: string[][] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) found.push(...pageDirectories(full, [...parts, name]));
    else if (name === "page.tsx") found.push(parts);
  }
  return found;
}

/** `/w/[workspace]/projects/[project]/runs/[runId]` becomes a concrete fixture URL; an unknown parameter fails loudly. */
function routes(): string[] {
  return pageDirectories(APP)
    .map((parts) => {
      // A route group such as `(review)` shares a layout and adds no path segment.
      const segments = parts
        .filter((part) => !/^\(.+\)$/.test(part))
        .map((part) => {
          const param = /^\[(\w+)\]$/.exec(part);
          if (!param) return part;
          const value = PARAMS[param[1]!];
          if (!value)
            throw new Error(`csp.spec.ts: give the route parameter [${param[1]}] a fixture value`);
          return value;
        });
      return `/${segments.join("/")}`;
    })
    .sort();
}

const MISSING = "/this-page-does-not-exist";
const ALL = [...routes(), MISSING];

declare global {
  interface Window {
    __violations?: string[];
  }
}

/** Under a loaded machine a page can still be replacing itself (a client redirect) when we look: retry once it settles. */
async function retryAfterNavigation<T>(page: Page, run: () => Promise<T>): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    try {
      return await run();
    } catch (error) {
      if (attempt >= 3 || !/context was destroyed|navigation/i.test(String(error))) throw error;
      await page.waitForLoadState("networkidle");
    }
  }
}

async function watch(page: Page) {
  const problems: string[] = [];
  page.on("console", (m) => m.type() === "error" && problems.push(`console: ${m.text()}`));
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("requestfailed", (r) => {
    // Next cancels its own in-flight link prefetches (`?_rsc=`) when the page settles: not a failure.
    if (r.url().includes("_rsc=") && r.failure()?.errorText === "net::ERR_ABORTED") return;
    problems.push(`request failed: ${r.url()} ${r.failure()?.errorText}`);
  });
  // Before any page script: record every violation the browser reports, in every frame.
  await page.addInitScript(() => {
    window.__violations = [];
    document.addEventListener("securitypolicyviolation", (e) => {
      window.__violations!.push(
        `${e.violatedDirective} blocked=${e.blockedURI || "inline"} source=${e.sourceFile}:${e.lineNumber}`,
      );
    });
  });
  return problems;
}

test("the route list is derived from the app and is not empty", () => {
  expect(routes().length).toBeGreaterThanOrEqual(19);
  expect(routes()).toContain(`/w/demo/projects/all/runs/${RUN}`);
});

for (const [width, height] of [
  [1440, 900],
  [375, 812],
] as const)
  test.describe(`${width} px`, () => {
    test.use({ viewport: { width, height } });

    for (const route of ALL) {
      test(`${route}: no policy violations, fonts and styles applied, axe clean`, async ({
        page,
      }) => {
        const problems = await watch(page);
        const response = await page.goto(route);
        expect(response).not.toBeNull();
        await page.waitForLoadState("networkidle");
        await settleAnimations(page);

        const headers = response!.headers();
        const csp = headers["content-security-policy"] ?? "";
        const directive = (name: string) =>
          csp.split("; ").find((d) => d.startsWith(`${name} `)) ?? "";
        expect(directive("script-src")).toMatch(/'nonce-[^']+' 'strict-dynamic'/);
        expect(directive("script-src")).not.toContain("unsafe-inline");
        expect(directive("script-src")).not.toContain("unsafe-eval");
        expect(directive("style-src")).not.toContain("unsafe-inline");
        expect(directive("font-src")).toBe("font-src 'self'");
        expect(directive("connect-src")).toBe("connect-src 'self'");
        expect(csp).toContain("frame-ancestors 'none'");
        expect(csp).toContain("object-src 'none'");
        expect(csp).toContain("base-uri 'none'");
        expect(csp).toContain("form-action 'self'");
        expect(headers["x-content-type-options"]).toBe("nosniff");
        expect(headers["x-frame-options"]).toBe("DENY");
        expect(headers["referrer-policy"]).toBe("no-referrer");
        expect(headers["permissions-policy"]).toContain("camera=()");
        expect(headers["cross-origin-opener-policy"]).toBe("same-origin");
        expect(headers["cross-origin-resource-policy"]).toBe("same-origin");
        expect(headers["strict-transport-security"]).toBeUndefined(); // http: no HTTPS claim

        const state = await retryAfterNavigation(page, () =>
          page.evaluate(async () => {
            await document.fonts.ready;
            const inline = [...document.scripts].filter((s) => !s.src);
            return {
              violations: window.__violations ?? [],
              inlineScripts: inline.length,
              unnoncedInline: inline.filter((s) => !s.nonce).length,
              fontsLoaded: [...document.fonts].filter((f) => f.status === "loaded").length,
              // The stylesheet applied: the body is not the browser default (Times, margin 8px, white).
              bodyFont: getComputedStyle(document.body).fontFamily,
              bodyMargin: getComputedStyle(document.body).marginTop,
            };
          }),
        );
        expect(state.violations).toEqual([]);
        expect(state.inlineScripts).toBeGreaterThan(0); // Next's bootstrap: present, and every one carries the nonce
        expect(state.unnoncedInline).toBe(0);
        expect(state.fontsLoaded).toBeGreaterThan(0);
        expect(state.bodyFont).not.toMatch(/^"?Times/);
        expect(state.bodyMargin).toBe("0px");

        const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
        const bad = axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
        expect(
          bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`),
        ).toEqual([]);
        // The missing page is itself a 404: the browser logs that one line for the document.
        const expected404 = route === MISSING ? /status of 404/ : null;
        expect(problems.filter((p) => !expected404?.test(p))).toEqual([]);
      });
    }
  });

test("the policy is enforced: an injected inline handler does not run and is reported", async ({
  page,
}) => {
  await watch(page);
  await page.goto("/login");
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => {
    document.body.insertAdjacentHTML(
      "beforeend",
      `<img src="/icon.svg" onerror="window.__pwned = 1">`,
    );
    document.body.insertAdjacentHTML(
      "beforeend",
      `<img src="/nope.png" onerror="window.__pwned = 2">`,
    );
  });
  await page.waitForTimeout(300);
  const state = await page.evaluate(() => ({
    pwned: (window as unknown as { __pwned?: number }).__pwned,
    violations: window.__violations ?? [],
  }));
  expect(state.pwned).toBeUndefined();
  expect(state.violations.some((v) => v.startsWith("script-src"))).toBe(true);
});

test("each response gets its own nonce", async ({ page }) => {
  const nonces = new Set<string>();
  for (let i = 0; i < 3; i++) {
    const response = await page.goto("/login");
    const match = /'nonce-([^']+)'/.exec(response!.headers()["content-security-policy"] ?? "");
    expect(match).not.toBeNull();
    nonces.add(match![1]!);
  }
  expect(nonces.size).toBe(3);
});

test("the relay and health routes carry the deny-everything policy", async ({ request }) => {
  const health = await request.get("/api/health");
  expect(health.headers()["content-security-policy"]).toBe(
    "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
  );
  expect(health.headers()["x-content-type-options"]).toBe("nosniff");
  const relay = await request.get("/api/abb/v1/me");
  expect(relay.headers()["cache-control"]).toBe("no-store");
  expect(relay.headers()["content-security-policy"]).toContain("default-src 'none'");
});

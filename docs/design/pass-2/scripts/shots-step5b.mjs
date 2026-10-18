// One-off script of design pass 2, kept as a record. It imports @playwright/test, so copy it into apps/web/scripts to run it (paths below are relative to apps/web).
// Design pass 2 step 5b: screenshots of the run page and Compare in every state, light and dark, 1440, 768 and 375.
// Usage: node scripts/shots-step5b.mjs [baseUrl] [nameFilter]. Needs a running fixtures server (port 3110 by default).
import { chromium } from "@playwright/test";

const base = process.argv[2] ?? "http://localhost:3110";
const only = process.argv[3];
const RUN = (n) => `run_${String(n).padStart(26, "0")}`;
const OUT = "../../docs/screenshots/pass-2";
const W = "/w/demo/projects/all";
const run = (n, q = "") => `${W}/runs/${RUN(n)}${q}`;
/** Fixtures have no timed-out run: dress the failed run as one (the API answers are rewritten, nothing else changes). */
const timedOut = (page) =>
  page.route(`**/api/abb/v1/runs/${RUN(2)}`, async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    await route.fulfill({ json: { ...body, status: "TIMED_OUT" } });
  });
/** The fixtures have no artifact store: give the coding run a diff per edit and stdout for a failed command (as e2e/artifacts.ts). */
const withArtifacts = (page) =>
  page.route(/\/v1\/runs\/[^/]+\/events(\?|$)/, async (route) => {
    const res = await route.fetch();
    if (!res.ok()) return route.fulfill({ response: res });
    const body = await res.json();
    let k = 0;
    const art = (n) => `artifact://art_${String(n).padStart(26, "0")}`;
    for (const e of body.items ?? []) {
      if (e.event_type === "file.modified") e.attributes["diff.artifact"] = art(++k);
      if (e.event_type === "shell.command.failed") {
        e.attributes["shell.stdout_artifact"] = art(++k);
        e.attributes["shell.stdout_bytes"] = 2048;
        e.attributes["shell.stderr_artifact"] = art(++k);
        e.attributes["shell.stderr_bytes"] = 311;
      }
    }
    return route.fulfill({ response: res, json: body });
  });
const states = [
  ["failed", run(2), null, null],
  ["failed-coding", run(12), null, null],
  ["failed-waterfall", run(2, "?view=waterfall"), null, null],
  ["failed-evidence", run(12, "?view=evidence"), withArtifacts, null],
  ["failed-findings", run(10, "?view=findings"), null, null],
  ["failed-evaluations", run(12, "?view=evaluations"), null, null],
  [
    "failed-inspector",
    run(2),
    null,
    async (p) => p.getByRole("button", { name: "Go to first error" }).click(),
    [1440, 375],
  ],
  [
    "failed-replay",
    run(2),
    null,
    async (p) => {
      await p.getByRole("button", { name: "Replay", exact: true }).click();
      await p.getByRole("slider", { name: "Replay position" }).fill("6");
    },
  ],
  [
    "failed-details",
    run(2),
    null,
    async (p) => p.getByRole("button", { name: /^Details/ }).click(),
  ],
  ["blocked", run(13), null, null],
  ["timed-out", run(2), timedOut, null],
  ["running", run(4), null, null],
  ["waiting", run(5), null, null],
  ["succeeded", run(11), null, null],
  ["succeeded-clean", run(1), null, null],
  ["expired", run(90), null, null],
  ["deleting", run(91), null, null],
  ["not-found", run(999), null, null, [1440, 375]],
  ["withheld", `/w/viewer-only/projects/all/runs/${RUN(11)}`, null, null, [1440, 375]],
  ["compare", `${W}/runs/compare?left=${RUN(11)}&right=${RUN(12)}`, null, null],
  ["compare-identical", `${W}/runs/compare?left=${RUN(11)}&right=${RUN(11)}`, null, null],
  ["compare-pick", `${W}/runs/compare`, null, null, [1440, 375]],
];
const browser = await chromium.launch({ channel: "chrome" });
for (const scheme of ["light", "dark"]) {
  for (const [w, h] of [
    [1440, 900],
    [768, 1024],
    [375, 812],
  ]) {
    for (const [name, url, setup, act, widths] of states) {
      if (only && !name.includes(only)) continue;
      if (
        widths
          ? !widths.includes(w)
          : w === 768 && !["failed", "compare", "failed-waterfall", "running"].includes(name)
      )
        continue;
      const ctx = await browser.newContext({
        viewport: { width: w, height: h },
        colorScheme: scheme,
        reducedMotion: "reduce",
      });
      const page = await ctx.newPage();
      if (setup) await setup(page);
      await page.goto(base + url);
      await page.waitForLoadState("networkidle", { timeout: 8000 }).catch(() => {});
      await page.waitForTimeout(400);
      if (act) {
        await act(page);
        await page.waitForTimeout(300);
      }
      await page.screenshot({ path: `${OUT}/step5b-${name}-${w}-${scheme}.png` });
      await ctx.close();
    }
  }
}
await browser.close();

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { settleAnimations } from "./motion";
import { EXPANSIONS, layoutProblems, pseudoLocalise } from "./pseudo-localise";

// Design pass 2 step 6 (fixtures build): the length-expansion stress test. Every in-shell screen, at 1440, 768 and 375, in both colour
// schemes, is loaded under +30%, +50%, +70% and +70% with unbroken names (e2e/pseudo-localise.ts) and checked for sideways page
// scroll, text outside the viewport or cut off, overlapping text and controls, and a broken top bar. The heaviest expansion is also
// checked with axe, and the console must stay clean. A failure names the expansion at which a screen first broke.
const RUN = (n: number) => `run_${String(n).padStart(26, "0")}`;
const APR = (n: number) => `apr_${String(n).padStart(26, "0")}`;
const P = "/w/demo/projects/all";
const S = "/w/demo/settings";
const SCREENS: [string, string][] = [
  ["Runs", `${P}/runs`],
  ["Runs, filtered to failures", `${P}/runs?status=failed`],
  ["Run detail, failed", `${P}/runs/${RUN(2)}`],
  ["Run detail, a finished run", `${P}/runs/${RUN(1)}`],
  ["Compare", `${P}/runs/compare?left=${RUN(11)}&right=${RUN(12)}`],
  ["Review, Approvals", "/w/demo/approvals"],
  ["Review, an open approval", `/w/demo/approvals?approval=${APR(3)}`],
  ["Review, Security findings", "/w/demo/security"],
  ...(
    [
      "members",
      "sessions",
      "api-keys",
      "policies",
      "security",
      "quotas",
      "retention",
      "pricing",
      "alerts",
      "audit",
    ] as const
  ).map((s): [string, string] => [`Settings, ${s}`, `${S}/${s}`]),
  ["Alerts, History", `${S}/alerts?tab=history`],
  ["Alerts, Destinations", `${S}/alerts?tab=destinations`],
  ["Analytics, Cost", `${P}/analytics`],
  ["Analytics, Reliability", `${P}/analytics?tab=reliability`],
  ["Analytics, Performance", `${P}/analytics?tab=performance`],
  ["Experiments", `${P}/experiments`],
  ["An experiment, controlled", `${P}/experiments/exp_${"1".padStart(26, "0")}`],
  ["An experiment, nothing to compare", `${P}/experiments/exp_${"2".padStart(26, "0")}`],
  ["Sign-in", "/login"],
];
const SIZES = [
  [1440, 900],
  [768, 1024],
  [375, 812],
] as const;

async function axeViolations(page: Page) {
  await settleAnimations(page);
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  return r.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `axe ${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
}

for (const [width, height] of SIZES)
  for (const scheme of ["light", "dark"] as const)
    test.describe(`Length expansion at ${width} px, ${scheme}`, () => {
      test.use({ viewport: { width, height }, colorScheme: scheme, reducedMotion: "reduce" });
      test.describe.configure({ timeout: 120_000 });

      for (const [label, url] of SCREENS)
        test(label, async ({ page }) => {
          const console_: string[] = [];
          page.on("console", (m) => m.type() === "error" && console_.push(m.text()));
          page.on("pageerror", (e) => console_.push(e.message));
          const failures: string[] = [];
          for (const [i, e] of EXPANSIONS.entries()) {
            await page.goto(url);
            await pseudoLocalise(page, e);
            for (const p of await layoutProblems(page)) failures.push(`${e.label}: ${p}`);
            if (i === EXPANSIONS.length - 1)
              for (const p of await axeViolations(page)) failures.push(`${e.label}: ${p}`);
          }
          expect(console_.filter((m) => !/favicon/.test(m))).toEqual([]);
          expect(failures).toEqual([]);
        });
    });

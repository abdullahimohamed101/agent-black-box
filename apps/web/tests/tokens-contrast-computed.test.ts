import { describe, expect, it } from "vitest";
import { computeContrast, loadTokens } from "../scripts/tokens-lib.mjs";
import { contrast, themes } from "./tokenCss";

// Design pass 2 (ADR-166): the contrast table in docs/design/pass-2/04-colour-type-tokens.md is COMPUTED from tokens.json by
// scripts/tokens-report.mjs, and this test fails when any listed pair drops below its target in either theme.
// Targets: 7:1 (AAA) for body text on the page, raised and selected surfaces; 4.5:1 (AA) for other text; 3:1 for graphics and
// UI boundaries. Decorative rules carry an explicit lower floor in tokens.json.
const pairs = computeContrast(loadTokens()) as {
  fg: string;
  bg: string;
  min: number;
  ratio: { light: number; dark: number };
  kind: string;
  target: string;
}[];

describe("computed contrast from tokens.json", () => {
  it("lists at least the pairs the direction names plus the common app pairs", () => {
    expect(pairs.length).toBeGreaterThanOrEqual(20);
  });
  for (const theme of ["light", "dark"] as const)
    for (const p of pairs)
      it(`${theme}: ${p.fg} on ${p.bg} is at least ${p.min}:1`, () => {
        expect(p.ratio[theme]).toBeGreaterThanOrEqual(p.min);
      });
  it("body text (text.primary) is AAA on every ground it is drawn on", () => {
    const body = pairs.filter((p) => p.fg === "color.text.primary" && p.target === "AAA");
    expect(body.length).toBeGreaterThanOrEqual(3);
    for (const p of body) expect(Math.min(p.ratio.light, p.ratio.dark)).toBeGreaterThanOrEqual(7);
  });
  it("the CSS the app loads resolves to the same colours the table was computed from", () => {
    const t = themes();
    const tokens = loadTokens();
    for (const theme of ["light", "dark"] as const) {
      for (const [css, token] of [
        ["color-text-primary", "color.text.primary"],
        ["color-text-secondary", "color.text.secondary"],
        ["color-bg-canvas", "color.bg.canvas"],
        ["color-bg-raised", "color.bg.raised"],
        ["color-status-fault", "color.status.fault"],
        ["color-status-caution", "color.status.caution"],
        ["color-interactive-default", "color.interactive.default"],
        ["color-bg-wash", "color.bg.wash"],
      ] as const) {
        const want = computeContrast({
          ...tokens,
          $contrast: [{ fg: token, bg: "color.bg.canvas" }],
        })[0]!.ratio[theme];
        expect(
          contrast(t[theme]![css]!, t[theme]!["color-bg-canvas"]!),
          `${css} (${theme})`,
        ).toBeCloseTo(want, 6);
      }
    }
  });
});

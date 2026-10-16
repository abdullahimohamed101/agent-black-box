import { describe, expect, it } from "vitest";
import { contrast, themes as themeVars } from "./tokenCss";

// Phase 22 palette (ADR-111), values moved by design pass 2 (ADR-166): every text/background pair the new semantic tokens are drawn on is at least 4.5:1 in both
// themes. tokens.test.ts covers the legacy names; this file covers the semantic names and the filled FAILED status.
const themes = themeVars();

// Design pass 2 (ADR-166): canvas is oklch(97.8% 0.003 250) / oklch(19% 0.012 255) and the raised surface is white / oklch(24% 0.012 255).
// The pairs are the generated hex values; the old plan values (#eef1f4/#ffffff, #12161b/#1a2027) are replaced on purpose.
const PLAN: Record<string, [string, string]> = {
  light: ["#f6f8fa", "#ffffff"],
  dark: ["#101419", "#1b2025"],
};

describe.each(Object.keys(themes))("%s palette", (name) => {
  const t = themes[name]!;
  it("matches the plan's base colours", () => {
    expect([t.bg, t.surface]).toEqual(PLAN[name]);
  });
  // Step 5b deleted surface-2, ok and the three tint backgrounds (fail-bg, wait-bg, accent-bg) with their pairs and the filled
  // FAILED chip's pair: no screen draws on them any more. The semantic pairs are in tokens-contrast-computed.test.ts.
  for (const fg of ["ink", "muted", "accent", "fail", "wait"])
    for (const bg of ["bg", "surface", "sel"])
      it(`${fg} on ${bg} is at least 4.5:1`, () => {
        expect(contrast(t[fg]!, t[bg]!)).toBeGreaterThanOrEqual(4.5);
      });
  it("the aliases nothing reads any more are gone", () => {
    for (const gone of [
      "bad",
      "bad-bg",
      "warn",
      "warn-bg",
      "fg",
      "fg-2",
      "info",
      "surface-2",
      "ok",
      "ok-bg",
      "fail-bg",
      "wait-bg",
      "accent-bg",
      "cond",
      "serif",
    ])
      expect(t[gone], gone).toBeUndefined();
  });
});

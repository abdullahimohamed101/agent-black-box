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
  for (const fg of ["ink", "muted", "accent", "fail", "wait", "ok"])
    for (const bg of ["bg", "surface", "surface-2", "sel"])
      it(`${fg} on ${bg} is at least 4.5:1`, () => {
        expect(contrast(t[fg]!, t[bg]!)).toBeGreaterThanOrEqual(4.5);
      });
  for (const [fg, bg] of [
    ["fail", "fail-bg"],
    ["wait", "wait-bg"],
    ["accent", "accent-bg"],
    ["ink", "fail-bg"],
    ["ink", "wait-bg"],
    ["ink", "accent-bg"],
    ["muted", "fail-bg"],
    ["muted", "wait-bg"],
  ] as const)
    it(`${fg} on ${bg} is at least 4.5:1`, () => {
      expect(contrast(t[fg]!, t[bg]!)).toBeGreaterThanOrEqual(4.5);
    });
  it("the filled FAILED status (surface on the fail fill) is at least 4.5:1", () => {
    expect(contrast(t.surface!, t.fail!)).toBeGreaterThanOrEqual(4.5);
  });
  it("old token names alias the semantic tokens", () => {
    expect(t.bad).toBe(t.fail);
    expect(t["bad-bg"]).toBe(t["fail-bg"]);
    expect(t.warn).toBe(t.wait);
    expect(t["warn-bg"]).toBe(t["wait-bg"]);
    expect(t.fg).toBe(t.ink);
    expect(t.info).toBe(t.accent);
  });
});

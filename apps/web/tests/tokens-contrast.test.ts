import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

// Phase 22 palette (ADR-111): every text/background pair the new semantic tokens are drawn on is at least 4.5:1 in both
// themes. tokens.test.ts covers the legacy names; this file covers the semantic names and the filled FAILED status.
const css = readFileSync(path.join(__dirname, "../src/app/globals.css"), "utf8");

function block(src: string): Record<string, string> {
  const start = src.indexOf(":root {");
  const body = src.slice(start, src.indexOf("\n}", start));
  const out: Record<string, string> = {};
  for (const m of body.matchAll(/--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6}|var\(--[a-z0-9-]+\))\s*;/g))
    out[m[1]!] = m[2]!;
  return out;
}
function resolve(raw: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const k of Object.keys(raw)) {
    let v = raw[k]!;
    for (let i = 0; i < 5 && v.startsWith("var("); i++) v = raw[v.slice(6, -1)] ?? v;
    out[k] = v;
  }
  return out;
}
const darkAt = css.indexOf("@media (prefers-color-scheme: dark)");
const light = block(css);
const themes: Record<string, Record<string, string>> = {
  light: resolve(light),
  dark: resolve({ ...light, ...block(css.slice(darkAt)) }),
};

function lum(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function contrast(a: string, b: string): number {
  const [x, y] = [lum(a), lum(b)];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

const PLAN: Record<string, [string, string]> = {
  light: ["#eef1f4", "#ffffff"],
  dark: ["#12161b", "#1a2027"],
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

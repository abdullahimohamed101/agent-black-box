import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

// D16: contrast is a test, not a scratch script. Parses the :root blocks of globals.css (light, then the dark media block)
// and asserts every text role on the backgrounds it is drawn on is at least 4.5:1 (WCAG AA for body text).
const css = readFileSync(path.join(__dirname, "../src/app/globals.css"), "utf8");

function vars(block: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of block.matchAll(/--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)) out[m[1]!] = m[2]!;
  return out;
}
function rootBlock(src: string): string {
  const start = src.indexOf(":root {");
  const end = src.indexOf("\n}", start);
  return src.slice(start, end);
}
const dark = css.indexOf("@media (prefers-color-scheme: dark)");
const light = vars(rootBlock(css));
const darkOnly = vars(rootBlock(css.slice(dark)));
const schemes: Record<string, Record<string, string>> = {
  light,
  dark: { ...light, ...darkOnly },
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

const text = ["fg", "fg-2", "muted", "accent", "ok", "warn", "bad"];
const grounds = ["bg", "surface", "surface-2"];
const tints: [string, string][] = [
  ["ok", "ok-bg"],
  ["warn", "warn-bg"],
  ["bad", "bad-bg"],
  ["accent", "accent-bg"],
  ["fg", "ok-bg"],
  ["fg", "warn-bg"],
  ["fg", "bad-bg"],
  ["muted", "warn-bg"],
  ["muted", "bad-bg"],
];

describe.each(Object.keys(schemes))("%s scheme tokens", (name) => {
  const t = schemes[name]!;
  it("defines every token the other stylesheets read", () => {
    for (const k of [
      ...text,
      ...grounds,
      "sel",
      "border",
      "ok-bg",
      "warn-bg",
      "bad-bg",
      "accent-bg",
      "mark",
    ])
      expect(t[k], k).toMatch(/^#[0-9a-f]{6}$/i);
  });
  for (const fg of text)
    for (const bg of grounds)
      it(`${fg} on ${bg} is at least 4.5:1`, () => {
        expect(contrast(t[fg]!, t[bg]!)).toBeGreaterThanOrEqual(4.5);
      });
  for (const fg of ["fg", "fg-2", "muted", "accent", "bad", "warn", "ok"])
    it(`${fg} on the selection colour is at least 4.5:1`, () => {
      expect(contrast(t[fg]!, t.sel!)).toBeGreaterThanOrEqual(4.5);
    });
  for (const [fg, bg] of tints)
    it(`${fg} on ${bg} is at least 4.5:1`, () => {
      expect(contrast(t[fg]!, t[bg]!)).toBeGreaterThanOrEqual(4.5);
    });
  it("button text (bg) on the accent fill is at least 4.5:1", () => {
    expect(contrast(t.bg!, t.accent!)).toBeGreaterThanOrEqual(4.5);
  });
});

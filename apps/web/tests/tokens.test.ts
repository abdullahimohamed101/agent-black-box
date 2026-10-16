import { describe, expect, it } from "vitest";
import { contrast, themes } from "./tokenCss";

// D16: contrast is a test, not a scratch script. Reads the custom properties through tests/tokenCss.ts (generated tokens, then
// the alias layer, then globals.css; design pass 2 moved the values out of globals.css) and asserts every text role on the backgrounds it is drawn on is at least 4.5:1 (WCAG AA for body text).
const schemes = themes();

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

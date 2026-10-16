import { describe, expect, it } from "vitest";
import { contrast, themes } from "./tokenCss";

// D16: contrast is a test, not a scratch script. Reads the custom properties through tests/tokenCss.ts (generated tokens, then
// the alias layer, then globals.css; design pass 2 moved the values out of globals.css) and asserts every text role on the backgrounds it is drawn on is at least 4.5:1 (WCAG AA for body text).
const schemes = themes();

// Step 5b deleted the aliases nothing reads (fg, fg-2, ok, warn, bad, info, surface-2 and the four tints), with their pairs: the
// roles that remain are ink, muted, accent, wait (caution) and fail, on the page, the raised surface and the selection.
const text = ["ink", "muted", "accent", "wait", "fail"];
const grounds = ["bg", "surface"];

describe.each(Object.keys(schemes))("%s scheme tokens", (name) => {
  const t = schemes[name]!;
  it("defines every token the other stylesheets read", () => {
    for (const k of [...text, ...grounds, "sel", "border", "mark"])
      expect(t[k], k).toMatch(/^#[0-9a-f]{6}$/i);
  });
  for (const fg of text)
    for (const bg of grounds)
      it(`${fg} on ${bg} is at least 4.5:1`, () => {
        expect(contrast(t[fg]!, t[bg]!)).toBeGreaterThanOrEqual(4.5);
      });
  for (const fg of text)
    it(`${fg} on the selection colour is at least 4.5:1`, () => {
      expect(contrast(t[fg]!, t.sel!)).toBeGreaterThanOrEqual(4.5);
    });
  it("button text (bg) on the accent fill is at least 4.5:1", () => {
    expect(contrast(t.bg!, t.accent!)).toBeGreaterThanOrEqual(4.5);
  });
});

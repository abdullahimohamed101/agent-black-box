import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { contrast, themes as themeVars } from "./tokenCss";

// Phase 14 stylesheet (policy.css): no red, no motion, and every text/background pair it draws is at least 4.5:1 in both
// themes. tokens-use.test.ts and motion-contract.test.ts already read every file in src/app; this pins the specifics.
const read = (f: string) => readFileSync(path.join(__dirname, "../src/app", f), "utf8");
const policy = read("policy.css").replace(/\/\*[\s\S]*?\*\//g, ""); // comments name the rules they follow
const themes = themeVars() as Record<"light" | "dark", Record<string, string>>;
describe("policy.css", () => {
  it("never reads the failure colour (a denial is a policy outcome, not a failed run)", () => {
    expect(policy).not.toMatch(/--(fail|bad)\b/);
  });
  it("adds no animation or transition", () => {
    expect(policy).not.toMatch(/\b(animation|transition|@keyframes)\b/);
  });
  it("draws the removed diff lines in amber and the added ones in ink", () => {
    expect(policy).toMatch(/\.pol-diff \.pol-del \{[^}]*color: var\(--wait\)[^}]*\}/);
    // no amber tint behind a removed line any more (ADR-166: no fills; the --wait-bg alias is gone)
    expect(policy).not.toMatch(/\.pol-diff \.pol-del \{[^}]*background/);
    expect(policy).toMatch(/\.pol-diff \.pol-add \{[^}]*color: var\(--ink\)[^}]*font-weight: 700/);
  });
  it("uses only tokens for colour, never a raw hex", () => {
    expect(policy).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });
  it("keeps the phone layout: tables fold to cards and the detail replaces the list", () => {
    expect(policy).toMatch(/\.apr-body\[data-detail\] \.apr-list \{\s*display: none/);
    expect(policy).toMatch(/\.apr-table thead \{\s*position: absolute/);
  });

  describe.each(Object.keys(themes) as (keyof typeof themes)[])("%s theme contrast", (name) => {
    const t = themes[name];
    const pairs: [string, string, string][] = [
      ["ink", "surface", "outcome words, the rail count, the guarantee note"],
      ["muted", "surface", "muted outcome marks, line numbers and diff headers"],
      ["ink", "bg", "added diff lines, the read-only viewer"],
      ["wait", "surface", "waiting glyphs and removed diff lines"],
      ["wait", "bg", "removed diff lines on the page"],
    ];
    for (const [fg, bg, what] of pairs)
      it(`${fg} on ${bg} is at least 4.5:1 (${what})`, () => {
        expect(contrast(t[fg]!, t[bg]!)).toBeGreaterThanOrEqual(4.5);
      });
  });
});

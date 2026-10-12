import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

// ADR-111: red means failure and nothing else. Any rule that reads --fail, --bad or their -bg tints must be on this allowlist
// of failure selectors; a new use needs a new entry here, which is the review point.
const FILES = ["globals.css", "compare.css", "waterfall.css", "search.css"];
const ALLOWED: RegExp[] = [
  /^\.stat\[data-tone="bad"\]/,
  /^\.badge\[data-tone="bad"\]/,
  /^\.badge\[data-status="(FAILED|TIMED_OUT|BLOCKED)"\]$/,
  /^\.tag-bad$/,
  /^\.state\[data-state="error"\]/,
  /^\.search-problem/,
  /^\.evaluation\[data-state="failed"\]/,
  /^\.live-bar\[data-tone="bad"\]/,
  /^\.tl-row\[data-(first-)?error\]/,
  /^\.drawer\[data-error\]/,
  /^\.drawer-error/,
  /^\.drawer \.drawer-error/,
  /^\.story li\[data-tone="bad"\]/,
  /^\.column-fill\[data-tone="bad"\]/,
  /^\.field-error$/,
  /^\.findings-state\[data-tone="bad"\]/,
  /^\.finding\[data-severity="critical"\]/,
  /^\.runs td\.num strong$/,
  /^\.evaluation-error$/,
  /^\.compare-table \.delta\[data-tone="bad"\]/,
  /^\.agent-item\[data-error\]/,
  /^\.wf-row\[data-(first-)?error\]/,
  /^\.wf-lane-bar\[data-error\]/,
  /^\.search-caret span$/,
  /^:root$/, // token definitions and aliases
];

function rules(src: string): { selector: string; body: string }[] {
  const out: { selector: string; body: string }[] = [];
  const clean = src.replace(/\/\*[\s\S]*?\*\//g, "");
  for (const m of clean.matchAll(/([^{}]+)\{([^{}]*)\}/g))
    out.push({ selector: m[1]!.trim().replace(/\s+/g, " "), body: m[2]! });
  return out;
}

describe("red is for failure only", () => {
  const uses: string[] = [];
  for (const f of FILES) {
    const src = readFileSync(path.join(__dirname, "../src/app", f), "utf8");
    for (const r of rules(src))
      if (/var\(--(fail|bad)(-bg)?\b|color-mix\([^)]*--(fail|bad)\b/.test(r.body))
        uses.push(`${f}: ${r.selector}`);
  }
  it("finds the failure rules at all", () => {
    expect(uses.length).toBeGreaterThan(10);
  });
  it("every rule that reads --fail/--bad is a failure selector", () => {
    const stray = uses.filter((u) => {
      const sel = u.slice(u.indexOf(": ") + 2);
      return !sel.split(",").every((p) => ALLOWED.some((re) => re.test(p.trim())));
    });
    expect(stray).toEqual([]);
  });
  it("destructive and diff-removal styling does not use red", () => {
    expect(uses.join("\n")).not.toMatch(/button\.danger|diff-del/);
  });
});

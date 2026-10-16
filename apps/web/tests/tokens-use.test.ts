import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

// ADR-111 / ADR-166: red means failure and nothing else (design pass 2 tightens it to the fault line and the failed word:
// tests/tokens-colour-rules.test.ts pins the token side and a ratchet on this list's length; 32 before 5a, 26 after 5a, 7 after 5b, 6 after 5c (the form field error is ink with the word "Error:"):
// the run page's tinted rows, red headline, red counts, red finding bars, red drawer section and the red landing outline are gone).
// The old and the new token names are both policed.
// ADR-111: red means failure and nothing else. Any rule that reads --fail, --bad or their -bg tints must be on this allowlist
// of failure selectors; a new use needs a new entry here, which is the review point.
// Every stylesheet in src/app, not a hand-picked list: a new file is covered the day it is added (review P2-2).
const DIR = path.join(__dirname, "../src/app");
const FILES = readdirSync(DIR)
  .filter((f) => f.endsWith(".css"))
  .sort();
const ALLOWED: RegExp[] = [
  /^\.badge\[data-status="(FAILED|TIMED_OUT|BLOCKED)"\]/, // the status word of a run
  /^\.tl-fault(-label|::before)?$/, // the fault line (its ::before is the line itself) and its label
  /^\.wf-fault-(line|flag)/, // the same cut across the waterfall's lanes, and its flag
  /^\.run-fail-dash$/, // the miniature fault line before a failure sentence in the runs list (step 5a)
  /^:root$/, // token definitions and aliases
  /^\.column-fill\[data-tone="bad"\]/, // TODO(5d): the Analytics chart's error column, the last chart that paints a failure red
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
    const src = readFileSync(path.join(DIR, f), "utf8");
    for (const r of rules(src))
      if (
        /var\(--(fail|bad|color-status-fault|color-legacy-fault-bg)(-bg)?\b|color-mix\([^)]*--(fail|bad|color-status-fault)\b/.test(
          r.body,
        )
      )
        uses.push(`${f}: ${r.selector}`);
  }
  it("reads every stylesheet, the Phase 22 ones included", () => {
    for (const f of [
      "login.css",
      "analytics.css",
      "settings.css",
      "setup-guide.css",
      "illustrations.css",
    ])
      expect(FILES).toContain(f);
  });
  it("finds the failure rules at all", () => {
    expect(uses.length).toBeGreaterThanOrEqual(8);
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

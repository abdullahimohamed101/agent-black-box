import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { colorHex, isRedish, leaves, loadTokens } from "../scripts/tokens-lib.mjs";

// Design pass 2 (ADR-166), "One Red Line": colour comes from tokens.json and red appears only as the fault line and the failed
// word. Both rules start from an allow-list of today's violations and may only SHRINK: the counts below are exact, so removing
// a violation means lowering its number here (the ratchet), and adding one fails the test. Step 5 takes them to zero.
const DIR = path.join(__dirname, "../src/app");
const FILES = readdirSync(DIR)
  .filter((f) => f.endsWith(".css") && !f.startsWith("tokens."))
  .sort();
const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, "");
const LITERAL =
  /#[0-9a-fA-F]{8}\b|#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|\brgba?\([^)]*\)|\bhsla?\([^)]*\)|\boklch\([^)]*\)/g;

// Step 5b moved the syntax-highlight colours to color.syntax.* and the waterfall palette to color.data.* (desaturated, none
// red-ish), and dropped the var() fallbacks: no stylesheet carries a colour literal any more (KI-271). The allow-lists stay as
// exact objects so a new literal fails the test; they were { globals.css: 12, waterfall.css: 35 } and { globals.css: 1,
// waterfall.css: 4 } before 5b.
const RAW_COLOUR_ALLOWED: Record<string, number> = {};
const RED_LITERALS_ALLOWED: Record<string, number> = {};

describe("no raw colour outside the token files", () => {
  const counts: Record<string, number> = {};
  const redCounts: Record<string, number> = {};
  for (const f of FILES) {
    const lits = strip(readFileSync(path.join(DIR, f), "utf8")).match(LITERAL) ?? [];
    if (lits.length) counts[f] = lits.length;
    const reds = lits.filter((l) => /^#[0-9a-fA-F]{6}$/.test(l) && isRedish(l.toLowerCase()));
    if (reds.length) redCounts[f] = reds.length;
  }
  it("scans every stylesheet but the two generated files", () => {
    expect(FILES.length).toBeGreaterThan(15);
    expect(FILES).not.toContain("tokens.generated.css");
    expect(FILES).toContain("globals.css");
  });
  it("matches the allow-list exactly (shrink it when you remove a literal)", () => {
    expect(counts).toEqual(RAW_COLOUR_ALLOWED);
  });
  it("the red-ish literal allow-list matches exactly and is a subset of the raw allow-list", () => {
    expect(redCounts).toEqual(RED_LITERALS_ALLOWED);
    for (const f of Object.keys(RED_LITERALS_ALLOWED))
      expect(RAW_COLOUR_ALLOWED[f]).toBeGreaterThan(0);
    expect(Object.keys(RED_LITERALS_ALLOWED)).toEqual([]);
  });
  it("components do not carry inline colour literals", () => {
    const src = path.join(__dirname, "../src");
    const bad: string[] = [];
    const walk = (d: string) => {
      for (const f of readdirSync(d, { withFileTypes: true })) {
        const p = path.join(d, f.name);
        if (f.isDirectory()) walk(p);
        else if (/\.tsx?$/.test(f.name) && !p.includes("/lib/api/"))
          if (
            /(fill|stroke|color|background)[:=]\s*\{?["'`]#[0-9a-fA-F]{3,8}/.test(
              readFileSync(p, "utf8"),
            )
          )
            bad.push(path.relative(src, p));
      }
    };
    walk(src);
    expect(bad).toEqual([]);
  });
});

describe("the one red line (tokens)", () => {
  // Step 5b deleted color.legacy.* (the red tint went with it): only the fault tokens remain red-ish (KI-272).
  const RED_TOKENS_ALLOWED = [
    "color.primitive.red.300",
    "color.primitive.red.600",
    "color.status.fault",
  ];
  const tokens = loadTokens();
  const red = new Set<string>();
  for (const { path: p, node } of leaves(tokens) as {
    path: string;
    node: Record<string, string>;
  }[]) {
    if (!p.startsWith("color.")) continue;
    for (const theme of node.light !== undefined ? ["light", "dark"] : ["light"])
      if (isRedish(colorHex(tokens, p, theme))) red.add(p);
  }
  it("red-ish values appear only in the fault tokens", () => {
    // semantic tokens that merely point at the red primitives are red too: none may, other than the fault token
    expect([...red].sort()).toEqual(RED_TOKENS_ALLOWED);
  });
  it("the fault token is the only semantic token that resolves to red", () => {
    const semanticRed = [...red].filter(
      (p) => !p.startsWith("color.primitive.") && !p.startsWith("color.legacy."),
    );
    expect(semanticRed).toEqual(["color.status.fault"]);
  });
  it("caution (amber), the accent, the mark and secondary text are not red", () => {
    for (const p of [
      "color.status.caution",
      "color.interactive.default",
      "color.brand.mark",
      "color.text.secondary",
    ])
      for (const theme of ["light", "dark"])
        expect(isRedish(colorHex(tokens, p, theme)), `${p} ${theme}`).toBe(false);
  });
  it("the allow-list of red uses in CSS does not grow (tests/tokens-use.test.ts holds the selectors)", () => {
    const src = readFileSync(path.join(__dirname, "tokens-use.test.ts"), "utf8");
    const entries = src
      .slice(src.indexOf("const ALLOWED"), src.indexOf("];", src.indexOf("const ALLOWED")))
      .split("\n")
      .filter((l) => /^\s+\//.test(l));
    expect(entries.length).toBeLessThanOrEqual(7); // 32 before step 5a, 26 after it (runs list, notices), 7 after 5b (the run page)
  });
});

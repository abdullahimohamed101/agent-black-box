import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

// Design pass 2 step 5d (ADR-166): the whole app uses the seven type steps, two weights and two radii, and nothing shouts. This
// scans every stylesheet for a value that is not a token, so a new off-scale size fails here instead of shipping. The exceptions
// are few and named: the 13 px body base, the 20 px phone headings (title-line.css, run-header.css) and circles (50%).
const DIR = path.join(__dirname, "../src/app");
const FILES = readdirSync(DIR)
  .filter((f) => f.endsWith(".css") && !f.startsWith("tokens."))
  .sort();
const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, "");
function decls(prop: RegExp): { file: string; value: string }[] {
  const out: { file: string; value: string }[] = [];
  for (const f of FILES)
    for (const m of strip(readFileSync(path.join(DIR, f), "utf8")).matchAll(prop))
      out.push({ file: f, value: m[1]!.trim() });
  return out;
}
const TOKEN_SIZE = /^var\(--type-[a-z0-9-]+-size\)$/;

describe("type steps, weights and radii are tokens", () => {
  it("font-size is a type token, inherit, or one of the two phone headings", () => {
    const bad = decls(/(?<![\w-])font-size:\s*([^;}]+)/g).filter(
      (d) =>
        !(
          TOKEN_SIZE.test(d.value) ||
          d.value === "inherit" ||
          (d.value === "1.25rem" && /^(title-line|run-header)\.css$/.test(d.file))
        ),
    );
    expect(bad).toEqual([]);
  });
  it("the font shorthand names a type token (or inherit, or the 13 px body base)", () => {
    const bad = decls(/(?<![\w-])font:\s*([^;}]+)/g).filter(
      (d) =>
        !(
          /var\(--type-/.test(d.value) ||
          d.value === "inherit" ||
          (d.file === "globals.css" && d.value.startsWith("13px/1.45"))
        ),
    );
    expect(bad).toEqual([]);
  });
  it("weights are 400 and 600 only", () => {
    const bad = decls(/(?<![\w-])font-weight:\s*([^;}]+)/g).filter(
      (d) =>
        !(
          /^var\(--(weight-(strong|regular)|type-[a-z0-9-]+-weight)\)$/.test(d.value) ||
          ["400", "600", "inherit"].includes(d.value)
        ),
    );
    expect(bad).toEqual([]);
    const shorthand = decls(/(?<![\w-])font:\s*([^;}]+)/g).filter((d) =>
      /^(100|200|300|500|700|800|900|bold|bolder|lighter)\b/.test(d.value),
    );
    expect(shorthand).toEqual([]);
  });
  it("radii are 0, the control radius or a circle", () => {
    const bad = decls(/(?<![\w-])border-radius:\s*([^;}]+)/g).filter(
      (d) =>
        !(
          ["4px", "50%", "inherit"].includes(d.value) ||
          d.value.split(/\s+/).every((v) => v === "0" || v === "var(--radius-control)")
        ),
    );
    expect(bad).toEqual([]);
  });
  it("nothing is upper-cased or tracked", () => {
    expect(decls(/text-transform:\s*([^;}]+)/g).filter((d) => d.value !== "none")).toEqual([]);
    expect(
      decls(/letter-spacing:\s*([^;}]+)/g).filter((d) => !["0", "normal"].includes(d.value)),
    ).toEqual([]);
  });
  it("the legacy page rules and the segmented control are gone", () => {
    const all = FILES.map((f) => strip(readFileSync(path.join(DIR, f), "utf8"))).join("\n");
    expect(all).not.toMatch(/\.page:not\(/);
    expect(all).not.toMatch(/\.seg-item|\.seg\b/);
  });
  it("no component sets a font size, weight or colour in an inline style", () => {
    const src = path.join(__dirname, "../src");
    const bad: string[] = [];
    const walk = (d: string) => {
      for (const f of readdirSync(d, { withFileTypes: true })) {
        const p = path.join(d, f.name);
        if (f.isDirectory()) walk(p);
        else if (
          /\.tsx$/.test(f.name) &&
          /style=\{\{[^}]*(fontSize|fontWeight|color)\s*:/.test(readFileSync(p, "utf8"))
        )
          bad.push(path.relative(src, p));
      }
    };
    walk(src);
    expect(bad).toEqual([]);
  });
});

describe("red is only the fault (the whole source, not only stylesheets)", () => {
  it("no component names a fault token, a red colour keyword or a red literal", () => {
    const src = path.join(__dirname, "../src");
    const bad: string[] = [];
    const walk = (d: string) => {
      for (const f of readdirSync(d, { withFileTypes: true })) {
        const p = path.join(d, f.name);
        if (f.isDirectory()) walk(p);
        else if (/\.tsx?$/.test(f.name) && !p.includes("/lib/api/") && !p.includes("/fixtures/"))
          if (/status-fault|\bcolor:\s*["']?(red|crimson|#f00)\b/i.test(readFileSync(p, "utf8")))
            bad.push(path.relative(src, p));
      }
    };
    walk(src);
    expect(bad).toEqual([]);
  });
  it("no stylesheet uses a red colour keyword", () => {
    const hits = decls(
      /(?:color|background(?:-color)?|border(?:-[a-z]+)?)\s*:\s*[^;}]*\b(red|crimson|firebrick|tomato)\b/g,
    );
    expect(hits).toEqual([]);
  });
});

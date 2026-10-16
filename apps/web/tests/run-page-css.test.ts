import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

// Design pass 2, batch 5b: structural rules of the run page and Compare that a screenshot would show and a unit test cannot, read
// from the sources. Each one is a thing the direction removed (01-direction.md Part C): they must not come back.
const SRC = path.join(__dirname, "../src");
const APP = path.join(SRC, "app");
const read = (...p: string[]) => readFileSync(path.join(SRC, ...p), "utf8");
const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, "");
const css = (f: string) => strip(readFileSync(path.join(APP, f), "utf8"));
const RUN_CSS = [
  "run-header.css",
  "run-layout.css",
  "run-timeline.css",
  "run-panels.css",
  "run-replay.css",
  "replay.css",
  "tabs.css",
  "title-line.css",
  "waterfall.css",
  "compare.css",
];
const walk = (d: string, out: string[] = []): string[] => {
  for (const e of readdirSync(d, { withFileTypes: true })) {
    const p = path.join(d, e.name);
    if (e.isDirectory()) walk(p, out);
    else out.push(p);
  }
  return out;
};

describe("rows are plain: no tint, no bar, no class column", () => {
  const c = css("run-timeline.css");
  const rules = [...c.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => ({
    sel: m[1]!.trim().replace(/\s+/g, " "),
    body: m[2]!,
  }));
  it("no row state paints a background or a left edge except the selection and the playhead", () => {
    for (const r of rules.filter((x) => /data-(error|first-error|after-fault|retry)/.test(x.sel)))
      expect(r.body, r.sel).not.toMatch(/background|box-shadow|outline|border-left/);
  });
  it("the selected row is the accent wash with a 2 px accent edge, the playhead the edge alone", () => {
    const sel = rules.find((r) => r.sel === ".tl-row[data-selected]")!;
    expect(sel.body).toMatch(/background: var\(--color-bg-wash\)/);
    expect(sel.body).toMatch(/inset 2px 0 0 var\(--color-interactive-default\)/);
  });
  it("the timeline has no class column in the markup", () => {
    expect(read("components", "Timeline.tsx")).not.toMatch(/tl-class|CLASS_LABELS/);
  });
  it("an error is a word in ink and a retry a caution token", () => {
    expect(rules.find((r) => r.sel === '.tl-flag[data-kind="error"]')!.body).not.toMatch(/color/);
    expect(rules.find((r) => r.sel === '.tl-flag[data-kind="retry"]')!.body).toMatch(
      /var\(--color-status-caution\)/,
    );
  });
});

describe("the fault line", () => {
  const c = css("run-timeline.css");
  it("is 2 px, red, and its label is the fault colour on the canvas colour", () => {
    expect(/\.tl-fault \{[^}]*height: 2px/.test(c)).toBe(true);
    expect(/\.tl-fault::before \{[^}]*background: var\(--color-status-fault\)/.test(c)).toBe(true);
    const label = /\.tl-fault-label \{([^}]*)\}/.exec(c)![1]!;
    expect(label).toMatch(/color: var\(--color-status-fault\)/);
    expect(label).toMatch(/background: var\(--color-bg-canvas\)/);
    expect(label).not.toMatch(/background: var\(--color-status-fault\)/); // no filled chip
  });
  it("draws once in 280 ms under no-preference and is not animated at all under reduce", () => {
    const m = strip(readFileSync(path.join(APP, "run-motion.css"), "utf8"));
    const gated = /@media \(prefers-reduced-motion: no-preference\)\s*\{([\s\S]*)\n\}\s*$/.exec(
      m,
    )![1]!;
    expect(gated).toMatch(/--t-fault: 280ms/);
    expect(gated).toMatch(
      /\.work\[data-fault-intro\] \.tl-fault::before \{\s*animation: fault-draw-x var\(--t-fault\)/,
    );
    // nothing outside the no-preference gate starts the animation
    const outside = m.replace(gated, "");
    expect(outside).not.toMatch(/animation:/);
    expect(m).not.toMatch(/prefers-reduced-motion: reduce/);
  });
});

describe("the page has one voice", () => {
  it("no run or compare stylesheet uses upper case, letter spacing, 500 weights, a shadow or a left tone bar", () => {
    for (const f of RUN_CSS) {
      const c = css(f);
      expect(c, f).not.toMatch(/text-transform:\s*uppercase/);
      expect(c, f).not.toMatch(/letter-spacing:\s*[^0\s]/);
      expect(c, f).not.toMatch(/font-weight:\s*500|font:\s*500\b/);
      expect(c, f).not.toMatch(/box-shadow:\s*0|rgb\(/);
      expect(c, f).not.toMatch(/border-left:\s*[34]px/);
    }
  });
  it("no source asks for the condensed or serif faces, a drawn key cap or the old radio tabs", () => {
    const all = walk(SRC).filter((f) => /\.(tsx?|css)$/.test(f));
    for (const f of all) {
      const s = readFileSync(f, "utf8");
      expect(s, f).not.toMatch(
        /var\(--cond\)|var\(--serif\)|IBMPlexSansCondensed|IBMPlexSerif|plex-cond|plex-serif/,
      );
      expect(s, f).not.toMatch(/seg-item|ViewSwitch|context-toggle|context-strip/);
    }
    for (const f of ["RunDetail.tsx", "AlignedSteps.tsx", "RunHeadline.tsx", "Timeline.tsx"])
      expect(read("components", f), f).not.toMatch(/<kbd/);
  });
  it("one tab component: role=tab is written once", () => {
    const hits = walk(path.join(SRC, "components")).filter((f) =>
      /role="tab"/.test(readFileSync(f, "utf8")),
    );
    expect(hits.map((f) => path.basename(f))).toEqual(["Tabs.tsx"]);
  });
});

describe("the layout contract (03)", () => {
  const c = css("run-layout.css");
  it("keeps the fixed-height page and the zero page padding, and becomes a document below 832 px or 576 px of height", () => {
    expect(c).toMatch(/\.run-detail \{[^}]*height: calc\(100dvh - var\(--layout-topbar\)\)/);
    expect(c).toMatch(/\.shell main\.page:has\(> \.run-detail\) \{[^}]*padding: 0/);
    expect(c).toMatch(
      /@media \(max-width: 52rem\), \(max-height: 36rem\) \{[^}]*\.run-detail \{[^}]*height: auto/,
    );
  });
  it("Steps is a column from 1100 px, the inspector a column from 1280 px and an overlay below", () => {
    expect(c).toMatch(/@media \(min-width: 68\.75rem\) and \(min-height: 36rem\)/);
    expect(c).toMatch(/@media \(min-width: 80rem\) and \(min-height: 36rem\)/);
    expect(/\.drawer \{[^}]*position: fixed/.test(c)).toBe(true);
  });
});

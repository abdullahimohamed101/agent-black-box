import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

// Design pass 2 step 5c: Review, the settings pattern and Alerts share one voice (ADR-166, "One Red Line"). The stylesheets of
// these screens use tokens only, no upper case or tracking, no 500 weight, no pill or card radius, no left tone bar, no shadow,
// and nothing red: a quota near its limit is caution text, a destructive action is caution text with no fill.
const APP = path.join(__dirname, "../src/app");
const FILES = [
  "settings.css",
  "review.css",
  "tabs.css",
  "quotas.css",
  "retention.css",
  "sessions.css",
  "alerts.css",
  "policy.css",
  "security.css",
];
const read = (f: string) =>
  readFileSync(path.join(APP, f), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

describe("the stylesheets of Review and Settings", () => {
  for (const f of FILES)
    describe(f, () => {
      const c = read(f);
      it("uses tokens for colour, never a literal", () => {
        expect(c).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(|oklch\(/);
      });
      it("has no upper case, tracking, 500 weights, shadows, pills or left tone bars", () => {
        expect(c).not.toMatch(/text-transform:\s*uppercase/);
        expect(c).not.toMatch(/letter-spacing:\s*[^0\s]/);
        expect(c).not.toMatch(/font-weight:\s*500|font:\s*500\b/);
        expect(c).not.toMatch(/box-shadow:\s*0/);
        expect(c).not.toMatch(/border-radius:\s*(999px|6px|50%)/);
        expect(c).not.toMatch(/border-left:\s*[34]px/);
      });
      it("reads no alias of the pre-pass-2 names", () => {
        expect(c).not.toMatch(
          /var\(--(bg|surface|border|border-strong|ink|muted|accent|sel|fail|wait|fs-[a-z]+|ease-out|top-h|sans|mono)\b/,
        );
      });
      it("never reads the fault colour (red is the failed run only)", () => {
        expect(c).not.toMatch(/var\(--(color-status-fault|fail|bad)/);
      });
    });

  it("a quota bar is caution only when near the limit and ink at the limit, never red", () => {
    const c = read("quotas.css");
    expect(c).toMatch(
      /\[data-status="near_limit"\] \{\s*background: var\(--color-status-caution\)/,
    );
    expect(c).toMatch(/\[data-status="at_limit"\] \{\s*background: var\(--color-text-primary\)/);
  });

  it("a destructive button is caution text with no fill, and a field error is ink with the word", () => {
    const g = read("globals.css");
    const danger = /button\.danger \{([^}]*)\}/.exec(g)![1]!;
    expect(danger).toMatch(/color: var\(--color-status-caution\)/);
    expect(danger).toMatch(/background: none/);
    const err = /\.field-error \{([^}]*)\}/.exec(g)![1]!;
    expect(err).toMatch(/color: var\(--color-text-primary\)/);
    expect(g).toMatch(/\.field-error::before \{[^}]*content: "Error: "/);
  });

  it("the tab list has a chip density and a hairline variant, one component for every tab row", () => {
    const t = read("tabs.css");
    expect(t).toMatch(/\.tabs-chip \.tab \{[^}]*min-height: 1\.75rem/);
    expect(t).toMatch(/\.tabs-ruled \{[^}]*border-bottom: 1px solid var\(--color-border-subtle\)/);
  });

  it("no source still carries the old tab styles", () => {
    for (const f of [...FILES, "globals.css"])
      expect(read(f), f).not.toMatch(/\.(subnav|apr-tabs|al-tabs|seg-item)\b/);
  });
});

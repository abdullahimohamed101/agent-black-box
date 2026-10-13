import { render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { SecuritySeverityMark } from "@/components/SecuritySeverity";
import { securitySeverityMeta } from "@/lib/security";

// Phase 13 D5 / ADR-111: severity is a shield glyph and a word, never a new hue and never red.
const css = readFileSync(path.join(__dirname, "../src/app/security.css"), "utf8");
const globals = readFileSync(path.join(__dirname, "../src/app/globals.css"), "utf8");

describe("severity glyph and word", () => {
  it.each([
    ["critical", "Critical", "filled", "ink"],
    ["high", "High", "outlined", "wait"],
    ["medium", "Medium", "outlined", "muted"],
    ["warning", "Warning", "outlined", "muted"],
    ["info", "Info", "outlined", "muted"],
  ])("%s is the word %s with a %s shield in %s", (sev, word, style, tone) => {
    const { container } = render(<SecuritySeverityMark severity={sev} />);
    expect(screen.getByText(word)).toBeInTheDocument();
    const svg = container.querySelector("svg")!;
    expect(svg).toHaveAttribute("aria-hidden", "true"); // the word is the name
    expect(svg.dataset.filled === "true").toBe(style === "filled");
    expect(container.firstElementChild).toHaveAttribute("data-tone", tone);
  });

  it("ranks critical above high above medium", () => {
    const r = (s: string) => securitySeverityMeta(s).rank;
    expect(r("critical")).toBeGreaterThan(r("high"));
    expect(r("high")).toBeGreaterThan(r("medium"));
  });

  it("an unknown severity from the API is shown as plain text, bounded", () => {
    render(<SecuritySeverityMark severity={"x".repeat(80)} />);
    expect(screen.getByText("x".repeat(24))).toBeInTheDocument();
  });
});

function block(src: string): Record<string, string> {
  const start = src.indexOf(":root {");
  const body = src.slice(start, src.indexOf("\n}", start));
  const out: Record<string, string> = {};
  for (const m of body.matchAll(/--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6}|var\(--[a-z0-9-]+\))\s*;/g))
    out[m[1]!] = m[2]!;
  return out;
}
const dark = globals.indexOf("@media (prefers-color-scheme: dark)");
const light = block(globals);
const themes = { light, dark: { ...light, ...block(globals.slice(dark)) } };
const lum = (hex: string) => {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const contrast = (a: string, b: string) =>
  (Math.max(lum(a), lum(b)) + 0.05) / (Math.min(lum(a), lum(b)) + 0.05);

describe("severity colours", () => {
  it("security.css reads no red and no other new hue", () => {
    expect(css).not.toMatch(/var\(--(fail|bad)/);
    expect(css).not.toMatch(/#[0-9a-fA-F]{3,6}\b/);
  });
  describe.each(Object.keys(themes) as (keyof typeof themes)[])("%s", (name) => {
    const t = themes[name];
    const hex = (k: string) => {
      let v = t[k]!;
      for (let i = 0; i < 4 && v.startsWith("var("); i++) v = t[v.slice(6, -1)]!;
      return v;
    };
    // Where a severity mark is drawn: the page, cards, the zebra surface, a selected row, and the amber wash.
    for (const bg of ["bg", "surface", "surface-2", "sel"]) {
      it.each(["ink", "muted"])(`the word in %s on ${bg} is at least 4.5:1`, (fg) => {
        expect(contrast(hex(fg), hex(bg))).toBeGreaterThanOrEqual(4.5);
      });
      it(`the amber outline (--wait) on ${bg} is at least 3:1 as a graphic and 4.5:1 here`, () => {
        expect(contrast(hex("wait"), hex(bg))).toBeGreaterThanOrEqual(4.5);
      });
    }
  });
});

import { contextSummary, securityCountText } from "@/lib/security";
describe("wording", () => {
  it("counts in the singular for one", () => {
    expect(securityCountText(1)).toBe("1 security finding");
    expect(securityCountText(2)).toBe("2 security findings");
  });
  it("the Context summary keeps the reliability wording and adds security first", () => {
    expect(contextSummary(0, 2)).toBe("2 findings");
    expect(contextSummary(2, 1)).toBe("2 security, 1 finding");
    expect(contextSummary(1, 0)).toBe("1 security");
  });
});

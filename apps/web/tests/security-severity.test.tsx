import { render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { contrast, ROLE_TOKENS, themes as themeVars } from "./tokenCss";
import { SecuritySeverityMark } from "@/components/SecuritySeverity";
import { securitySeverityMeta } from "@/lib/security";

// Phase 13 D5 / ADR-111: severity is a shield glyph and a word, never a new hue and never red.
const css = readFileSync(path.join(__dirname, "../src/app/security.css"), "utf8");

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

const themes = themeVars() as Record<"light" | "dark", Record<string, string>>;

describe("severity colours", () => {
  it("security.css reads no red and no other new hue", () => {
    expect(css).not.toMatch(/var\(--(fail|bad|color-status-fault)/);
    expect(css).not.toMatch(/#[0-9a-fA-F]{3,6}\b/);
  });
  describe.each(Object.keys(themes) as (keyof typeof themes)[])("%s", (name) => {
    const t = themes[name];
    const hex = (role: string) => {
      let v = t[ROLE_TOKENS[role as keyof typeof ROLE_TOKENS]]!;
      for (let i = 0; i < 4 && v.startsWith("var("); i++) v = t[v.slice(6, -1)]!;
      return v;
    };
    // Where a severity mark is drawn: the page, the raised surface and a selected row (the zebra surface and the amber wash are gone).
    for (const bg of ["bg", "surface", "sel"]) {
      it.each(["ink", "muted"])(`the word in %s on ${bg} is at least 4.5:1`, (fg) => {
        expect(contrast(hex(fg), hex(bg))).toBeGreaterThanOrEqual(4.5);
      });
      it(`the amber outline (--wait) on ${bg} is at least 3:1 as a graphic and 4.5:1 here`, () => {
        expect(contrast(hex("wait"), hex(bg))).toBeGreaterThanOrEqual(4.5);
      });
    }
  });
});

import { securityCountText } from "@/lib/security";
describe("wording", () => {
  it("counts in the singular for one", () => {
    expect(securityCountText(1)).toBe("1 security finding");
    expect(securityCountText(2)).toBe("2 security findings");
  });
});

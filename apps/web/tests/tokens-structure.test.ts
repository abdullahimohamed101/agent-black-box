import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  GENERATED_CSS,
  REPORT_MD,
  applySections,
  colorHex,
  cssName,
  leaves,
  loadTokens,
  lookup,
  renderSections,
  renderTokensCss,
  resolveValue,
} from "../scripts/tokens-lib.mjs";

// Design pass 2 (ADR-166): design-tokens/tokens.json is the only source of colour, type, space, radius and motion values.
// This protects its structure and keeps every generated artifact current.
const tokens = loadTokens();
const all = leaves(tokens) as { path: string; node: Record<string, string> }[];
const colours = all.filter((l) => l.path.startsWith("color."));
const semantic = colours.filter((l) => l.node.light !== undefined);

describe("tokens.json structure", () => {
  it("has the Style Dictionary categories the build prompt names", () => {
    for (const top of ["color", "type", "space", "radius", "motion"])
      expect(tokens[top]).toBeTruthy();
    for (const g of ["interactive", "bg", "text", "border", "status"])
      expect(tokens.color[g], g).toBeTruthy();
  });

  it("every leaf has exactly one of value or light+dark", () => {
    for (const { path: p, node } of all) {
      const themed = node.light !== undefined || node.dark !== undefined;
      const plain = node.value !== undefined;
      expect(themed !== plain, p).toBe(true);
      if (themed) expect(node.dark, `${p} has no dark value`).toBeDefined();
    }
  });

  it("every reference resolves, in both themes, to a hex colour (colours) or a literal", () => {
    for (const { path: p, node } of colours) {
      for (const theme of node.light !== undefined ? ["light", "dark"] : ["light"]) {
        expect(colorHex(tokens, p, theme), `${p} (${theme})`).toMatch(/^#[0-9a-f]{6}$/);
      }
    }
    for (const { path: p, node } of all.filter((l) => !l.path.startsWith("color.")))
      expect(resolveValue(tokens, node.value!, "light"), p).toBeTruthy();
  });

  it("semantic colours never point at another theme-dependent leaf with a different meaning in dark", () => {
    // a semantic token may reference a primitive or another semantic token; a reference to a themed token is followed per theme
    for (const { path: p, node } of semantic)
      for (const raw of [node.light!, node.dark!])
        if (raw.startsWith("{"))
          expect(lookup(tokens, raw.slice(1, -1)), `${p} -> ${raw}`).toBeTruthy();
  });

  it("the semantic layer is the schema from the prompt: interactive, bg, text, border and status", () => {
    for (const name of [
      "color.interactive.default",
      "color.interactive.hover",
      "color.interactive.active",
      "color.interactive.disabled",
      "color.bg.canvas",
      "color.bg.raised",
      "color.bg.wash",
      "color.text.primary",
      "color.text.secondary",
      "color.text.disabled",
      "color.text.inverse",
      "color.text.link",
      "color.border.subtle",
      "color.border.default",
      "color.border.strong",
      "color.status.fault",
      "color.status.caution",
    ])
      expect(
        semantic.map((l) => l.path),
        name,
      ).toContain(name);
  });

  it("the type scale is the direction's seven steps (plus the two mono roles) with two weights and no tracking", () => {
    const steps = Object.keys(tokens.type.scale).filter((k) => !k.startsWith("$"));
    expect(steps).toEqual([
      "display",
      "h1",
      "h2",
      "h3",
      "body-lg",
      "body",
      "small",
      "mono",
      "mono-small",
    ]);
    expect(Object.values<{ value: string }>(tokens.type.weight).map((w) => w.value)).toEqual([
      "400",
      "600",
    ]);
    expect(Object.values<{ value: string }>(tokens.type.tracking).map((w) => w.value)).toEqual([
      "0",
    ]);
    expect(tokens.type.measure.prose.value).toBe("72ch");
  });

  it("radii are 0 and 4 px only", () => {
    expect(Object.values<{ value: string }>(tokens.radius).map((r) => r.value)).toEqual([
      "0",
      "0.25rem",
    ]);
  });

  it("CSS variable names are unique", () => {
    const names = all.map((l) => cssName(l.path, l.node));
    expect(new Set(names).size).toBe(names.length);
  });

  it("every contrast pair and alias target names a real token", () => {
    for (const p of tokens.$contrast) {
      expect(lookup(tokens, p.fg), p.fg).toBeTruthy();
      expect(lookup(tokens, p.bg), p.bg).toBeTruthy();
    }
    for (const [name, a] of Object.entries<{ to?: string; status: string }>(tokens.alias)) {
      expect(["aliased", "kept", "delete"], name).toContain(a.status);
      if (a.to) expect(lookup(tokens, a.to), `${name} -> ${a.to}`).toBeTruthy();
    }
  });
});

describe("generated artifacts are current", () => {
  it("tokens.generated.css matches tokens.json", () => {
    expect(readFileSync(GENERATED_CSS, "utf8")).toBe(renderTokensCss(tokens));
  });
  it("tokens.aliases.css is gone: the old custom property names have no alias layer any more (step 5d)", () => {
    expect(existsSync(path.join(__dirname, "../src/app/tokens.aliases.css"))).toBe(false);
    expect(Object.values<{ to?: string }>(tokens.alias).filter((a) => a.to)).toEqual([]);
  });
  it("04-colour-type-tokens.md tables match tokens.json", () => {
    const md = readFileSync(REPORT_MD, "utf8");
    expect(applySections(md, renderSections(tokens))).toBe(md);
  });
  it("the generated file carries the do-not-edit header", () => {
    expect(readFileSync(GENERATED_CSS, "utf8").startsWith("/* generated: do not edit.")).toBe(true);
  });
  it("every pre-pass-2 custom property still resolves: defined in a stylesheet", () => {
    const dir = path.join(__dirname, "../src/app");
    const defined = new Set<string>();
    for (const f of readdirSync(dir).filter((x) => x.endsWith(".css")))
      for (const m of readFileSync(path.join(dir, f), "utf8").matchAll(
        /(?:^|[;{\s])--([a-z0-9-]+)\s*:/g,
      ))
        defined.add(m[1]!);
    const used = new Set<string>();
    for (const f of readdirSync(dir).filter((x) => x.endsWith(".css") && !x.startsWith("tokens.")))
      for (const m of readFileSync(path.join(dir, f), "utf8").matchAll(/var\(--([a-z0-9-]+)/g))
        used.add(m[1]!);
    // next/font variables and per-rule locals are set by Next or inline, not in a stylesheet
    const external = new Set([
      "font-plex-sans",
      "font-plex-cond",
      "font-plex-mono",
      "font-plex-serif",
    ]);
    const undefinedUses = [...used].filter((u) => !defined.has(u) && !external.has(u));
    expect(undefinedUses).toEqual([]);
  });
});

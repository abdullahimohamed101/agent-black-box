import { readFileSync } from "node:fs";
import path from "node:path";

// Reads the colour custom properties the way the browser would: tokens.generated.css, then
// globals.css (the order layout.tsx imports them), light block first and the prefers-color-scheme: dark overrides on top.
// Values are resolved through var() chains to the hex the primitives hold. Shared by the contrast, palette and red tests.
const DIR = path.join(__dirname, "../src/app");
export const FILES = ["tokens.generated.css", "globals.css"];

function strip(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, "");
}
/** Top-level `selector { body }` segments, brace-matched (the media blocks nest one level). */
function topLevel(src: string): { head: string; body: string }[] {
  const out: { head: string; body: string }[] = [];
  let depth = 0;
  let headStart = 0;
  let bodyStart = 0;
  for (let i = 0; i < src.length; i++) {
    const c = src[i];
    if (c === "{") {
      if (depth === 0) {
        bodyStart = i + 1;
      }
      depth++;
    } else if (c === "}") {
      depth--;
      if (depth === 0) {
        out.push({
          head: src.slice(headStart, bodyStart - 1).trim(),
          body: src.slice(bodyStart, i),
        });
        headStart = i + 1;
      }
    }
  }
  return out;
}
function decls(body: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of body.matchAll(/--([a-z0-9-]+):\s*([^;{}]+);/g)) out[m[1]!] = m[2]!.trim();
  return out;
}

export function rawVars(): { light: Record<string, string>; dark: Record<string, string> } {
  const light: Record<string, string> = {};
  const dark: Record<string, string> = {};
  for (const f of FILES) {
    for (const seg of topLevel(strip(readFileSync(path.join(DIR, f), "utf8")))) {
      if (seg.head === ":root") Object.assign(light, decls(seg.body));
      else if (seg.head === "@media (prefers-color-scheme: dark)")
        for (const inner of topLevel(seg.body))
          if (inner.head === ":root") Object.assign(dark, decls(inner.body));
    }
  }
  return { light, dark };
}

function resolve(raw: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const k of Object.keys(raw)) {
    let v = raw[k]!;
    for (let i = 0; i < 8; i++) {
      const m = /^var\(--([a-z0-9-]+)\)$/.exec(v);
      if (!m) break;
      v = raw[m[1]!] ?? v;
    }
    out[k] = v;
  }
  return out;
}

/** Every custom property per scheme, var() chains followed. Colours come out as `#rrggbb`. */
export function themes(): Record<string, Record<string, string>> {
  const { light, dark } = rawVars();
  return { light: resolve(light), dark: resolve({ ...light, ...dark }) };
}

export function lum(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
export function contrast(a: string, b: string): number {
  const [x, y] = [lum(a), lum(b)];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

/**
 * The colour ROLES the contrast tests are written in terms of, read from the real tokens. The pre-pass-2 alias names (--bg,
 * --ink, --muted, ...) are deleted from the stylesheets (step 5c), so the tests name the roles here and look each one up under
 * its token name; the pairs and thresholds they assert are unchanged.
 */
export const ROLE_TOKENS = {
  bg: "color-bg-canvas",
  surface: "color-bg-raised",
  sel: "color-bg-wash",
  border: "color-border-subtle",
  ink: "color-text-primary",
  muted: "color-text-secondary",
  accent: "color-interactive-default",
  fail: "color-status-fault",
  wait: "color-status-caution",
} as const;
export function roles(theme: Record<string, string>): Record<string, string> {
  return Object.fromEntries(
    Object.entries(ROLE_TOKENS).map(([role, token]) => [role, theme[token]!]),
  );
}

import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

// The zero-motion contract (Phase 21, extended in Phase 22 step 6). A person who asks for reduced motion gets a static,
// complete page. So every animation, transition and keyframe in src/app must be one of:
//   1. inside `@media (prefers-reduced-motion: no-preference)` (it does not exist for them), or
//   2. switched off by a `prefers-reduced-motion: reduce` rule for the same selector, or
//   3. timed only with the `--t-*` tokens, which the reduce block sets to 0ms.
// New motion goes in run-motion.css and is gated; a rule that is none of the three fails here, which is the review point.
const DIR = path.join(__dirname, "../src/app");
const FILES = readdirSync(DIR).filter((f) => f.endsWith(".css"));

type Decl = { file: string; chain: string[]; selector: string; prop: string; value: string };
type Keyframes = { file: string; name: string; gated: boolean; props: Set<string> };

/** A small CSS walker: enough for plain rules, @media, @supports and @keyframes (what this stylesheet set uses). */
function walk(file: string, src: string) {
  const text = src.replace(/\/\*[\s\S]*?\*\//g, "");
  const decls: Decl[] = [];
  const keyframes: Keyframes[] = [];
  const stack: string[] = [];
  let buf = "";
  let current: Keyframes | null = null;
  const gated = () => stack.some((p) => /prefers-reduced-motion:\s*no-preference/.test(p));
  const flush = (inRule: boolean) => {
    const body = buf.trim();
    buf = "";
    if (!inRule || !body) return;
    const owner = stack[stack.length - 1] ?? "";
    for (const d of body.split(";")) {
      const i = d.indexOf(":");
      if (i < 0) continue;
      const prop = d.slice(0, i).trim().toLowerCase();
      const value = d.slice(i + 1).trim();
      if (current && owner !== current.name) current.props.add(prop);
      else if (!current)
        decls.push({
          file,
          chain: [...stack],
          selector: owner.replace(/\s+/g, " "),
          prop,
          value,
        });
    }
  };
  for (const ch of text) {
    if (ch === "{") {
      const prelude = buf.trim();
      buf = "";
      stack.push(prelude);
      const kf = /^@keyframes\s+([\w-]+)/.exec(prelude);
      if (kf) {
        current = { file, name: prelude, gated: gated(), props: new Set() };
        keyframes.push(current);
      }
    } else if (ch === "}") {
      flush(true);
      const closed = stack.pop();
      if (current && closed === current.name) current = null;
    } else if (ch === ";" && stack.length === 0) buf = "";
    else buf += ch;
  }
  return { decls, keyframes };
}

const parsed = FILES.map((f) => walk(f, readFileSync(path.join(DIR, f), "utf8")));
const decls = parsed.flatMap((p) => p.decls);
const keyframes = parsed.flatMap((p) => p.keyframes);

const MOTION_PROPS =
  /^(animation|animation-name|animation-duration|animation-iteration-count|transition|transition-property|transition-duration|scroll-behavior)$/;
const isOff = (v: string) => /^(none|0s|0ms|auto|initial)$/.test(v.trim());
const isGated = (d: Decl) => d.chain.some((p) => /prefers-reduced-motion:\s*no-preference/.test(p));
const inReduce = (d: Decl) => d.chain.some((p) => /prefers-reduced-motion:\s*reduce/.test(p));
const tokenOnly = (v: string) =>
  [...v.matchAll(/(\d*\.?\d+)(ms|s)\b/g)].length === 0 || /var\(--t-[a-z]+\)/.test(v);

const moving = decls.filter(
  (d) => MOTION_PROPS.test(d.prop) && !isOff(d.value) && !inReduce(d) && !/^none\b/.test(d.value),
);
const reduceOverrides = decls.filter(
  (d) => inReduce(d) && MOTION_PROPS.test(d.prop) && /none|0ms|0s/.test(d.value),
);
const hasOverride = (d: Decl) =>
  reduceOverrides.some((o) => o.selector.split(",").some((s) => d.selector.split(",").includes(s)));

describe("zero-motion contract", () => {
  it("the walker sees a violation: an ungated animation with no reduce override", () => {
    const bad = walk(
      "x.css",
      `@keyframes spin2 { to { transform: rotate(1turn); } }
       .a { animation: spin2 1s; }
       @media (prefers-reduced-motion: no-preference) { .b { transition: opacity 120ms; } }`,
    );
    expect(bad.keyframes.map((k) => k.gated)).toEqual([false]);
    const [a, b] = bad.decls;
    expect(a!.selector).toBe(".a");
    expect(isGated(a!)).toBe(false);
    expect(isGated(b!)).toBe(true);
  });

  it("finds the motion at all", () => {
    // Design pass 2 removed the headline fade (2 keyframes, 2 rules) and the live-row rise (1 keyframe, 2 rules): 8 and 6 became 6 and 5.
    expect(moving.length).toBeGreaterThanOrEqual(6);
    expect(keyframes.length).toBeGreaterThanOrEqual(6);
  });

  it("every animation, transition and scroll-behavior is gated, switched off for reduce, or timed only by tokens", () => {
    const stray = moving
      .filter(
        (d) => !isGated(d) && !hasOverride(d) && !(tokenOnly(d.value) && /var\(--t-/.test(d.value)),
      )
      .map((d) => `${d.file}: ${d.selector} { ${d.prop}: ${d.value} }`);
    expect(stray).toEqual([]);
  });

  it("every keyframe set is gated, or only used by rules that reduce switches off", () => {
    const stray = keyframes
      .filter((k) => !k.gated)
      .filter((k) => {
        const name = k.name.replace(/^@keyframes\s+/, "");
        const users = moving.filter(
          (d) => d.prop.startsWith("animation") && d.value.includes(name),
        );
        return users.length === 0 || users.some((d) => !isGated(d) && !hasOverride(d));
      })
      .map((k) => `${k.file}: ${k.name}`);
    expect(stray).toEqual([]);
  });

  it("the --t-* tokens are zero under reduce", () => {
    const root = readFileSync(path.join(DIR, "globals.css"), "utf8");
    const reduce = /@media \(prefers-reduced-motion: reduce\)\s*\{\s*:root\s*\{([^}]*)\}/.exec(
      root,
    );
    expect(reduce).not.toBeNull();
    for (const t of ["--t-fast", "--t-base", "--t-slow", "--t-land"])
      expect(reduce![1]).toMatch(new RegExp(`${t}:\\s*0ms`));
  });

  it("the run page's motion animates transform and opacity only, once, with no loop", () => {
    const own = keyframes.filter((k) => k.file === "run-motion.css");
    expect(own.length).toBeGreaterThanOrEqual(5); // fault draw x and y, land fade, inspector in and out
    for (const k of own) {
      expect(k.gated, k.name).toBe(true);
      for (const p of k.props) expect(["opacity", "transform"], `${k.name}: ${p}`).toContain(p);
    }
    const own2 = decls.filter((d) => d.file === "run-motion.css" && MOTION_PROPS.test(d.prop));
    expect(own2.length).toBeGreaterThanOrEqual(5);
    for (const d of own2) {
      expect(d.value, d.selector).not.toMatch(/infinite|steps\(|cubic-bezier\((?!0\.2, 0, 0, 1)/);
      expect(d.value, d.selector).not.toMatch(/\btransition\b/);
    }
    expect(own2.every((d) => isGated(d))).toBe(true);
  });

  it("nothing outside the spinner loops, and nothing eases with a spring or a bounce", () => {
    const loops = decls.filter((d) => MOTION_PROPS.test(d.prop) && /infinite/.test(d.value));
    expect(loops.map((d) => d.selector)).toEqual([".spinner"]);
    for (const d of decls.filter((x) => MOTION_PROPS.test(x.prop)))
      expect(d.value).not.toMatch(/cubic-bezier\([^)]*-\d|linear\(/);
  });
});

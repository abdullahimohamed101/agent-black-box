// Design pass 2, step 4: shared helpers for the token pipeline (stdlib only, no dependency).
// tokens.json -> resolve -> CSS (tokens-build.mjs), contrast numbers and report tables (tokens-report.mjs, the vitest suite).
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
export const TOKENS_PATH = path.join(ROOT, "design-tokens/tokens.json");
export const GENERATED_CSS = path.join(ROOT, "src/app/tokens.generated.css");
export const ALIASES_CSS = path.join(ROOT, "src/app/tokens.aliases.css");
export const REPORT_MD = path.join(ROOT, "../../docs/design/pass-2/04-colour-type-tokens.md");

export const loadTokens = () => JSON.parse(readFileSync(TOKENS_PATH, "utf8"));

// ---- colour maths ---------------------------------------------------------------------------------------------------

const lin = (c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const gam = (c) => (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055);

/** CSS Color 4 `oklch(L% C H)` (or `oklch(L C H)`) to a rounded sRGB hex. Out-of-gamut chroma is reduced, hue and lightness kept. */
export function oklchToHex(spec) {
  const m = /^oklch\(\s*([\d.]+)(%?)\s+([\d.]+)\s+([\d.]+)\s*\)$/.exec(spec.trim());
  if (!m) throw new Error(`not an oklch() colour: ${spec}`);
  const L = parseFloat(m[1]) / (m[2] === "%" ? 100 : 1);
  let C = parseFloat(m[3]);
  const h = (parseFloat(m[4]) * Math.PI) / 180;
  const toRgb = (chroma) => {
    const a = chroma * Math.cos(h);
    const b = chroma * Math.sin(h);
    const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
    const mm = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
    const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
    return [
      4.0767416621 * l - 3.3077115913 * mm + 0.2309699292 * s,
      -1.2684380046 * l + 2.6097574011 * mm - 0.3413193965 * s,
      -0.0041960863 * l - 0.7034186147 * mm + 1.707614701 * s,
    ];
  };
  const inGamut = (rgb) => rgb.every((c) => c >= -0.0005 && c <= 1.0005);
  let rgb = toRgb(C);
  for (let i = 0; i < 40 && !inGamut(rgb); i++) {
    C *= 0.95;
    rgb = toRgb(C);
  }
  return (
    "#" +
    rgb
      .map((c) =>
        Math.round(Math.min(1, Math.max(0, gam(Math.min(1, Math.max(0, c))))) * 255)
          .toString(16)
          .padStart(2, "0"),
      )
      .join("")
  );
}

/** Rounded sRGB hex to OKLCH, for the report only (the hex is what is rendered and what contrast is computed on). */
export function hexToOklch(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => lin(parseInt(hex.slice(i, i + 2), 16) / 255));
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  const L = 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s;
  const a = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s;
  const bb = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
  const C = Math.hypot(a, bb);
  const H = ((Math.atan2(bb, a) * 180) / Math.PI + 360) % 360;
  return { L, C, H: C < 0.004 ? 0 : H };
}
export const fmtOklch = ({ L, C, H }) =>
  `oklch(${(L * 100).toFixed(1)}% ${C.toFixed(3)} ${Math.round(H)})`;

export function luminance(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => lin(parseInt(hex.slice(i, i + 2), 16) / 255));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
export function contrast(a, b) {
  const [x, y] = [luminance(a), luminance(b)];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

// ---- token tree -----------------------------------------------------------------------------------------------------

const isLeaf = (n) => n && typeof n === "object" && ("value" in n || "light" in n);

/** Flat list of every leaf: { path: "color.bg.canvas", node }. Keys starting with "$" are metadata, not tokens. */
export function leaves(tokens) {
  const out = [];
  const walk = (node, trail) => {
    for (const [k, v] of Object.entries(node)) {
      if (k.startsWith("$")) continue;
      if (isLeaf(v)) out.push({ path: [...trail, k].join("."), node: v });
      else if (v && typeof v === "object") walk(v, [...trail, k]);
    }
  };
  for (const top of ["color", "type", "space", "radius", "motion", "layout"])
    if (tokens[top]) walk({ [top]: tokens[top] }, []);
  return out;
}

export function lookup(tokens, dotted) {
  let n = tokens;
  for (const k of dotted.split(".")) n = n?.[k];
  return n;
}

/** A theme value of a leaf: "{path}" references are followed until a literal (hex or oklch() resolves to hex). */
export function resolveValue(tokens, raw, theme, depth = 0) {
  if (depth > 8) throw new Error(`reference loop at ${raw}`);
  const ref = /^\{([^}]+)\}$/.exec(raw);
  if (ref) {
    const node = lookup(tokens, ref[1]);
    if (!isLeaf(node)) throw new Error(`unresolved token reference ${raw}`);
    return resolveValue(
      tokens,
      node.light !== undefined ? node[theme] : node.value,
      theme,
      depth + 1,
    );
  }
  return raw.startsWith("oklch(") ? oklchToHex(raw) : raw;
}

export function colorHex(tokens, dotted, theme) {
  const node = lookup(tokens, dotted);
  if (!isLeaf(node)) throw new Error(`no such token: ${dotted}`);
  return resolveValue(tokens, node.light !== undefined ? node[theme] : node.value, theme);
}

export const cssName = (p, node) => {
  if (node.css) return `--${node.css}`;
  return (
    "--" +
    p
      .split(".")
      .filter((s) => s !== "primitive" && s !== "scale")
      .join("-")
  );
};
const refToVar = (tokens, raw) => {
  const m = /^\{([^}]+)\}$/.exec(raw);
  if (!m) return raw.startsWith("oklch(") ? oklchToHex(raw) : raw;
  return `var(${cssName(m[1], lookup(tokens, m[1]))})`;
};

// ---- CSS generation ---------------------------------------------------------------------------------------------------

const HEADER = (what) =>
  `/* generated: do not edit. Source: apps/web/design-tokens/tokens.json (${what}).\n   Regenerate with \`pnpm --filter @abb/web tokens:build\`; \`tokens:check\` fails when this file is stale. */\n`;

export function renderTokensCss(tokens) {
  const all = leaves(tokens);
  const light = [];
  const dark = [];
  const body = [];
  const reduced = [];
  for (const { path: p, node } of all) {
    const name = cssName(p, node);
    if (node.light !== undefined) {
      light.push(`  ${name}: ${refToVar(tokens, node.light)};`);
      if (node.dark !== node.light) dark.push(`  ${name}: ${refToVar(tokens, node.dark)};`);
    } else if (node.scope === "body") body.push(`  ${name}: ${node.value};`);
    else light.push(`  ${name}: ${refToVar(tokens, node.value)};`);
    if (node.reduce !== undefined) reduced.push(`    ${name}: ${node.reduce};`);
  }
  let css = HEADER("primitives, semantic tokens, type, space, radius, motion");
  css += `/* Light is the default; dark follows the system (prefers-color-scheme), exactly as before: the app has no theme switch. */\n`;
  css += `:root {\n${light.join("\n")}\n}\n`;
  css += `@media (prefers-color-scheme: dark) {\n  :root {\n${dark.map((l) => "  " + l).join("\n")}\n  }\n}\n`;
  css += `/* Zero motion under reduce: durations collapse here, and the legacy --t-* tokens do the same in globals.css. */\n`;
  css += `@media (prefers-reduced-motion: reduce) {\n  :root {\n${reduced.join("\n")}\n  }\n}\n`;
  css += `/* Font stacks live on body, not :root: next/font puts its family variables on the body element. */\n`;
  css += `body {\n${body.join("\n")}\n}\n`;
  return css;
}

export function renderAliasesCss(tokens) {
  const root = [];
  const body = [];
  for (const [name, a] of Object.entries(tokens.alias)) {
    const target = a.to ? `var(${cssName(a.to, lookup(tokens, a.to))})` : null;
    if (!target) continue; // status "kept": defined where it always was, nothing to emit
    (a.scope === "body" ? body : root).push(`  --${name}: ${target};`);
  }
  let css = HEADER("alias section");
  css += `/* The pre-pass-2 custom property names, as aliases of the new tokens, so every screen keeps working until step 5 rebuilds it.\n   Status of each name (aliased, kept, deleted in step 5) is listed in docs/design/pass-2/04-colour-type-tokens.md. */\n`;
  css += `:root {\n${root.join("\n")}\n}\nbody {\n${body.join("\n")}\n}\n`;
  return css;
}

// ---- contrast ---------------------------------------------------------------------------------------------------

export const TARGET = {
  text: { aa: 4.5, aaa: 7 },
  large: { aa: 3, aaa: 4.5 },
  ui: { aa: 3, aaa: 4.5 },
};

/** Every pair in tokens.$contrast with both themes' ratios, its minimum (the pair's target) and the AA / AAA verdicts. */
export function computeContrast(tokens) {
  return tokens.$contrast.map((p) => {
    const kind = p.kind ?? "text";
    const ratio = {};
    for (const theme of ["light", "dark"])
      ratio[theme] = contrast(colorHex(tokens, p.fg, theme), colorHex(tokens, p.bg, theme));
    const min = p.min ?? (p.target === "AAA" ? TARGET[kind].aaa : TARGET[kind].aa);
    return { ...p, kind, ratio, min, aa: TARGET[kind].aa, aaa: TARGET[kind].aaa };
  });
}

// ---- red-ish detection (the "one red line" rule) ------------------------------------------------------------------------

/** Hue 340..40 degrees with visible chroma: the family the direction reserves for the fault line and the failed word. */
export function isRedish(hex) {
  const { C, H } = hexToOklch(hex);
  return C >= 0.08 && (H >= 340 || H <= 40);
}

export const themes = ["light", "dark"];

// ---- report (docs/design/pass-2/04-colour-type-tokens.md) ------------------------------------------------------------------

const px = (rem) => `${parseFloat(rem) * 16}px`;
const row = (cells) => `| ${cells.join(" | ")} |`;
const table = (head, rows) => [row(head), row(head.map(() => "---")), ...rows.map(row)].join("\n");

function walkFiles(dir, exts, out = []) {
  for (const f of readdirSync(dir)) {
    if (f === "node_modules" || f === ".next" || f === "test-results") continue;
    const p = path.join(dir, f);
    if (statSync(p).isDirectory()) walkFiles(p, exts, out);
    else if (exts.some((e) => f.endsWith(e))) out.push(p);
  }
  return out;
}

/** Every custom property DEFINED in src/app/*.css (outside the generated files) and how many `var(--name)` readers exist. */
export function customPropertyInventory() {
  const defs = new Set();
  const cssDir = path.join(ROOT, "src/app");
  for (const f of readdirSync(cssDir).filter((x) => x.endsWith(".css"))) {
    if (f.startsWith("tokens.")) continue;
    const src = readFileSync(path.join(cssDir, f), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    for (const m of src.matchAll(/(?:^|[;{\s])--([a-z0-9-]+)\s*:/g)) defs.add(m[1]);
  }
  const files = [
    ...walkFiles(path.join(ROOT, "src"), [".css", ".ts", ".tsx"]),
    ...walkFiles(path.join(ROOT, "e2e"), [".ts"]),
    ...walkFiles(path.join(ROOT, "tests"), [".ts", ".tsx"]),
  ].filter((f) => !/tokens\.(generated|aliases)\.css$/.test(f));
  const text = files.map((f) => readFileSync(f, "utf8")).join("\n");
  const readers = (name) => text.split(`var(--${name}`).length - 1;
  return { defs: [...defs].sort(), readers };
}

export function renderSections(tokens) {
  const all = leaves(tokens);
  const hexOf = (p, theme) => colorHex(tokens, p, theme);
  const sec = {};

  // primitives
  const prim = all.filter((l) => l.path.startsWith("color.primitive."));
  sec.primitives = table(
    ["Token", "OKLCH", "Hex (rendered)", "Source"],
    prim.map(({ path: p, node }) => {
      const hex = resolveValue(tokens, node.value, "light");
      const authored = node.value.startsWith("oklch(");
      return [
        `\`${p}\``,
        `\`${authored ? node.value : fmtOklch(hexToOklch(hex))}\``,
        `\`${hex}\``,
        authored ? "authored in OKLCH" : "literal hex kept (ADR-111 / direction)",
      ];
    }),
  );

  // semantic map
  const sem = all.filter(
    (l) => l.path.startsWith("color.") && !l.path.startsWith("color.primitive.") && l.node.light,
  );
  const refName = (raw) => {
    const m = /^\{(.+)\}$/.exec(raw);
    return m ? `${m[1].replace("color.primitive.", "")} ` : "";
  };
  sec.semantic = table(
    ["Token", "CSS variable", "Light", "Dark", "Use"],
    sem.map(({ path: p, node }) => [
      `\`${p}\``,
      `\`${cssName(p, node)}\``,
      `${refName(node.light)}\`${resolveValue(tokens, node.light, "light")}\``,
      node.dark === node.light && !node.light.startsWith("{color.primitive")
        ? "same"
        : `${refName(node.dark)}\`${resolveValue(tokens, node.dark, "dark")}\``,
      node.use ?? "",
    ]),
  );

  // contrast
  const pairs = computeContrast(tokens);
  const decorative = (p) => p.min < p.aa;
  const verdict = (p) => {
    if (decorative(p)) return "n/a (decorative)";
    const l = p.ratio.light >= p.aa;
    const d = p.ratio.dark >= p.aa;
    return l && d
      ? "pass"
      : `${l ? "" : "light FAIL"}${!l && !d ? ", " : ""}${d ? "" : "dark FAIL"}`;
  };
  const aaa = (p) => {
    if (decorative(p)) return "n/a";
    const l = p.ratio.light >= p.aaa;
    const d = p.ratio.dark >= p.aaa;
    return l && d ? "yes" : l ? "light only" : d ? "dark only" : "no";
  };
  sec.contrast = table(
    [
      "Foreground on background",
      "Light",
      "Dark",
      "Target (the test fails below it)",
      "Meets AA",
      "Meets AAA",
      "Used for",
    ],
    pairs.map((p) => [
      `\`${p.fg.replace("color.", "")}\` on \`${p.bg.replace("color.", "")}\`${p.legacy ? " (legacy)" : ""}`,
      p.ratio.light.toFixed(2),
      p.ratio.dark.toFixed(2),
      `${p.min}:1 (${decorative(p) ? "decorative floor" : p.target}${p.kind === "ui" ? ", graphics" : ""})`,
      verdict(p),
      aaa(p),
      p.use,
    ]),
  );

  // type
  const scale = tokens.type.scale;
  sec.type = table(
    ["Step", "Size / line", "rem", "Weight", "Tracking", "Family", "Use"],
    Object.keys(scale)
      .filter((k) => !k.startsWith("$"))
      .map((k) => {
        const s = scale[k];
        const w = lookup(tokens, /^\{(.+)\}$/.exec(s.weight.value)[1]).value;
        return [
          `\`${k}\``,
          `${px(s.size.value)} / ${px(s.line.value)}`,
          `${s.size.value} / ${s.line.value}`,
          w,
          "0",
          scale.$families[k] === "mono" ? "Plex Mono" : "Plex Sans",
          s.$use ?? "",
        ];
      }),
  );

  // aliases
  const inv = customPropertyInventory();
  const aliasNames = Object.keys(tokens.alias);
  const rows = [];
  const counts = { aliased: 0, kept: 0, delete: 0 };
  for (const name of aliasNames) {
    const a = tokens.alias[name];
    counts[a.status]++;
    rows.push([
      `\`--${name}\``,
      a.status === "aliased" ? "aliased" : a.status === "kept" ? "kept" : "deleted in step 5",
      a.to ? `\`${cssName(a.to, lookup(tokens, a.to))}\`` : "(defined in globals.css, unchanged)",
      String(inv.readers(name)),
      a.note ?? "",
    ]);
  }
  for (const name of inv.defs.filter((d) => !aliasNames.includes(d))) {
    counts.kept++;
    rows.push([`\`--${name}\``, "kept", "(unchanged)", String(inv.readers(name)), dataNote(name)]);
  }
  sec.aliases = `${counts.aliased + counts.delete + counts.kept} old custom properties: ${counts.aliased} aliased to a faithful new token, ${counts.kept} kept unchanged, ${counts.delete} to be deleted in step 5 (their role is removed, or they alias a removed role).\n\n${table(["Old name", "Status", "Now", "Readers", "Note"], rows)}`;
  return sec;
}

function dataNote(name) {
  if (name.startsWith("wf-a") || name.startsWith("wf-"))
    return name.endsWith("-w")
      ? "layout width"
      : "waterfall data colour (ADR-071); desaturation and token move in step 5";
  if (name === "tone" || name === "tone-bg") return "component-local, set per state";
  if (name.startsWith("t-")) return "motion contract duration";
  return "";
}

const marker = (name, which) => `<!-- tokens:report:${name}:${which} -->`;
/** Replace the text between each pair of markers with its generated section; throws when a marker pair is missing. */
export function applySections(md, sections) {
  let out = md;
  for (const [name, body] of Object.entries(sections)) {
    const a = out.indexOf(marker(name, "start"));
    const b = out.indexOf(marker(name, "end"));
    if (a < 0 || b < a)
      throw new Error(`missing marker pair for section "${name}" in ${REPORT_MD}`);
    out = out.slice(0, a + marker(name, "start").length) + "\n" + body + "\n" + out.slice(b);
  }
  return out;
}

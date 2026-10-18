// Informational report: class selectors in src/app/*.css that no .ts or .tsx file under src mentions (a class name appearing in a
// string, template or the copy modules counts as a reader). Nothing is deleted by this script; each hit is checked by hand
// (dynamic names such as `tl-${kind}` are listed under "built from a prefix" so they are not mistaken for dead).
// Usage: node scripts/dead-css.mjs
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

const root = path.join(import.meta.dirname, "../src");
const walk = (d, out = []) => {
  for (const f of readdirSync(d)) {
    const p = path.join(d, f);
    if (statSync(p).isDirectory()) walk(p, out);
    else out.push(p);
  }
  return out;
};
const files = walk(root);
const code = files
  .filter((f) => /\.tsx?$/.test(f))
  .map((f) => readFileSync(f, "utf8"))
  .join("\n");
const hits = [];
for (const f of files.filter((f) => f.endsWith(".css") && !/tokens\./.test(f))) {
  const css = readFileSync(f, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  const seen = new Set();
  for (const m of css.matchAll(/\.([a-zA-Z][\w-]*)/g)) {
    const name = m[1];
    if (seen.has(name) || /^\d/.test(name)) continue;
    seen.add(name);
    if (new RegExp(`(?<![\\w-])${name}(?![\\w-])`).test(code)) continue;
    const prefix = name.replace(/[-_][^-_]*$/, "");
    const dynamic = prefix !== name && code.includes(`${prefix}-\${`);
    hits.push([path.relative(root, f), name, dynamic ? "built from a prefix" : "no reader"]);
  }
}
for (const [f, n, why] of hits.sort()) console.log(`${f}\t.${n}\t${why}`);
console.log(`\n${hits.length} class names with no literal reader in src/**/*.ts(x).`);

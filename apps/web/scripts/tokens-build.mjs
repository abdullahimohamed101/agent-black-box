// Generates src/app/tokens.generated.css from design-tokens/tokens.json (the aliases file is gone since step 5d: no old name is left).
//   node scripts/tokens-build.mjs          write the file
//   node scripts/tokens-build.mjs --check  exit 1 when it differs from what tokens.json produces
import { readFileSync, writeFileSync } from "node:fs";
import { GENERATED_CSS, loadTokens, renderTokensCss } from "./tokens-lib.mjs";

const tokens = loadTokens();
const outputs = [[GENERATED_CSS, renderTokensCss(tokens)]];
const check = process.argv.includes("--check");
let stale = 0;
for (const [file, css] of outputs) {
  let current = "";
  try {
    current = readFileSync(file, "utf8");
  } catch {
    /* missing counts as stale */
  }
  if (check) {
    if (current !== css) {
      stale++;
      console.error(`stale: ${file} (run \`pnpm --filter @abb/web tokens:build\`)`);
    }
  } else writeFileSync(file, css);
}
if (check && stale) process.exit(1);
if (!check) console.log(`wrote ${outputs.length} file`);

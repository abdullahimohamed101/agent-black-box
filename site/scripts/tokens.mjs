// Writes the site's two generated inputs from the app's single sources (Phase 20, H2):
//   src/styles/tokens.css   <- apps/web/design-tokens/tokens.json through the app's own generator (renderTokensCss)
//   src/fonts/*, public/fonts/OFL.txt <- apps/web/src/fonts (IBM Plex, SIL OFL 1.1; the site uses three of the app's four files)
//   node scripts/tokens.mjs           write them
//   node scripts/tokens.mjs --check   exit 1 when a committed copy differs from its source
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { loadTokens, renderTokensCss } from "../../apps/web/scripts/tokens-lib.mjs";

const SITE = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const FONT_SRC = path.join(SITE, "../apps/web/src/fonts");
const FONTS = ["IBMPlexSans-Regular-Latin1.woff2", "IBMPlexSans-SemiBold-Latin1.woff2", "IBMPlexMono-Regular-Latin1.woff2"];
const check = process.argv.includes("--check");

const outputs = [
  [path.join(SITE, "src/styles/tokens.css"), renderTokensCss(loadTokens())],
  ...FONTS.map((f) => [path.join(SITE, "src/fonts", f), readFileSync(path.join(FONT_SRC, f))]),
  [path.join(SITE, "src/fonts/OFL.txt"), readFileSync(path.join(FONT_SRC, "OFL.txt"))],
  [path.join(SITE, "public/fonts/OFL.txt"), readFileSync(path.join(FONT_SRC, "OFL.txt"))],
];
let stale = 0;
for (const [file, data] of outputs) {
  const want = Buffer.from(data);
  if (check) {
    if (!existsSync(file) || !readFileSync(file).equals(want)) {
      stale++;
      console.error(`stale: ${path.relative(SITE, file)} (run \`pnpm --filter @abb/site sync\`)`);
    }
  } else {
    mkdirSync(path.dirname(file), { recursive: true });
    writeFileSync(file, want);
  }
}
if (check && stale) process.exit(1);
console.log(check ? "site inputs are fresh" : `wrote ${outputs.length} files`);

// Regenerates the tables between the <!-- tokens:report:NAME:start/end --> markers in
// docs/design/pass-2/04-colour-type-tokens.md from tokens.json (computed contrast, not estimated).
//   node scripts/tokens-report.mjs          rewrite the tables
//   node scripts/tokens-report.mjs --check  exit 1 when the committed tables are stale
import { readFileSync, writeFileSync } from "node:fs";
import {
  REPORT_MD,
  applySections,
  computeContrast,
  loadTokens,
  renderSections,
} from "./tokens-lib.mjs";

const tokens = loadTokens();
const current = readFileSync(REPORT_MD, "utf8");
const next = applySections(current, renderSections(tokens));
if (process.argv.includes("--check")) {
  if (next !== current) {
    console.error(
      "stale: docs/design/pass-2/04-colour-type-tokens.md (run `pnpm --filter @abb/web tokens:report`)",
    );
    process.exit(1);
  }
} else {
  writeFileSync(REPORT_MD, next);
  const failing = computeContrast(tokens).filter(
    (p) => p.ratio.light < p.min || p.ratio.dark < p.min,
  );
  console.log(`report written; ${failing.length} pair(s) below target`);
  if (failing.length) process.exit(1);
}

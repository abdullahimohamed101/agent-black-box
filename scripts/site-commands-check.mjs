// The quickstart commands the page shows must be the commands docs/quickstart.md shows (one source, AC12). While that document is
// not on the branch the check says so and passes; the page carries a [CONFIRM AFTER STEP 14] marker until it is, and
// check-site.sh --release fails on it.
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const src = readFileSync(path.join(root, "site/src/content/commands.ts"), "utf8");
const cmds = [...src.matchAll(/\{ cmd: ([`"])(.+?)\1/g)].map((m) => m[2].replace("${REPO_URL}", "https://github.com/abdullahimohamed101/agent-black-box"));
if (cmds.length !== 5) { console.error(`FAIL: expected 5 commands in site/src/content/commands.ts, found ${cmds.length}`); process.exit(1); }
const doc = path.join(root, "docs/quickstart.md");
if (!existsSync(doc)) {
  console.log("docs/quickstart.md is not on this branch: command equality UNVERIFIED (the page keeps its [CONFIRM AFTER STEP 14] marker)");
  process.exit(0);
}
const text = readFileSync(doc, "utf8");
const missing = cmds.filter((c) => !text.includes(c.replace(/\.git$/, "")));
if (missing.length) { console.error(`FAIL: docs/quickstart.md does not contain: ${missing.join(" | ")}`); process.exit(1); }
console.log(`all ${cmds.length} commands are in docs/quickstart.md`);

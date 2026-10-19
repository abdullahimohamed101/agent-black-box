// Browser checks of a BUILT landing page (Phase 20, brief section 9). One subcommand per concern; scripts/check-site.sh runs them all.
//   node scripts/site-checks.mjs <nojs|copy|network|a11y|motion|perf|shots> DIR [--base /agent-black-box]
// DIR is any static export with an index.html. It is served on a loopback port under --base (default /agent-black-box, as GitHub
// Pages serves a project page; "" serves it at the root). Each check is scripts/site-checks/<name>.mjs (default export).
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { launch, parseArgs, Report, serve } from "./site-lib.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const args = parseArgs(process.argv.slice(2));
const file = path.join(here, "site-checks", `${args.cmd}.mjs`);
if (!args.cmd || !existsSync(file)) {
  console.error("usage: site-checks.mjs <nojs|copy|network|a11y|motion|perf|shots> DIR [--base PATH]");
  process.exit(2);
}
const { default: check } = await import(file);
const srv = await serve(args.dir, args.base);
const browser = await launch();
const rep = new Report();
try {
  await check({ ...args, srv, browser, rep });
} finally {
  await browser.close();
  await srv.close();
}
rep.finish(args.cmd);

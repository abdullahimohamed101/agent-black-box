// C3: the page is real HTML. With JavaScript disabled the hero, the trace rows, the quickstart commands and (as they land) the
// gallery and FAQ are all in the DOM, and nothing waits for hydration.
import { readFileSync } from "node:fs";
const PANELS = JSON.parse(readFileSync(new URL("../../site/screenshots.json", import.meta.url), "utf8")).panels.length; // the manifest decides how many views there are
export default async function nojs({ srv, browser, rep }) {
  const context = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await page.goto(srv.url, { waitUntil: "load" });
  const text = (sel) => page.locator(sel).first().innerText();
  const count = (sel) => page.locator(sel).count();

  const h1 = await text("h1");
  if (!/See what your agent actually did\./.test(h1)) rep.fail("nojs", `h1 is ${JSON.stringify(h1)}`);
  const rows = await count('[role="option"]');
  if (rows < 9) rep.fail("nojs", `${rows} trace rows, expected at least 9`);
  if ((await count('[role="option"][aria-selected="true"]')) !== 1) rep.fail("nojs", "the first-error row is not selected in the static HTML");
  if (!(await text("#trace-inspector")).includes("run_migration failed: ConnectionTimeout")) rep.fail("nojs", "the inspector text is missing");
  const cmds = await count("#cmd-quickstart .ln");
  if (cmds !== 5) rep.fail("nojs", `${cmds} quickstart command lines, expected 5`);
  if (!(await text("#cmd-quickstart")).includes("make demo")) rep.fail("nojs", "make demo is not in the quickstart block");
  if (!(await count("#cmd-sdk .ln"))) rep.fail("nojs", "the SDK snippet is missing");
  const imgs = await count("#screens .panel img");
  if (imgs !== PANELS) rep.fail("nojs", `${imgs} gallery images, expected ${PANELS}`);
  if ((await count("#screens .panel[hidden]")) !== 0) rep.fail("nojs", "a gallery panel is hidden without script");
  if ((await count('#screens [role="tab"]')) !== 0 || (await count("#screens a.tab")) !== PANELS) rep.fail("nojs", `without script the tab list must be ${PANELS} in-page links`);
  const copy = page.locator("button.copy").first();
  if (!(await copy.isDisabled())) rep.fail("nojs", "the Copy button must be disabled without script");
  rep.fact(`h1, ${rows} trace rows, ${cmds} command lines, inspector, SDK snippet and ${imgs} gallery images are in the DOM with scripting off`);
  await context.close();
}

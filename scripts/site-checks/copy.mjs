// The Copy button writes the commands without prompts or notes, and says so.
import { fileURLToPath } from "node:url";
import { readFileSync } from "node:fs";
import path from "node:path";

export default async function copy({ srv, browser, rep }) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, permissions: ["clipboard-read", "clipboard-write"] });
  const page = await context.newPage();
  await page.goto(srv.url, { waitUntil: "networkidle" });
  const block = page.locator("figure.code", { has: page.locator("#cmd-quickstart") });
  const button = block.locator("button.copy");
  await button.waitFor();
  if (await button.isDisabled()) rep.fail("copy", "the Copy button is still disabled after hydration");
  await button.click();
  try {
    await block.getByRole("button", { name: "Copied" }).waitFor({ timeout: 3000 });
  } catch {
    rep.fail("copy", "the button never read Copied after a click");
  }
  const got = await page.evaluate(() => navigator.clipboard.readText());
  const lines = got.split("\n");
  if (lines.length !== 5) rep.fail("copy", `${lines.length} lines copied, expected 5`);
  if (lines.some((l) => l.startsWith("$") || l.includes("#"))) rep.fail("copy", "a prompt or a note was copied");
  if (!lines.at(-1)?.startsWith("make demo")) rep.fail("copy", `last copied line is ${JSON.stringify(lines.at(-1))}`);
  if ((await block.locator('[role="status"]').innerText()) !== "Copied to the clipboard") rep.fail("copy", "no status announcement");
  // The one source: the commands the page copies are the commands in docs/quickstart.md, when that document exists.
  const repo = path.join(path.dirname(fileURLToPath(import.meta.url)), "../..");
  try {
    const doc = readFileSync(path.join(repo, "docs/quickstart.md"), "utf8");
    for (const l of lines) if (!doc.includes(l.replace(/^git clone .*/, "git clone"))) rep.fail("copy", `docs/quickstart.md does not contain ${JSON.stringify(l)}`);
  } catch {
    rep.fact("docs/quickstart.md is not on this branch: command equality UNVERIFIED (marker AFTER STEP 14 stays in the export)");
  }
  rep.fact(`copied ${lines.length} lines, no prompts or notes, status announced`);
  // Fallback without the Clipboard API: the block is selected instead.
  const ctx2 = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const p2 = await ctx2.newPage();
  await p2.addInitScript(() => Object.defineProperty(navigator, "clipboard", { value: undefined }));
  await p2.goto(srv.url, { waitUntil: "networkidle" });
  const b2 = p2.locator("figure.code", { has: p2.locator("#cmd-quickstart") }).locator("button.copy");
  await b2.click();
  const selected = await p2.evaluate(() => window.getSelection()?.toString() ?? "");
  if (!selected.includes("make demo")) rep.fail("copy", "without the Clipboard API the block was not selected");
  await context.close();
  await ctx2.close();
}

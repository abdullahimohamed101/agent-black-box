import { expect, test } from "@playwright/test";
import { BASE, RUN, VIEWER, enterReplay, figure, openRun, scrub, seekTo } from "./replay";
import { wrongFixCodingRun } from "../src/fixtures/codingRuns";

// Phase 12 review fixes (fixtures build), in a real browser: a viewer's replay of a run that edited two files ends on
// the header's own "Files changed" (it used to say 1), says "up to" before that, and shows no path. The owner is the
// control. Expected values come from the fixture events.
const WRONG_FIX = RUN(12);
const wrong = wrongFixCodingRun();
const paths = [
  ...new Set(
    wrong.events
      .filter((e) => e.event_type === "file.modified")
      .map((e) => String(e.attributes["file.path"])),
  ),
];
test.use({ viewport: { width: 1440, height: 900 } });

for (const [role, base] of [
  ["viewer", VIEWER],
  ["owner", BASE],
] as const) {
  test(`the ${role}'s replay ends on the header's Files changed and Cost`, async ({ page }) => {
    expect(paths.length).toBe(2);
    await openRun(page, base, WRONG_FIX);
    const live = { files: await figure(page, "Files changed"), cost: await figure(page, "Cost") };
    expect(live.files).toBe("2");
    await enterReplay(page);
    const last = Number(await scrub(page).getAttribute("max"));
    const seen: string[] = [];
    for (let i = 0; i <= last; i++) {
      await seekTo(page, i);
      seen.push(await figure(page, "Files changed"));
      if (role === "viewer") {
        const text = await page.evaluate(() => document.body.innerText + document.body.textContent);
        for (const p of paths) expect(text, `leaked at ${i}`).not.toContain(p);
      }
    }
    expect(seen.at(-1)).toBe(live.files);
    expect(await figure(page, "Cost")).toBe(live.cost);
    if (role === "viewer") expect(seen.some((f) => f.startsWith("up to "))).toBe(true);
    else expect(seen.some((f) => f.startsWith("up to "))).toBe(false);
  });
}

import { expect, test } from "@playwright/test";

// The Evidence view on the 10,000-event run with an artifact on every file read: the table holds bounded rows while it
// scrolls, the region is still a keyboard tab stop and Open still opens the inspector. Fixtures build.
const BASE = "/w/demo/projects/all";
const RUN6 = `run_${"6".padStart(26, "0")}`;

test.use({ viewport: { width: 1440, height: 900 } });

test("evidence of a 10,000-event run: bounded rows, scrolls, keyboard and Open work", async ({
  page,
}) => {
  await page.route(/\/v1\/runs\/[^/]+\/events(\?|$)/, async (route) => {
    const res = await route.fetch();
    if (!res.ok()) return route.fulfill({ response: res });
    const body = await res.json();
    let k = 0;
    for (const e of body.items ?? []) {
      if (e.event_type === "file.read")
        e.attributes["diff.artifact"] = `artifact://art_${String(++k).padStart(26, "0")}`;
    }
    return route.fulfill({ response: res, json: body });
  });
  await page.goto(`${BASE}/runs/${RUN6}?view=evidence`);
  const region = page.getByRole("region", { name: "Captured artifacts" });
  await expect(region.getByRole("row").nth(1)).toBeVisible();
  await expect(page.getByTestId("progress")).toContainText(/\d[\d,]* artifacts in 10,000 events/, {
    timeout: 30_000,
  });
  const total = Number(
    ((await page.getByTestId("progress").innerText()).match(/^([\d,]+) artifacts/) ??
      [])[1]?.replace(/,/g, ""),
  );
  expect(total).toBeGreaterThan(1000);
  expect(await region.getByRole("row").count()).toBeLessThan(80);
  expect(await page.locator(".evidence tr").count()).toBeLessThan(90);

  await region.focus();
  await expect(region).toBeFocused();
  await region.evaluate((el) => (el.scrollTop = el.scrollHeight));
  await expect(region.getByRole("row").last()).toBeVisible();
  expect(await page.locator(".evidence tr").count()).toBeLessThan(90);
  expect(await region.getByRole("button", { name: /^Open diff/ }).count()).toBeLessThan(80);

  await region
    .getByRole("button", { name: /^Open diff/ })
    .last()
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(region).toBeFocused();
});

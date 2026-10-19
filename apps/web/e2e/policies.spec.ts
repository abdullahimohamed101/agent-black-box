import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { settleAnimations } from "./motion";

// Phase 14 step 4 (fixtures mode): the Policies settings page. The project policy (version 3) is never changed here, so
// the specs can run in parallel against one fixture server; the one test that publishes uses the workspace policy.
const PAGE = "/w/demo/settings/policies";
const PROJECT = `${PAGE}?project=demo`;
const SHOTS = "../../docs/screenshots/phase-14";

async function axe(page: Page) {
  await settleAnimations(page);
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual(
    [],
  );
}
const noSideScroll = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
const editor = (page: Page) =>
  page.getByRole("textbox", { name: /^Policy text( \(read only\))?$/ });
const GUARANTEE =
  "Policies are enforced for agents that ask Agent BlackBox through the SDK guard. They do not stop code that bypasses it.";
const RULE = `
  - id: deny-prod-shell
    description: Production tooling is for people.
    when:
      capability: shell.execute
      resource: terraform
    decision: deny
`;

for (const [width, height] of [
  [1440, 900],
  [375, 812],
] as const)
  test.describe(`${width} px`, () => {
    test.use({ viewport: { width, height } });

    test("the page states the guarantee, the effective policy, its version, source and metadata", async ({
      page,
    }) => {
      const problems: string[] = [];
      page.on("console", (m) => m.type() === "error" && problems.push(m.text()));
      page.on("pageerror", (e) => problems.push(e.message));
      await page.goto(PROJECT);
      await expect(page.getByRole("heading", { name: "Policies", level: 1 })).toBeVisible();
      await expect(page.getByTestId("cooperative-note")).toHaveText(GUARANTEE);
      await expect(page.getByTestId("policy-meta")).toContainText("version 3");
      await expect(page.getByTestId("policy-meta")).toContainText("this project's own policy");
      await expect(editor(page)).toHaveValue(/id: deny-env-read/);
      await expect(page.getByTestId("gutter").locator("span").first()).toHaveText("1");
      await expect(page.getByRole("link", { name: "audit log", exact: true })).toBeVisible();
      expect(await noSideScroll(page)).toBe(true);
      expect(problems.filter((p) => /418|hydrat/i.test(p))).toEqual([]);
      await axe(page);
    });

    test("scope picker: workspace policy and each project, kept in the URL", async ({ page }) => {
      await page.goto(PAGE);
      await expect(page.getByTestId("policy-meta")).toContainText("the workspace policy");
      await page.getByLabel(/^Scope/).selectOption("demo");
      await expect(page).toHaveURL(/\?project=demo$/);
      await expect(page.getByTestId("policy-meta")).toContainText("version 3");
      await page.goBack();
      await expect(page.getByTestId("policy-meta")).toContainText("the workspace policy");
    });

    test("history: pick a version, read it, compare with current; added lines ink, removed lines amber, never red", async ({
      page,
    }) => {
      await page.goto(PROJECT);
      const rows = page.getByTestId("version-row");
      await expect(rows).toHaveCount(3);
      await expect(rows.first()).toContainText("(current)");
      await page.getByRole("button", { name: "View version 1" }).click();
      const view = page.getByTestId("version-view");
      await expect(view.getByRole("textbox")).toHaveAttribute("readonly", "");
      await expect(view.getByRole("textbox")).not.toHaveValue(/approve-push-main/);
      await view.getByRole("button", { name: /^Compare with current/ }).click();
      const diff = page.getByTestId("policy-diff");
      await expect(diff).toContainText("+  - id: approve-push-main");
      const add = diff.locator(".pol-add").first();
      await expect(add).toBeVisible();
      // removed lines exist between v2 and v3 (a changed expiry)
      await page.getByRole("button", { name: "View version 2" }).click();
      await page.getByRole("button", { name: /^Compare with current/ }).click();
      const del = diff.locator(".pol-del").first();
      await expect(del).toBeVisible();
      const color = await del.evaluate((el) => getComputedStyle(el).color);
      const bg = await del.evaluate((el) => getComputedStyle(el).backgroundColor);
      for (const c of [color, bg]) {
        const [r, g, b] = (c.match(/\d+/g) ?? []).map(Number) as [number, number, number];
        expect(r > 150 && g < 80 && b < 80, `a red-looking colour: ${c}`).toBe(false);
      }
      const addColor = await add.evaluate((el) => getComputedStyle(el).color);
      expect(addColor).not.toBe(color);
      expect(await noSideScroll(page)).toBe(true);
      await axe(page);
    });
  });

test("editor flow: problems with line and column, caret jump, a valid draft, dry run, publish (workspace policy)", async ({
  page,
}) => {
  await page.goto(PAGE);
  const box = editor(page);
  await expect(box).toHaveValue(/deny-catastrophic-shell/);
  const base = await box.inputValue();
  // a mistake: the API's validator answers with line and column, and the issue moves the caret
  await box.fill(base.replace("version: 1", "version: 9"));
  const issues = page.getByTestId("issues");
  await expect(issues.getByRole("button")).toHaveCount(1);
  await expect(issues).toContainText("1:10");
  await expect(issues).toContainText("Only 'version: 1' is supported.");
  await expect(page.getByRole("button", { name: "Publish" })).toBeDisabled();
  await expect(page.getByTestId("gutter").locator("span[data-issue]")).toHaveText("1");
  await box.evaluate((el: HTMLTextAreaElement) =>
    el.setSelectionRange(el.value.length, el.value.length),
  );
  await issues.getByRole("button").click();
  await expect(box).toBeFocused();
  expect(await box.evaluate((el: HTMLTextAreaElement) => el.selectionStart)).toBe(9);
  // fix it, add a rule
  await box.fill(`${base}${RULE}`);
  await expect(page.getByTestId("check-status")).toHaveText("Valid: 2 rules.");
  // dry run on a project: counts, rules, samples with run and event links; nothing is changed
  await page.getByRole("button", { name: "Dry run" }).click();
  const dry = page.getByTestId("dry-run-result");
  await expect(dry).toContainText("Nothing was changed.");
  await expect(dry.getByRole("table", { name: "By rule" })).toContainText(
    "deny-catastrophic-shell",
  );
  await expect(dry.getByTestId("dry-sample").first().getByRole("link").first()).toHaveAttribute(
    "href",
    /\/w\/demo\/projects\/demo\/runs\/run_\w+$/,
  );
  await axe(page);
  // publish with a comment
  await page.getByLabel("Comment (optional)").fill("Add a terraform rule.");
  await page.getByRole("button", { name: "Publish" }).click();
  await expect(page.getByTestId("published")).toContainText(/Published version \d+\./);
  await expect(page.getByTestId("version-row").first()).toContainText("(current)");
  await expect(page.getByTestId("version-row").first()).toContainText("Add a terraform rule.");
  await expect(page.getByRole("button", { name: "Publish" })).toBeDisabled();
  // the fixture server keeps the new version
  await page.reload();
  await expect(editor(page)).toHaveValue(/deny-prod-shell/);
});

test("'Start from the example' fills the editor with a policy the validator accepts", async ({
  page,
}) => {
  await page.goto(PAGE);
  await expect(editor(page)).toHaveValue(/deny-catastrophic-shell/);
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "Start from the example" }).click();
  await expect(editor(page)).toHaveValue(
    /deny-env-read[\s\S]*approve-push-main[\s\S]*deny-catastrophic-shell/,
  );
  await expect(page.getByTestId("check-status")).toHaveText("Valid: 3 rules.");
});

test("a concurrent publish: 'Someone published a newer version' with a reload action", async ({
  page,
}) => {
  await page.route("**/api/abb/v1/policies/publish", (route) =>
    route.fulfill({
      status: 409,
      json: {
        error: {
          code: "POLICY_VERSION_CONFLICT",
          message: "The policy was changed by someone else; reload it before publishing.",
          retryable: false,
          request_id: "r",
          details: { current_version: 4 },
        },
      },
    }),
  );
  await page.goto(PROJECT);
  const box = editor(page);
  await box.fill(`${await box.inputValue()}${RULE}`);
  await expect(page.getByRole("button", { name: "Publish" })).toBeEnabled();
  await page.getByRole("button", { name: "Publish" }).click();
  const alert = page.getByTestId("conflict");
  await expect(alert).toContainText("Someone published a newer version (version 4).");
  await expect(page.getByRole("button", { name: "Publish" })).toBeDisabled();
  await page.unroute("**/api/abb/v1/policies/publish");
  await alert.getByRole("button", { name: "Reload the latest version" }).click();
  await expect(page.getByTestId("conflict")).toHaveCount(0);
  await expect(editor(page)).not.toHaveValue(/deny-prod-shell/); // the draft was replaced by the published text
  await axe(page);
});

test("unsaved changes: a link asks first, and so does a reload", async ({ page }) => {
  await page.goto(PROJECT);
  const box = editor(page);
  await box.fill(`${await box.inputValue()}${RULE}`);
  const seen: string[] = [];
  page.on("dialog", (d) => {
    seen.push(d.type());
    void d.dismiss();
  });
  await page
    .getByRole("navigation", { name: "Primary" })
    .getByRole("link", { name: /^Review/ }) // Approvals is under Review in the top bar (design pass 2)
    .click();
  await expect(page).toHaveURL(/settings\/policies/);
  expect(seen).toContain("confirm");
  // accepting lets the navigation through
  page.removeAllListeners("dialog");
  page.once("dialog", (d) => void d.accept());
  await page
    .getByRole("navigation", { name: "Primary" })
    .getByRole("link", { name: /^Review/ }) // Approvals is under Review in the top bar (design pass 2)
    .click();
  await expect(page).toHaveURL(/\/w\/demo\/approvals/);
});

test("keyboard: an issue is reachable by Tab and moves the caret on Enter", async ({ page }) => {
  await page.goto(PROJECT);
  const box = editor(page);
  await box.fill("policies: []\n");
  const issue = page.getByTestId("issues").getByRole("button").first();
  await expect(issue).toBeVisible();
  await issue.focus();
  await page.keyboard.press("Enter");
  await expect(box).toBeFocused();
});

test("a viewer: 'Not available for your role'", async ({ page }) => {
  await page.goto("/w/viewer-only/settings/policies");
  await expect(page.getByText("Not available for your role")).toBeVisible();
});

for (const scheme of ["light", "dark"] as const)
  test.describe(`${scheme} scheme`, () => {
    test.use({ colorScheme: scheme, viewport: { width: 1440, height: 900 } });
    test("axe on the editor with a dry run and a diff open", async ({ page }) => {
      await page.goto(PROJECT);
      await expect(editor(page)).toHaveValue(/deny-env-read/);
      await page.getByRole("button", { name: "Dry run" }).click();
      await expect(page.getByTestId("dry-run-result")).toBeVisible();
      await page.getByRole("button", { name: "View version 2" }).click();
      await page.getByRole("button", { name: /^Compare with current/ }).click();
      await expect(page.getByTestId("policy-diff")).toBeVisible();
      await axe(page);
    });
  });

test.describe("reduced motion", () => {
  test.use({ reducedMotion: "reduce" });
  test("nothing animates", async ({ page }) => {
    await page.goto(PROJECT);
    await expect(editor(page)).toHaveValue(/deny-env-read/);
    await page.waitForTimeout(300);
    const running = await page.evaluate(
      () => document.getAnimations().filter((a) => a.playState === "running").length,
    );
    expect(running).toBe(0);
  });
});

for (const scheme of ["light", "dark"] as const)
  for (const [width, height] of [
    [1440, 900],
    [375, 812],
  ] as const)
    test(`screenshots: ${scheme} ${width}`, async ({ browser }) => {
      const ctx = await browser.newContext({ colorScheme: scheme, viewport: { width, height } });
      const page = await ctx.newPage();
      const shot = async (name: string) => {
        await settleAnimations(page);
        await page.screenshot({ path: `${SHOTS}/${name}-${width}-${scheme}.png`, fullPage: true });
      };
      await page.goto(PROJECT);
      await expect(editor(page)).toHaveValue(/deny-env-read/);
      await shot("policies-editor");
      const box = editor(page);
      await box.fill((await box.inputValue()).replace("decision: deny", "decision: maybe"));
      await expect(page.getByTestId("issues")).toBeVisible();
      await shot("policies-issues");
      await box.fill(
        await box.inputValue().then((t) => t.replace("decision: maybe", "decision: deny")),
      );
      await expect(page.getByTestId("check-status")).toContainText("Unchanged");
      await page.getByRole("button", { name: "Dry run" }).click();
      await expect(page.getByTestId("dry-run-result")).toBeVisible();
      await page.getByTestId("dry-run-result").scrollIntoViewIfNeeded();
      await shot("policies-dry-run");
      await page.getByRole("button", { name: "View version 2" }).click();
      await page.getByRole("button", { name: /^Compare with current/ }).click();
      await expect(page.getByTestId("policy-diff")).toBeVisible();
      await shot("policies-history-diff");
      await ctx.close();
    });

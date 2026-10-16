// Design pass 2 step 5c: counts per screen for the report (docs/design/pass-2/01-direction.md A.7 asks for them): regions (landmarks,
// tables, forms, fieldsets, tab lists and any element with its own border box or fill, table cells and list items aside),
// distinct font sizes, distinct weights, font families, tab lists, interactive targets above 900 px, and elements painted in the
// fault colour. Usage: node scripts/count-step5c.mjs [baseUrl]. Needs the fixtures server (port 3110).
import { chromium } from "@playwright/test";

const base = process.argv[2] ?? "http://localhost:3110";
const S = "/w/demo/settings";
const screens = [
  ["review-approvals", "/w/demo/approvals"],
  ["review-approvals-open", "/w/demo/approvals?approval=apr_00000000000000000000000001"],
  ["review-security", "/w/demo/security"],
  ["settings-members", `${S}/members`],
  ["settings-api-keys", `${S}/api-keys`],
  ["settings-policies", `${S}/policies`],
  ["settings-security", `${S}/security`],
  ["settings-quotas", `${S}/quotas`],
  ["settings-retention", `${S}/retention`],
  ["settings-pricing", `${S}/pricing`],
  ["settings-sessions", `${S}/sessions`],
  ["settings-alerts", `${S}/alerts`],
  ["settings-audit", `${S}/audit`],
];
const browser = await chromium.launch({ channel: "chrome" });
const rows = [];
for (const [name, url] of screens) {
  const page = await (
    await browser.newContext({ viewport: { width: 1440, height: 900 } })
  ).newPage();
  await page.goto(base + url);
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(400);
  rows.push([
    name,
    await page.evaluate(() => {
      const visible = (el) => {
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
      };
      const probe = document.createElement("i");
      probe.style.color = "var(--color-status-fault)";
      document.body.append(probe);
      const red = getComputedStyle(probe).color;
      probe.remove();
      const sizes = new Set(),
        weights = new Set(),
        families = new Set();
      let regions = 0,
        redEls = 0,
        targets = 0,
        tablists = 0;
      for (const el of document.body.querySelectorAll("*")) {
        if (!visible(el)) continue;
        const s = getComputedStyle(el);
        if ([...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) {
          sizes.add(s.fontSize);
          weights.add(s.fontWeight);
          families.add(
            s.fontFamily
              .split(",")[0]
              .replace(/["']/g, "")
              .replace(/^var.*/, "mono/sans"),
          );
        }
        if ([s.color, s.backgroundColor, s.borderTopColor, s.borderBottomColor].includes(red))
          redEls++;
        const tag = el.tagName.toLowerCase();
        if (
          /^(a|button|input|select|textarea|summary)$/.test(tag) ||
          el.getAttribute("role") === "tab"
        )
          targets++;
        if (el.getAttribute("role") === "tablist") tablists++;
        const box =
          ["Top", "Right", "Bottom", "Left"].some(
            (d) => parseFloat(s[`border${d}Width`]) > 0 && s[`border${d}Style`] !== "none",
          ) &&
          !/^(td|th|tr|li|a|button|input|select|textarea|i|span|svg|path|pre|code|kbd|label)$/.test(
            tag,
          );
        const fill =
          s.backgroundColor !== "rgba(0, 0, 0, 0)" &&
          !/^(html|body|button|a|span|input|select|textarea|svg|path|tr|td|th|li|label)$/.test(tag);
        if (
          /^(nav|header|aside|main|table|form|fieldset|details|dialog)$/.test(tag) ||
          el.getAttribute("role") === "tablist" ||
          box ||
          fill
        )
          regions++;
      }
      return {
        regions,
        sizes: sizes.size,
        sizeList: [...sizes].sort((a, b) => parseFloat(a) - parseFloat(b)).join(" "),
        weights: [...weights].sort().join(","),
        families: [...families].join(","),
        tablists,
        targets,
        red: redEls,
      };
    }),
  ]);
  await page.context().close();
}
await browser.close();
console.log(
  "screen | regions | font sizes (count) | weights | tab lists | interactive targets | fault-coloured elements",
);
for (const [n, r] of rows)
  console.log(
    `${n} | ${r.regions} | ${r.sizes} [${r.sizeList}] | ${r.weights} | ${r.tablists} | ${r.targets} | ${r.red}`,
  );

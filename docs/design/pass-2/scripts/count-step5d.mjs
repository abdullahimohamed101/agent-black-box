// One-off script of design pass 2, kept as a record. It imports @playwright/test, so copy it into apps/web/scripts to run it (paths below are relative to apps/web).
// Design pass 2 steps 5c and 5d (screens come from screens5d.mjs, the whole app): counts per screen for the report (docs/design/pass-2/01-direction.md A.7 asks for them): regions (landmarks,
// tables, forms, fieldsets, tab lists and any element with its own border box or fill, table cells and list items aside),
// distinct font sizes, distinct weights, font families, tab lists, interactive targets above 900 px, and elements painted in the
// fault colour. Usage: node scripts/count-step5c.mjs [baseUrl]. Needs the fixtures server (port 3110).
import { chromium } from "@playwright/test";

const base = process.argv[2] ?? "http://localhost:3110";
import { SCREENS } from "./screens5d.mjs";
const screens = SCREENS;
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
        return (
          r.width > 0 &&
          r.height > 0 &&
          s.visibility !== "hidden" &&
          s.display !== "none"
        );
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
        if (
          [...el.childNodes].some(
            (n) => n.nodeType === 3 && n.textContent.trim(),
          )
        ) {
          sizes.add(s.fontSize);
          weights.add(s.fontWeight);
          families.add(
            s.fontFamily
              .split(",")[0]
              .replace(/["']/g, "")
              .replace(/^var.*/, "mono/sans"),
          );
        }
        if (
          [
            s.color,
            s.backgroundColor,
            s.borderTopColor,
            s.borderBottomColor,
          ].includes(red)
        )
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
            (d) =>
              parseFloat(s[`border${d}Width`]) > 0 &&
              s[`border${d}Style`] !== "none",
          ) &&
          !/^(td|th|tr|li|a|button|input|select|textarea|i|span|svg|path|pre|code|kbd|label)$/.test(
            tag,
          );
        const fill =
          s.backgroundColor !== "rgba(0, 0, 0, 0)" &&
          !/^(html|body|button|a|span|input|select|textarea|svg|path|tr|td|th|li|label)$/.test(
            tag,
          );
        if (
          /^(nav|header|aside|main|table|form|fieldset|details|dialog)$/.test(
            tag,
          ) ||
          el.getAttribute("role") === "tablist" ||
          box ||
          fill
        )
          regions++;
      }
      const h1 = document.querySelector("h1");
      const hb = h1?.getBoundingClientRect();
      const hs = h1 ? getComputedStyle(h1) : null;
      const th = document.querySelector("main thead th, main th[scope=col]");
      const ths = th ? getComputedStyle(th) : null;
      return {
        h1: h1
          ? `${Math.round(hb.x)},${Math.round(hb.y)} ${hs.fontSize}/${hs.fontWeight}`
          : "none",
        th: ths ? `${ths.fontSize}/${ths.color}/pl${ths.paddingLeft}` : "none",
        regions,
        sizes: sizes.size,
        sizeList: [...sizes]
          .sort((a, b) => parseFloat(a) - parseFloat(b))
          .join(" "),
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
  "screen | h1 (x,y size/weight) | table head | regions | font sizes (count) | weights | tab lists | interactive targets | fault-coloured elements",
);
for (const [n, r] of rows)
  console.log(
    `${n} | ${r.h1} | ${r.th} | ${r.regions} | ${r.sizes} [${r.sizeList}] | ${r.weights} | ${r.tablists} | ${r.targets} | ${r.red}`,
  );

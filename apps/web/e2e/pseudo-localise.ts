import type { Page } from "@playwright/test";

/**
 * Test-only pseudo-localisation (design pass 2 step 6). Rewrites every visible text node in the page after it has hydrated, and keeps
 * rewriting what React renders later, so the real copy modules, the fixture data and the server-sent strings are all stretched the
 * way German, Finnish or Spanish stretches English. It lives in the browser, not in the bundle: the shipped code is unchanged.
 *
 * Each run of letters grows by `factor` (0.3, 0.5, 0.7) with filler letters that keep the word breaks where they were, so wrapping
 * behaves like a real language. With `tokens`, names (the page title, a table's first-column link, code) also get a long unbroken
 * token appended: a run name or an agent slug that has no space to break at.
 */
export type Expansion = { label: string; factor: number; tokens: boolean };
export const EXPANSIONS: Expansion[] = [
  { label: "+30%", factor: 0.3, tokens: false },
  { label: "+50%", factor: 0.5, tokens: false },
  { label: "+70%", factor: 0.7, tokens: false },
  { label: "+70% and unbroken names", factor: 0.7, tokens: true },
];

export async function pseudoLocalise(page: Page, e: Expansion) {
  await page.waitForLoadState("networkidle");
  await page.evaluate(
    ({ factor, tokens }) => {
      const FILL = "eaoiuénra";
      const TOKEN = "customer_support_escalation_triage_agent_v2_0123456789abcdef";
      const SKIP = "script,style,noscript,textarea,input,option,[data-pseudo-skip]";
      const NAMES = "main h1, main td a, main td code";
      const seen = new WeakMap<Node, string>();
      const stretch = (s: string) =>
        s.replace(/\p{L}{2,}/gu, (w) => {
          const extra = Math.round(w.length * factor);
          if (extra < 1) return w;
          let pad = "";
          for (let i = 0; i < extra; i++) pad += FILL[(w.length + i) % FILL.length];
          return w + pad;
        });
      const expand = (n: Node) => {
        const el = n.parentElement;
        if (!el || el.closest(SKIP)) return;
        const cur = n.nodeValue ?? "";
        if (seen.get(n) === cur || !/\p{L}{2,}/u.test(cur)) return;
        let next = stretch(cur);
        const owner = tokens ? el.closest<HTMLElement>(NAMES) : null;
        if (owner && !owner.closest("header.topbar,nav") && !owner.dataset.token) {
          owner.dataset.token = "1";
          next = `${next} ${TOKEN}`;
        }
        seen.set(n, next);
        n.nodeValue = next;
      };
      const walk = (root: Node) => {
        const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        for (let n = w.nextNode(); n; n = w.nextNode()) expand(n);
      };
      const attrs = (root: ParentNode) => {
        for (const el of root.querySelectorAll<HTMLElement>("[placeholder]"))
          if (!el.dataset.pseudo) {
            el.dataset.pseudo = "1";
            el.setAttribute("placeholder", stretch(el.getAttribute("placeholder") ?? ""));
          }
      };
      walk(document.body);
      attrs(document);
      new MutationObserver((records) => {
        for (const r of records) {
          if (r.type === "characterData") expand(r.target);
          else
            for (const added of r.addedNodes) {
              if (added.nodeType === Node.TEXT_NODE) expand(added);
              else if (added.nodeType === Node.ELEMENT_NODE) {
                walk(added);
                attrs(added as Element);
              }
            }
        }
      }).observe(document.body, { childList: true, subtree: true, characterData: true });
    },
    { factor: e.factor, tokens: e.tokens },
  );
  // Let the layout settle after the stretch (fonts are local; this is one frame, not a timer for data).
  await page.evaluate(() => new Promise<void>((r) => requestAnimationFrame(() => r())));
}

/** Every layout problem a person would see on the current page. Runs in the page; returns one line per problem. */
export function layoutProblems(page: Page) {
  return page.evaluate(() => {
    const out: string[] = [];
    const vw = document.documentElement.clientWidth;
    const name = (el: Element) =>
      `${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ""}${
        typeof el.className === "string" && el.className
          ? "." + el.className.trim().split(/\s+/).slice(0, 2).join(".")
          : ""
      } "${(el.textContent ?? "").trim().replace(/\s+/g, " ").slice(0, 28)}"`;
    const shown = (el: Element) => {
      if (el.closest("a.skip") && document.activeElement !== el) return false; // off screen until focused, by design
      // The content of a closed <details> is not rendered (the browser still reports stale boxes for it).
      if (el.closest("details:not([open])") && !el.matches("summary") && !el.closest("summary"))
        return false;
      const s = getComputedStyle(el);
      if (s.display === "none" || s.visibility === "hidden" || Number(s.opacity) === 0)
        return false;
      const r = el.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) return false;
      // The visually hidden pattern (sr-only, skip link while it is off screen).
      if (s.position === "absolute" && (r.width <= 1 || r.height <= 1)) return false;
      if (el.closest("[hidden],[aria-hidden='true']")) return false;
      // Inside a visually-hidden box (the table that is the text equivalent of a chart).
      for (let p: Element | null = el; p; p = p.parentElement) {
        const ps = getComputedStyle(p);
        if (ps.position !== "absolute" && ps.position !== "fixed") continue;
        const pr = p.getBoundingClientRect();
        if (pr.width <= 1 || pr.height <= 1 || ps.clip === "rect(0px, 0px, 0px, 0px)") return false;
      }
      return true;
    };
    const scrollerOf = (el: Element) => {
      for (let p: Element | null = el.parentElement; p; p = p.parentElement) {
        const ox = getComputedStyle(p).overflowX;
        if (ox === "auto" || ox === "scroll" || ox === "hidden" || ox === "clip") return p;
      }
      return null;
    };

    // 1. The page itself never scrolls sideways.
    const de = document.documentElement;
    if (de.scrollWidth > vw + 1) out.push(`page: scrollWidth ${de.scrollWidth} > ${vw}`);
    if (document.body.scrollWidth > vw + 1)
      out.push(`body: scrollWidth ${document.body.scrollWidth} > ${vw}`);

    // 2. Nothing sits outside the viewport (unless a scroll region owns it), and clipped text is designed or a bug.
    for (const el of document.body.querySelectorAll("*")) {
      if (!shown(el) || (el.closest("svg") && el.tagName.toLowerCase() !== "svg")) continue;
      const r = el.getBoundingClientRect();
      const owner = scrollerOf(el);
      if ((r.right > vw + 1 || r.left < -1) && !owner)
        out.push(
          `outside the viewport: ${name(el)} [${Math.round(r.left)}..${Math.round(r.right)}]`,
        );
      const s = getComputedStyle(el);
      const clips = ["hidden", "clip", "auto", "scroll"];
      if (
        el !== document.body &&
        el !== de &&
        clips.includes(s.overflowX) &&
        el.scrollWidth > el.clientWidth + 1
      ) {
        const designed = s.textOverflow === "ellipsis" && el.getAttribute("title");
        const scrollRegion =
          (s.overflowX === "auto" || s.overflowX === "scroll") &&
          (el.closest(
            "pre,textarea,.table-scroll,.table-wrap,.tabs,.aligned-toolbar,[data-scroll]",
          ) ||
            el.matches(
              "pre,textarea,.table-scroll,.table-wrap,.tabs,.aligned-toolbar,[data-scroll]",
            ));
        if (!designed && !scrollRegion)
          out.push(
            `clipped or scrolls sideways (${s.overflowX}, ${s.textOverflow}): ${name(el)} ${el.scrollWidth} > ${el.clientWidth}`,
          );
      }
      if (
        s.textOverflow === "ellipsis" &&
        el.scrollWidth > el.clientWidth + 1 &&
        !el.getAttribute("title") &&
        !el.getAttribute("aria-label")
      )
        out.push(`ellipsis without a full text (title): ${name(el)}`);
      // A fixed height that cuts the text off.
      if (
        (s.overflowY === "hidden" || s.overflowY === "clip") &&
        el.scrollHeight > el.clientHeight + 2 &&
        el.children.length === 0 &&
        s.textOverflow !== "ellipsis" &&
        !s.webkitLineClamp
      )
        out.push(`text cut off vertically: ${name(el)} ${el.scrollHeight} > ${el.clientHeight}`);
    }

    // 3. Overlap: text boxes and controls that cover each other.
    // Text that an ancestor clips (an ellipsis, a scroll region) is not seen outside that ancestor: judge what is painted.
    const clipped = (el: Element, r: DOMRect) => {
      let l = r.left;
      let t = r.top;
      let rt = r.right;
      let b = r.bottom;
      for (let p: Element | null = el; p && p !== document.body; p = p.parentElement) {
        const s = getComputedStyle(p);
        const pr = p.getBoundingClientRect();
        if (s.overflowX !== "visible") {
          l = Math.max(l, pr.left);
          rt = Math.min(rt, pr.right);
        }
        if (s.overflowY !== "visible") {
          t = Math.max(t, pr.top);
          b = Math.min(b, pr.bottom);
        }
      }
      return new DOMRect(l, t, Math.max(0, rt - l), Math.max(0, b - t));
    };
    type Box = { el: Element; r: DOMRect; text: boolean };
    const boxes: Box[] = [];
    for (const el of document.body.querySelectorAll("*")) {
      if (!shown(el) || el.closest("svg")) continue;
      const direct = [...el.childNodes].some(
        (n) => n.nodeType === 3 && (n.nodeValue ?? "").trim() !== "",
      );
      const control = el.matches("button,a,input,select,[role='tab'],[role='button'],summary");
      if (!direct && !control) continue;
      if (direct) {
        // Direct text nodes only: a range over the element would also report its descendants (a screen-reader-only span).
        for (const n of el.childNodes) {
          if (n.nodeType !== 3 || (n.nodeValue ?? "").trim() === "") continue;
          // A lone glyph (a disclosure triangle) has a fallback font's line box, not its ink: not text a person reads.
          if (!/[\p{L}\p{N}]/u.test(n.nodeValue ?? "")) continue;
          const range = document.createRange();
          range.selectNodeContents(n);
          for (const raw of range.getClientRects()) {
            const r = clipped(el, raw);
            if (r.width > 1 && r.height > 1) boxes.push({ el, r, text: true });
          }
        }
      } else boxes.push({ el, r: el.getBoundingClientRect(), text: false });
    }
    const hit = (a: DOMRect, b: DOMRect) => {
      const w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
      const h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      return w > 3 && h > 3 && w * h > 0.25 * Math.min(a.width * a.height, b.width * b.height);
    };
    const seenPair = new Set<string>();
    for (let i = 0; i < boxes.length; i++)
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i]!;
        const b = boxes[j]!;
        if (a.el === b.el || a.el.contains(b.el) || b.el.contains(a.el)) continue;
        // A control's box (a 44 px touch target with negative margins) is not ink: only text-on-text and control-on-control count.
        if (a.text !== b.text) continue;
        if (!hit(a.r, b.r)) continue;
        // Text over a control it sits inside is one thing; two separate things covering each other is the bug.
        const key = `${name(a.el)} | ${name(b.el)}`;
        if (!seenPair.has(key)) {
          seenPair.add(key);
          out.push(`overlap: ${key}`);
        }
      }

    // 4. The top bar is still usable: every control inside the viewport, none over another, a bounded height.
    const bar = document.querySelector("header.topbar");
    if (bar) {
      const br = bar.getBoundingClientRect();
      if (br.height > 150) out.push(`top bar is ${Math.round(br.height)} px tall`);
      const controls = [...bar.querySelectorAll("a,button,input")].filter(shown);
      if (controls.length < 3) out.push(`top bar has ${controls.length} visible controls`);
      for (const c of controls) {
        const r = c.getBoundingClientRect();
        if (r.left < -1 || r.right > vw + 1)
          out.push(`top bar control outside the viewport: ${name(c)}`);
      }
      for (let i = 0; i < controls.length; i++)
        for (let j = i + 1; j < controls.length; j++)
          if (
            !controls[i]!.contains(controls[j]!) &&
            !controls[j]!.contains(controls[i]!) &&
            hit(controls[i]!.getBoundingClientRect(), controls[j]!.getBoundingClientRect())
          )
            out.push(`top bar overlap: ${name(controls[i]!)} | ${name(controls[j]!)}`);
    }
    return out;
  });
}

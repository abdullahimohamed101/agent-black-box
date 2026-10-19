/*
 * record-demo-lib.mjs: the pure parts of scripts/record-demo.mjs (no browser, no files): easing, the caption timing rule, the camera
 * origin formula, subtitle files and the frame list. They are tested by scripts/record-demo-lib.test.mjs (`node --test scripts/`).
 * The numbers come from docs/design/launch/video-brief.md (sections 3 and 4).
 */

/** cubic-bezier(x1, y1, x2, y2) as an easing function f(0)=0, f(1)=1, solved for t by bisection. */
export function cubicBezier(x1, y1, x2, y2) {
  const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
  const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
  const sx = (t) => ((ax * t + bx) * t + cx) * t;
  const sy = (t) => ((ay * t + by) * t + cy) * t;
  return (x) => {
    if (x <= 0) return 0;
    if (x >= 1) return 1;
    let lo = 0, hi = 1, t = x;
    for (let i = 0; i < 40; i++) {
      const v = sx(t);
      if (Math.abs(v - x) < 1e-6) break;
      if (v < x) lo = t; else hi = t;
      t = (lo + hi) / 2;
    }
    return sy(t);
  };
}

/** Cursor travel: fast start, long settle (brief 3.3). */
export const travelEase = cubicBezier(0.22, 1, 0.36, 1);
/** Camera and spotlight: symmetric ease-in-out (brief 3.1). The CSS side uses the same string. */
export const CAMERA_EASE_CSS = "cubic-bezier(0.65, 0, 0.35, 1)";

/** Duration of one travel in ms: clamp(350, 0.9 ms per px, 1100). */
export function travelMs(distancePx) {
  return Math.max(350, Math.min(1100, distancePx * 0.9));
}

/** Perpendicular bow of a travel: 4 % of the distance, at most 40 px. */
export function bowPx(distancePx) {
  return Math.min(40, distancePx * 0.04);
}

/** The point of a travel at progress p (0..1) from a to b: eased along the line plus the bow. */
export function travelPoint(a, b, p) {
  const dx = b.x - a.x, dy = b.y - a.y;
  const dist = Math.hypot(dx, dy) || 1;
  const e = travelEase(p);
  const arc = Math.sin(Math.PI * p) * bowPx(dist);
  return { x: a.x + dx * e + (-dy / dist) * arc, y: a.y + dy * e + (dx / dist) * arc };
}

/**
 * Camera origin so that the focus point ends near the middle of the window and no canvas edge shows (brief 3.1).
 * With origin O and scale s the visible window is [O - O/s, O + (W - O)/s], inside [0, W] whenever 0 <= O <= W.
 */
export function zoomOrigin(scale, focus, size) {
  const one = (c, extent) => {
    const o = (c - extent / (2 * scale)) / (1 - 1 / scale);
    return Math.max(0, Math.min(extent, o));
  };
  return { x: one(focus.x, size.w), y: one(focus.y, size.h) };
}

/** The visible window of the zoomed page, for tests and for the QA of "no canvas edge". */
export function zoomWindow(scale, origin, size) {
  return {
    x0: origin.x - origin.x / scale,
    x1: origin.x + (size.w - origin.x) / scale,
    y0: origin.y - origin.y / scale,
    y1: origin.y + (size.h - origin.y) / scale,
  };
}

// ---- captions (brief section 4) ------------------------------------------------------------------------------------------

/** The text of a caption without the `[[red words]]` markup. */
export function plain(text) {
  return text.replace(/\[\[|\]\]/g, "");
}

/** The caption as HTML: `[[x]]` becomes <em>x</em> (the only colour inside a caption), everything else is escaped. */
export function captionHtml(text) {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return esc(text).replace(/\[\[(.+?)\]\]/g, "<em>$1</em>");
}

/** The least time a caption stays on screen: max(2.0 s, characters / 15 + 0.6 s), at most 6 s. */
export function minCaptionSeconds(text) {
  return Math.min(6, Math.max(2.0, plain(text).length / 15 + 0.6));
}

export const MAX_CAPTION_WORDS = 9;
export const MIN_CAPTION_GAP = 0.25;

export function wordCount(text) {
  return plain(text).trim().split(/\s+/).filter(Boolean).length;
}

/**
 * Lay a beat's captions on its clock. Each cue starts at `at` (seconds from the beat start) and lasts `d` seconds, or the reading
 * rule when `d` is omitted; a cue never starts before the previous one ended plus the gap. Returns the cues with absolute
 * `start`/`end` (seconds from the beat start) and the beat's length: the table's `len`, or the end of the last cue plus `tail`.
 */
export function layoutBeat(beat, tail = 0.3) {
  let free = 0;
  const cues = [];
  for (const cap of beat.caps ?? []) {
    const start = Math.max(cap.at, free);
    const end = start + (cap.d ?? minCaptionSeconds(cap.text));
    cues.push({ ...cap, start, end });
    free = end + MIN_CAPTION_GAP;
  }
  const last = cues.length ? cues[cues.length - 1].end + tail : 0;
  return { cues, len: Math.max(beat.len ?? 0, last) };
}

/** Plan a whole cut: each beat's start (seconds from the cut start), length and cues. */
export function planCut(beats) {
  let t = 0;
  return beats.map((beat) => {
    const { cues, len } = layoutBeat(beat);
    const planned = { id: beat.id, start: t, len, cues };
    t += len;
    return planned;
  });
}

// ---- subtitle files -----------------------------------------------------------------------------------------------------

const pad = (n, w) => String(Math.floor(n)).padStart(w, "0");

export function vttTime(seconds) {
  const ms = Math.round(seconds * 1000);
  return `${pad(ms / 3600000, 2)}:${pad((ms % 3600000) / 60000, 2)}:${pad((ms % 60000) / 1000, 2)}.${pad(ms % 1000, 3)}`;
}

export function srtTime(seconds) {
  return vttTime(seconds).replace(".", ",");
}

/** `cues`: [{start, end, text}] with the real times the recorder logged; `text` may carry [[markup]]. */
export function buildVtt(cues) {
  const body = cues.map((c, i) => `${i + 1}\n${vttTime(c.start)} --> ${vttTime(c.end)}\n${plain(c.text)}\n`);
  return `WEBVTT\n\n${body.join("\n")}`;
}

export function buildSrt(cues) {
  return cues.map((c, i) => `${i + 1}\n${srtTime(c.start)} --> ${srtTime(c.end)}\n${plain(c.text)}\n`).join("\n");
}

/** Read back a .vtt: the cues as [{start, end, text}]; throws on a file that is not WebVTT (used by the recorder's own check). */
export function parseVtt(text) {
  if (!text.startsWith("WEBVTT")) throw new Error("not a WebVTT file");
  const seconds = (s) => {
    const m = /^(\d+):(\d\d):(\d\d)\.(\d{3})$/.exec(s);
    if (!m) throw new Error(`bad time ${s}`);
    return +m[1] * 3600 + +m[2] * 60 + +m[3] + +m[4] / 1000;
  };
  const cues = [];
  for (const block of text.split(/\n\n+/).slice(1)) {
    const lines = block.trim().split("\n");
    const at = lines.findIndex((l) => l.includes("-->"));
    if (at < 0) continue;
    const [a, b] = lines[at].split("-->").map((s) => s.trim());
    cues.push({ start: seconds(a), end: seconds(b), text: lines.slice(at + 1).join("\n") });
  }
  return cues;
}

// ---- frames -------------------------------------------------------------------------------------------------------------

/**
 * ffconcat list with real durations: frame i lasts until frame i+1 arrived, the last one until `endMs`. The first frame is
 * treated as starting at `startMs` (the recorder's clock zero) however late the compositor delivered it.
 */
export function buildConcat(frames, startMs, endMs) {
  const lines = ["ffconcat version 1.0"];
  for (let i = 0; i < frames.length; i++) {
    const from = i === 0 ? startMs : frames[i].t;
    const to = i + 1 < frames.length ? frames[i + 1].t : endMs;
    lines.push(`file '${frames[i].file}'`, `duration ${Math.max(0.001, (to - from) / 1000).toFixed(4)}`);
  }
  if (frames.length) lines.push(`file '${frames[frames.length - 1].file}'`);
  return lines.join("\n") + "\n";
}

/** The longest gap between consecutive frames inside [fromMs, toMs]: a stalled screencast shows up as a large number. */
export function maxFrameGap(frames, fromMs, toMs) {
  const inside = frames.map((f) => f.t).filter((t) => t >= fromMs && t <= toMs);
  let gap = 0;
  let prev = fromMs;
  for (const t of [...inside, toMs]) {
    gap = Math.max(gap, t - prev);
    prev = t;
  }
  return gap;
}

// ---- text lines: what the camera and the caption must not cut or cover (polish pass) -------------------------------------------
// A "line" is the client rect of one line of one text node ({x, y, w, h}, viewport CSS px, transforms included), measured in the page.

const TOL = 0.5;

/**
 * The lines the viewport edge cuts through: partly visible (at least TOL px inside, at least TOL px outside). A line that is
 * wholly outside, or wholly inside, is not clipped. This is "cut mid-glyph": the text node is one line of glyphs.
 */
export function clippedLines(lines, view) {
  const cut = (lo, len, max) => (lo < -TOL && lo + len > TOL) || (lo < max - TOL && lo + len > max + TOL);
  const inside = (lo, len, max) => lo < max - TOL && lo + len > TOL;
  return lines.filter((r) => (cut(r.x, r.w, view.w) && inside(r.y, r.h, view.h)) || (cut(r.y, r.h, view.h) && inside(r.x, r.w, view.w)));
}

/** Lines of the page (layout px, no camera) as they appear with scale `s` and window top-left (x0, y0). */
export function throughCamera(lines, s, x0, y0) {
  return lines.map((r) => ({ ...r, x: (r.x - x0) * s, y: (r.y - y0) * s, w: r.w * s, h: r.h * s }));
}

/**
 * Choose the camera for a zoom: the largest scale in [sMin, s0] and the window position at which the viewport edges cut no line
 * of text, with `focus` (a rect) inside the window by `margin`; among equals, the window centred nearest the focus. A window edge
 * on the page edge cuts nothing. Returns {s, x0, y0, clipped} where `clipped` is how many lines are still cut (0 = clean).
 */
export function fitZoom({ lines, focus, s0, sMin = 1.25, view, margin = 24, stepPx = 6, stepS = 0.05 }) {
  const near = lines.filter((r) => r.x < view.w && r.x + r.w > 0 && r.y < view.h && r.y + r.h > 0);
  let best = null;
  for (let s = s0; s >= sMin - 1e-9; s -= stepS) {
    const vw = view.w / s, vh = view.h / s;
    const xr = [Math.max(0, Math.min(view.w, focus.x + focus.w + margin) - vw), Math.min(view.w - vw, Math.max(0, focus.x - margin))];
    const yr = [Math.max(0, Math.min(view.h, focus.y + focus.h + margin) - vh), Math.min(view.h - vh, Math.max(0, focus.y - margin))];
    if (xr[0] > xr[1] || yr[0] > yr[1]) continue; // the focus does not fit at this scale
    const axis = (r) => { const out = []; for (let v = r[0]; v <= r[1]; v += stepPx) out.push(v); out.push(r[1]); return out; };
    const fcx = focus.x + focus.w / 2, fcy = focus.y + focus.h / 2;
    for (const x0 of axis(xr)) {
      for (const y0 of axis(yr)) {
        const clipped = clippedLines(throughCamera(near, s, x0, y0), view).length;
        const dist = Math.hypot(x0 + vw / 2 - fcx, y0 + vh / 2 - fcy);
        if (!best || clipped < best.clipped || (clipped === best.clipped && clipped === 0 && (s > best.s + 1e-9 || (Math.abs(s - best.s) < 1e-9 && dist < best.dist)))) {
          best = { s: +s.toFixed(3), x0, y0, clipped, dist };
        }
      }
    }
    if (best && best.clipped === 0 && Math.abs(best.s - s0) < 1e-9) break; // the wanted scale works: do not look further
  }
  return best;
}

const hits = (a, b, pad = 0) => a.x < b.x + b.w + pad && a.x + a.w > b.x - pad && a.y < b.y + b.h + pad && a.y + a.h > b.y - pad;

/** How much of the picture a caption box at `rect` would cover: lines of text and the rects the beat is about. */
export function captionOverlap(rect, lines, focus, view) {
  const seen = lines.filter((r) => r.x < view.w && r.x + r.w > 0 && r.y < view.h && r.y + r.h > 0);
  return { lines: seen.filter((r) => hits(rect, r, 6)).length, focus: focus.some((f) => hits(rect, f, 12)) };
}

/**
 * Where a caption of size (w, h) goes: the lowest top y, from its usual place (box bottom 84 px above the frame bottom) upwards in
 * 6 px steps to just under the top bar, at which the box covers no text and none of the focus rects. When no such place exists:
 * the place that avoids the focus rects and covers the fewest lines, lowest first. Returns {top, lines, focus}.
 */
export function placeCaption({ w, h, lines, focus, view, bottomGap = 84, topMin = 56 }) {
  const left = (view.w - w) / 2;
  let best = null;
  for (let top = view.h - bottomGap - h; top >= topMin; top -= 6) {
    const o = captionOverlap({ x: left, y: top, w, h }, lines, focus, view);
    if (o.lines === 0 && !o.focus) return { top, ...o };
    const score = (o.focus ? 1e6 : 0) + o.lines;
    if (!best || score < best.score) best = { top, ...o, score };
  }
  const { score, ...rest } = best ?? { top: view.h - bottomGap - h, lines: 0, focus: false, score: 0 };
  return rest;
}

/** Largest brightness step between consecutive frames (0-255 means) and where it is: a flash is a step above ~40. */
export function maxLumaStep(means) {
  let step = 0, at = 0;
  for (let i = 1; i < means.length; i++) {
    const d = Math.abs(means[i] - means[i - 1]);
    if (d > step) { step = d; at = i; }
  }
  return { step, at };
}

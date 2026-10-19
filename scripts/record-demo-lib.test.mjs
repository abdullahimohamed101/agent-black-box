// node --test scripts/   (the pure parts of record-demo.mjs; no browser, no stack)
import assert from "node:assert/strict";
import test from "node:test";
import {
  applyHolds, buildConcat, buildSrt, captionOverlap, clippedLines, fitZoom, maxLumaStep, placeCaption, throughCamera, buildVtt, captionHtml, cubicBezier, layoutBeat, maxFrameGap, minCaptionSeconds, parseVtt, plain, planCut,
  travelEase, travelMs, travelPoint, vttTime, wordCount, zoomOrigin, zoomWindow,
} from "./record-demo-lib.mjs";

test("bezier easing: f(0)=0, f(1)=1, monotonic, and the camera curve is symmetric", () => {
  for (const ease of [travelEase, cubicBezier(0.65, 0, 0.35, 1)]) {
    assert.equal(ease(0), 0);
    assert.equal(ease(1), 1);
    let prev = -1;
    for (let i = 0; i <= 200; i++) {
      const v = ease(i / 200);
      assert.ok(v >= prev - 1e-9, `monotonic at ${i}`);
      prev = v;
    }
  }
  const camera = cubicBezier(0.65, 0, 0.35, 1);
  assert.ok(Math.abs(camera(0.5) - 0.5) < 1e-4);
  assert.ok(Math.abs(camera(0.25) + camera(0.75) - 1) < 1e-4);
  assert.ok(travelEase(0.1) > 0.25, "travel starts fast");
});

test("travel duration is 0.9 ms per px, clamped to 350..1100 ms", () => {
  assert.equal(travelMs(10), 350);
  assert.equal(travelMs(500), 450);
  assert.equal(travelMs(5000), 1100);
});

test("a travel starts and ends exactly on its endpoints and bows at most 40 px", () => {
  const a = { x: 100, y: 100 }, b = { x: 1500, y: 300 };
  assert.deepEqual(travelPoint(a, b, 0), a);
  const end = travelPoint(a, b, 1);
  assert.ok(Math.abs(end.x - b.x) < 1e-6 && Math.abs(end.y - b.y) < 1e-6);
  for (let i = 0; i <= 20; i++) {
    const p = travelPoint(a, b, i / 20);
    const along = ((p.x - a.x) * (b.x - a.x) + (p.y - a.y) * (b.y - a.y)) / Math.hypot(b.x - a.x, b.y - a.y);
    const off = Math.abs(-(p.x - a.x) * (b.y - a.y) + (p.y - a.y) * (b.x - a.x)) / Math.hypot(b.x - a.x, b.y - a.y);
    assert.ok(off <= 40.0001, `bow ${off} at ${i}`);
    assert.ok(along >= -50);
  }
});

test("the camera origin never shows a canvas edge, wherever the focus is", () => {
  const size = { w: 1920, h: 1080 };
  for (const scale of [1.4, 1.6]) {
    for (const fx of [0, 60, 500, 960, 1500, 1900, 1920]) {
      for (const fy of [0, 120, 540, 1000, 1080]) {
        const o = zoomOrigin(scale, { x: fx, y: fy }, size);
        const w = zoomWindow(scale, o, size);
        assert.ok(w.x0 >= -1e-9 && w.x1 <= size.w + 1e-9 && w.y0 >= -1e-9 && w.y1 <= size.h + 1e-9, `${scale} ${fx},${fy}`);
      }
    }
  }
  const mid = zoomOrigin(1.6, { x: 960, y: 540 }, size);
  assert.ok(Math.abs(mid.x - 960) < 1e-9, "a centred focus zooms about the centre");
});

test("the caption rule: max(2.0 s, chars / 15 + 0.6 s), at most 6 s, and the worked examples of the brief", () => {
  assert.ok(Math.abs(minCaptionSeconds("A failed run. Open it.") - 2.0667) < 0.01);
  assert.ok(Math.abs(minCaptionSeconds("One sentence says where it broke.") - 2.8) < 0.01);
  assert.ok(Math.abs(minCaptionSeconds("Destructive commands, secret reads, risky git.") - 3.667) < 0.01);
  assert.equal(minCaptionSeconds("x".repeat(500)), 6);
  assert.equal(minCaptionSeconds("Hi."), 2);
});

test("markup: [[red]] becomes <em>, plain text drops it, html is escaped", () => {
  assert.equal(captionHtml("A [[failed]] run <b>"), "A <em>failed</em> run &lt;b&gt;");
  assert.equal(plain("The red line is the [[first error]]."), "The red line is the first error.");
  assert.equal(wordCount("Press e. The [[failed]] tool call opens."), 7);
});

test("a beat lays captions on its clock: no cue shorter than the rule, gaps of at least 250 ms, the beat holds them", () => {
  const beat = { len: 3, caps: [{ at: 0.2, text: "Every model call carries tokens and cost." }, { at: 0.3, text: "Retries are retries, not log lines." }] };
  const { cues, len } = layoutBeat(beat);
  assert.equal(cues.length, 2);
  assert.ok(cues[0].end - cues[0].start >= minCaptionSeconds(cues[0].text) - 1e-9);
  assert.ok(cues[1].start - cues[0].end >= 0.25 - 1e-9);
  assert.ok(len >= cues[1].end);
});

test("a cut is the beats back to back", () => {
  const plan = planCut([{ id: 1, len: 3 }, { id: 2, len: 4 }, { id: 3, len: 2 }]);
  assert.deepEqual(plan.map((b) => b.start), [0, 3, 7]);
});

test("subtitles: WebVTT and SRT carry the plain text with the right time format and read back", () => {
  const cues = [{ start: 0.3, end: 3.6, text: "A [[failed]] run. Open it." }, { start: 65.5, end: 70.125, text: "Light and dark follow your system." }];
  assert.equal(vttTime(3723.4567), "01:02:03.457");
  const vtt = buildVtt(cues);
  assert.ok(vtt.startsWith("WEBVTT\n\n1\n00:00:00.300 --> 00:00:03.600\nA failed run. Open it."));
  assert.ok(!vtt.includes("[[") && !vtt.includes("<em>"));
  const back = parseVtt(vtt);
  assert.equal(back.length, 2);
  assert.equal(back[1].start, 65.5);
  assert.equal(back[1].text, "Light and dark follow your system.");
  assert.ok(buildSrt(cues).includes("00:01:05,500 --> 00:01:10,125"));
  assert.throws(() => parseVtt("nope"));
});

test("the frame list has real durations: wall time equals playback time, the last frame runs to the end mark", () => {
  const frames = [{ file: "a.jpg", t: 1000 }, { file: "b.jpg", t: 1500 }, { file: "c.jpg", t: 4000 }];
  const list = buildConcat(frames, 900, 6000);
  const durations = [...list.matchAll(/duration ([\d.]+)/g)].map((m) => +m[1]);
  assert.deepEqual(durations, [0.6, 2.5, 2]);
  assert.ok(Math.abs(durations.reduce((a, b) => a + b, 0) - 5.1) < 1e-9);
  assert.ok(list.trim().endsWith("file 'c.jpg'"));
});

test("a stalled screencast shows up as a large gap", () => {
  const frames = [{ t: 100 }, { t: 133 }, { t: 166 }, { t: 3000 }];
  assert.equal(maxFrameGap(frames, 100, 3100), 2834);
  assert.equal(maxFrameGap(frames, 100, 200), 34);
});

// ---- the camera must not cut text, the caption must not cover it (polish pass) ----------------------------------------------

const view = { w: 1920, h: 1080 };
const row = (x, y, w, h = 14) => ({ x, y, w, h });

test("clippedLines: a line the viewport edge runs through is clipped; whole lines and page-edge lines are not", () => {
  const lines = [row(-30, 100, 200), row(10, 200, 100), row(1900, 300, 100), row(1930, 400, 50), row(100, 1075, 100), row(100, 500, 100)];
  const cut = clippedLines(lines, view);
  assert.deepEqual(cut.map((r) => r.y), [100, 300, 1075]);
});

test("fitZoom finds a window whose edges run through gaps, and says so when it cannot", () => {
  // text from x 24 to 700 on every row (a table), rows 32 px apart, a column of text at x 1600-1900
  const lines = [];
  for (let y = 60; y < 1000; y += 32) { lines.push(row(24, y, 676)); lines.push(row(1600, y, 300)); }
  const focus = { x: 1600, y: 300, w: 300, h: 100 };
  const fit = fitZoom({ lines, focus, s0: 1.6, sMin: 1.25, view });
  assert.equal(fit.clipped, 0);
  assert.ok(fit.s >= 1.55 && fit.s <= 1.6, "only the high zoom clears the left column");
  const w = { x0: fit.x0, x1: fit.x0 + view.w / fit.s, y0: fit.y0, y1: fit.y0 + view.h / fit.s };
  assert.ok(w.x0 <= focus.x && w.x1 >= focus.x + focus.w && w.y0 <= focus.y && w.y1 >= focus.y + focus.h, "the focus is inside the window");
  assert.equal(clippedLines(throughCamera(lines, fit.s, fit.x0, fit.y0), view).length, 0);
  // a wall of text from edge to edge cannot be framed cleanly: the answer must say so, never pretend
  const wall = []; for (let y = 0; y < 1080; y += 20) wall.push(row(0, y, 1920, 20));
  const bad = fitZoom({ lines: wall, focus: { x: 800, y: 500, w: 100, h: 40 }, s0: 1.6, sMin: 1.5, view });
  assert.ok(bad.clipped > 0);
});

test("planted violation: the draft-1 camera (1.4x pinned right) cuts the headline; the fitted one does not", () => {
  const lines = [row(24, 122, 680, 24), row(140, 481, 330), row(124, 513, 551), row(1521, 355, 120), row(1521, 382, 160)];
  const focus = { x: 1505, y: 347, w: 400, h: 100 };
  const naive = throughCamera(lines, 1.4, 549, 0);
  assert.ok(clippedLines(naive, view).length > 0, "the draft-1 camera cuts the headline mid-word");
  const fit = fitZoom({ lines, focus, s0: 1.6, sMin: 1.3, view });
  assert.equal(fit.clipped, 0);
});

test("placeCaption: the lowest empty band, never on the focus, and says what it covers when nothing is free", () => {
  const empty = placeCaption({ w: 800, h: 72, lines: [row(24, 100, 600)], focus: [], view });
  assert.equal(empty.top, 1080 - 84 - 72);
  assert.equal(empty.lines, 0);
  const lines = []; for (let y = 880; y < 1060; y += 20) lines.push(row(0, y, 1920, 16));
  const moved = placeCaption({ w: 800, h: 72, lines, focus: [], view });
  assert.ok(moved.top < 880 - 72 && moved.lines === 0);
  const dense = []; for (let y = 60; y < 1060; y += 20) dense.push(row(0, y, 1920, 16));
  const o = placeCaption({ w: 800, h: 72, lines: dense, focus: [{ x: 400, y: 900, w: 900, h: 80 }], view });
  assert.equal(o.focus, false);
  assert.ok(o.lines > 0);
  assert.ok(captionOverlap({ x: 560, y: 900, w: 800, h: 72 }, dense, [], view).lines > 0);
});

test("maxLumaStep finds a one-frame flash and ignores a long cross-fade", () => {
  const flash = [37, 37, 37, 222, 222, 222];
  assert.equal(maxLumaStep(flash).step, 185);
  assert.equal(maxLumaStep(flash).at, 3);
  const fade = Array.from({ length: 19 }, (_, i) => 37 + (185 * i) / 18);
  assert.ok(maxLumaStep(fade).step < 12);
});

test("a hop is a hard cut: only the last frame of the cut-out interval survives, at its start, and later frames move earlier", () => {
  const f = (t) => ({ file: `f${t}`, t });
  const frames = [f(0), f(100), f(500), f(900), f(1200), f(1600), f(2000)];
  const out = applyHolds(frames, [{ from: 400, to: 1300 }]);
  assert.deepEqual(out.map((x) => [x.file, x.t]), [["f0", 0], ["f100", 100], ["f1200", 400], ["f1600", 700], ["f2000", 1100]]);
  // two holds: the second is shifted by the first
  const two = applyHolds(frames, [{ from: 400, to: 1000 }, { from: 1100, to: 1700 }]);
  assert.deepEqual(two.map((x) => [x.file, x.t]), [["f0", 0], ["f100", 100], ["f900", 400], ["f1600", 500], ["f2000", 800]]);
  // a hold with no frame inside it only closes the gap; no holds changes nothing
  assert.deepEqual(applyHolds(frames, [{ from: 1700, to: 1800 }]).map((x) => x.t), [0, 100, 500, 900, 1200, 1600, 1900]);
  assert.deepEqual(applyHolds(frames, []).map((x) => x.t), frames.map((x) => x.t));
  // the film's end is the old end minus the holds: durations then add up to playback time
  const concat = buildConcat(out, 0, 2000 - 900);
  assert.match(concat, /duration 0.3000\nfile 'f1200'\nduration 0.3000\nfile 'f1600'\nduration 0.4000/);
});

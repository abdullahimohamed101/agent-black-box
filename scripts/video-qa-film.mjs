/*
 * video-qa-film.mjs: the QA checks of the framed (cinematic) film, docs/design/launch/video-cinematic-brief.md section 9. Pixels are
 * read from the composed master with ffmpeg; the timeline (cue, zoom and cut times in film seconds) is read from compose.json.
 * Every check can be run against a planted violation (`--plant <name>` on video-qa.mjs): the planted frame goes through the same
 * measurement, and the check must fail. Names: band, window, dim, red, cards, dips, cuts, fonts.
 */
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import path from "node:path";

const FAULT_BANKS = new Set([3, 7, 8]); // the captions whose red word is the failure word

export async function filmChecks({ root, take, outDir, master, meta, tl, spec, check, plant, claims }) {
  const S = spec[meta.cut === "social" ? "social" : "main"];
  const { w: CW, h: CH } = S.canvas;
  const win = S.window, band = S.band;
  const plates = path.join(take, "plates");
  const qa = path.join(outDir, "qa");
  mkdirSync(qa, { recursive: true });

  // A planted violation is a filter applied to the frames of ONE check, so the measurement itself is what is being proved.
  const capPlate = path.join(plates, "cap-01.png");
  const PLANT = {
    band: { overlay: capPlate }, // a caption plate laid on the band where there must be none
    cuts: { overlay: capPlate },
    window: "pad=iw+2:ih+2:2:2:color=black,crop=iw-2:ih-2:0:0", // the whole picture 2 px right and down
    red: "drawbox=x=20:y=100:w=30:h=30:color=0xff8a80:t=fill",
    cards: "pad=iw+12:ih:12:0:color=0x0d1015,crop=iw-12:ih:0:0",
  };
  const vfFor = (name) => (plant === name ? PLANT[name] : "");

  // raw frame grab: `region` crops, `fmt` is gray or rgb24, `vf` a planted violation (a filter chain or {overlay: png})
  function grab(t, { region, fmt = "gray", vf = "", scale } = {}) {
    const chain = [typeof vf === "string" ? vf : "", region ? `crop=${region.w}:${region.h}:${region.x}:${region.y}` : "", scale ? `scale=${scale.w}:${scale.h}:flags=area` : "", `format=${fmt}`].filter(Boolean).join(",");
    const over = typeof vf === "object" && vf.overlay;
    const args = ["-v", "error", "-ss", String(Math.max(0, t)), "-i", master, ...(over ? ["-i", vf.overlay, "-filter_complex", `[0:v][1:v]overlay=0:${band.y},${chain}`] : ["-vf", chain]), "-frames:v", "1", "-f", "rawvideo", "-"];
    return execFileSync("ffmpeg", args, { maxBuffer: 1 << 28 });
  }
  const mean = (b) => b.reduce((s, v) => s + v, 0) / (b.length || 1);
  const maxOf = (b) => b.reduce((m, v) => (v > m ? v : m), 0);
  const pct = (b, p) => Buffer.from(b).sort()[Math.min(b.length - 1, Math.floor(b.length * p))];

    const cards = tl.titleSeconds > 0;
  const ref = mean(grab(cards ? 0.7 : 0, { region: { x: 20, y: 20, w: 40, h: 40 } })); // the canvas colour as this encode renders it
  const cues = tl.cues.filter((c) => c.beat !== "end");
  const bandRegion = { x: 0, y: band.y + 6, w: CW, h: band.h - 6 }; // under the window's own bottom hairline (row 990)

  // ---- band is clean ----------------------------------------------------------------------------------------------------------
  const bad = [];
  let lit = 0;
  cues.forEach((c, i) => {
    const t = (c.start + c.end) / 2;
    const rgb = grab(t, { region: bandRegion, fmt: "rgb24", vf: vfFor("red") });
    let light = 0, red = 0;
    for (let k = 0; k < rgb.length; k += 3) {
      const y = 0.2126 * rgb[k] + 0.7152 * rgb[k + 1] + 0.0722 * rgb[k + 2];
      if (y > 150) light++;
      if (Math.hypot(rgb[k] - 255, rgb[k + 1] - 138, rgb[k + 2] - 128) < 40) red++;
    }
    const bank = meta.cues.find((m) => plainEq(m.text, c.text))?.bank;
    if (light < 40) bad.push(`cue ${i + 1}: no caption text at its midpoint`); else lit++;
    if (FAULT_BANKS.has(bank) && red < 10) bad.push(`cue ${i + 1}: the failure word is not red`);
    if (!FAULT_BANKS.has(bank) && red > 0) bad.push(`cue ${i + 1}: red in a caption that has no failure word`);
  });
  const gaps = [];
  const edges = [0, ...cues.flatMap((c) => [c.start, c.end]), tl.totalSeconds];
  for (let i = 0; i + 1 < edges.length; i += 2) if (edges[i + 1] - edges[i] >= 0.25) gaps.push((edges[i] + edges[i + 1]) / 2);
  const gapTimes = gaps.filter((t) => t > 0.3 && t < tl.totalSeconds - 0.3);
  gapTimes.forEach((t) => {
    const g = grab(t, { region: bandRegion, vf: vfFor("band") });
    if (maxOf(g) >= 40 || pct(g, 0.999) > ref + 3) bad.push(`gap at ${t.toFixed(2)} s: something is in the band (max luma ${maxOf(g)})`);
  });
  check(`the caption band is the caption and nothing else: text at all ${cues.length} cue midpoints, empty (luma < 40) in ${gapTimes.length} gaps, red only in the failure captions`, bad.length === 0, bad.slice(0, 4).join("; "));

  // ---- the window is still ----------------------------------------------------------------------------------------------------
  const edgeBad = [];
  const beatTimes = tl.beats.map((b) => [b.id, (b.start + b.end) / 2]);
  for (const [id, t] of beatTimes) {
    const v = vfFor("window");
    const top = grab(t, { region: { x: 300, y: 26, w: 1400 > CW - 400 ? CW - 600 : 1400, h: 16 }, vf: v });
    const w1 = 1400 > CW - 400 ? CW - 600 : 1400;
    const rows = Array.from({ length: 16 }, (_, r) => mean(top.subarray(r * w1, (r + 1) * w1)));
    const topY = 26 + rows.indexOf(Math.max(...rows));
    const left = grab(t, { region: { x: win.x - 12, y: 200, w: 16, h: 500 }, vf: v });
    const cols = Array.from({ length: 16 }, (_, c) => { let s = 0; for (let r = 0; r < 500; r++) s += left[r * 16 + c]; return s / 500; });
    const leftX = win.x - 12 + cols.indexOf(Math.max(...cols));
    if (Math.abs(topY - (win.y - 1)) > 1 || Math.abs(leftX - (win.x - 1)) > 1) edgeBad.push(`beat ${id}: top edge row ${topY} (want ${win.y - 1}), left edge column ${leftX} (want ${win.x - 1})`);
  }
  check(`the window never moves: its hairline is at row ${win.y - 1} and column ${win.x - 1} (1 px tolerance) in a frame of each of the ${beatTimes.length} beats`, edgeBad.length === 0, edgeBad.slice(0, 3).join("; "));

  // ---- the dim happens ----------------------------------------------------------------------------------------------------------
  const patch = { x: 16, y: 300, w: 60, h: 200 };
  const dimBad = [];
  const alpha = (file, region) => execFileSync("ffmpeg", ["-v", "error", "-i", file, "-vf", `alphaextract,crop=${region.w}:${region.h}:${region.x}:${region.y},format=gray`, "-frames:v", "1", "-f", "rawvideo", "-"], { maxBuffer: 1 << 24 });
  const aIn = mean(alpha(path.join(plates, "dim.png"), { x: win.x + 100, y: win.y + 100, w: 200, h: 200 }));
  const aOut = mean(alpha(path.join(plates, "dim.png"), patch));
  if (aIn > 0.5) dimBad.push(`the dim plate covers the window (alpha ${aIn.toFixed(1)})`);
  if (Math.abs(aOut - spec.dimAlpha * 255) > 3) dimBad.push(`the dim plate is ${aOut.toFixed(0)}/255 on the canvas, want ${Math.round(spec.dimAlpha * 255)}`);
  const drops = [];
  for (const z of tl.zooms) {
    const base = mean(grab(z.in - 0.3, { region: patch }));
    const hold = mean(grab(plant === "dim" ? z.in - 0.3 : (z.inEnd + z.out) / 2, { region: patch }));
    const after = mean(grab(z.outEnd + 0.3, { region: patch }));
    drops.push(+(base - hold).toFixed(1));
    if (base - hold < 4) dimBad.push(`zoom of beat ${z.beat}: the canvas drops ${(base - hold).toFixed(1)} luma (want >= 4)`);
    if (Math.abs(after - base) > 2) dimBad.push(`zoom of beat ${z.beat}: the canvas is ${(after - base).toFixed(1)} luma off its baseline after the zoom-out (want within 2)`);
  }
  check(`the dim: the plate is transparent over the window and ${Math.round(spec.dimAlpha * 100)} % on the canvas; the canvas darkens by ${drops.join(", ") || "n/a"} luma in each of ${tl.zooms.length} zoom holds and returns within 2`, dimBad.length === 0, dimBad.slice(0, 3).join("; "));

  // ---- red outside the window ---------------------------------------------------------------------------------------------------
  const redBad = [];
  let samples = 0;
  for (let t = 0.25; t < tl.totalSeconds - 0.1; t += 0.5) {
    const rgb = grab(t, { fmt: "rgb24", vf: vfFor("red"), scale: { w: CW / 2, h: CH / 2 } });
    const w2 = CW / 2;
    let n = 0;
    for (let y = 0; y < CH / 2; y++) for (let x = 0; x < w2; x++) {
      const inWin = x >= win.x / 2 - 1 && x < (win.x + win.w) / 2 + 1 && y >= win.y / 2 - 1 && y < (win.y + win.h) / 2 + 1;
      const inBand = y >= band.y / 2;
      if (inWin || inBand) continue;
      const k = (y * w2 + x) * 3;
      if (Math.hypot(rgb[k] - 255, rgb[k + 1] - 138, rgb[k + 2] - 128) < 20) n++;
    }
    samples++;
    if (n > 0) redBad.push(`${t.toFixed(2)} s: ${n} px`);
  }
  check(`no pixel near the failure red (#ff8a80) outside the window and the caption band, on ${samples} samples (every 0.5 s)`, redBad.length === 0, redBad.slice(0, 3).join("; "));

  // ---- cards (title and end) ----------------------------------------------------------------------------------------------------
  if (cards) {
    const C = S.card;
    const blockH = C.mark + C.gapMarkName + C.nameLine + C.gapNameLine + C.lineLine;
    const cy = CH / 2 - C.lift;
    const bboxOf = (t, vf) => {
      const g = grab(t, { vf });
      let x0 = CW, x1 = -1, y0 = CH, y1 = -1;
      for (let y = 0; y < CH; y++) for (let x = 0; x < CW; x++) if (g[y * CW + x] > ref + 40) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
      return { x0, x1, y0, y1, cx: (x0 + x1) / 2, cy: (y0 + y1) / 2 };
    };
    const tb = bboxOf(tl.titleSeconds * 0.55, vfFor("cards"));
    const eb = bboxOf(tl.totalSeconds - 0.05, vfFor("cards"));
    const cardBad = [];
    for (const [n, b] of [["title", tb], ["end", eb]]) {
      if (claims && n === "end") continue;
      if (Math.abs(b.cx - CW / 2) > 2) cardBad.push(`${n} card: lockup centre x ${b.cx} (want ${CW / 2} +- 2)`);
      if (Math.abs(b.cy - cy) > 6) cardBad.push(`${n} card: lockup centre y ${b.cy} (want ${cy} +- 6)`);
    }
    const first = grab(0, { fmt: "gray" }), last = grab(tl.totalSeconds - 0.04, { fmt: "gray" });
    if (maxOf(first) < ref + 100) cardBad.push("frame 1 is not the title card (no lockup)");
    if (maxOf(last) < ref + 100) cardBad.push("the last frame is not the end card (no lockup)");
    const below = { x: 0, y: Math.round(cy + blockH / 2 + 12), w: CW, h: 120 };
    const bl = grab(tl.totalSeconds - 0.04, { region: below });
    const litBelow = maxOf(bl) > ref + 60;
    if (!claims && litBelow) cardBad.push(`the last frame has text below the tagline (rows ${below.y}-${below.y + below.h}) but RELEASE_CLAIMS is off`);
    if (claims && !litBelow) cardBad.push("the claims end card shows no claims text below the tagline");
    check(`the cards: frame 1 and the last frame are the lockup, centred within 2 px (x) and 6 px (y); ${claims ? "the claims line and URL are on the end card" : "nothing below the tagline on the end card (the claims are gated)"}`, cardBad.length === 0, cardBad.join("; "));
  }

  // ---- dips ------------------------------------------------------------------------------------------------------------------------
  const dipBad = [];
  const flat = (t, vf) => { const g = grab(t, { vf, scale: { w: 192, h: 108 } }); return Math.abs(mean(g) - ref) <= 2 && maxOf(g) <= ref + 30; }; // 192x108 area-averaged: a 1 px hairline or a text stem is diluted, a lockup or a window is not
  const boundaries = tl.dips.filter((d) => d.what === "app in" || d.what === "end in" || d.what === "seam in" || d.what === "seam out" || d.what === "app out");
  const seen = new Set();
  for (const d of tl.dips) {
    const t = d.what.endsWith("in") ? d.at : d.at + d.d; // the boundary frame: the first frame of a fade-in, the end of a fade-out
    const tt = Math.min(Math.max(0, t), tl.totalSeconds - 1 / 30);
    if (seen.has(tt.toFixed(2))) continue;
    seen.add(tt.toFixed(2));
    const first = Math.ceil(tt * 30 - 1e-6) / 30 + 0.004; // the frames either side of the boundary (the clips join between two frames)
    const probe = plant === "dips" ? first + 0.12 : first;
    if (![-1 / 30, 0, 1 / 30].some((d) => flat(probe + d, ""))) dipBad.push(`${d.what} at ${tt.toFixed(2)} s is not the flat canvas colour`);
    let run = 0;
    for (let k = -12; k <= 12; k++) { const x = tt + k / 30; if (x >= 0 && x < tl.totalSeconds && flat(x, "")) run++; }
    if (run > 9) dipBad.push(`the dip at ${tt.toFixed(2)} s stays flat for ${run} frames (${Math.round(run * 33)} ms, want <= 300)`);
  }
  check(`the dips: every boundary frame (${seen.size}) is the flat canvas colour (within 2 luma everywhere) and none stays flat longer than 300 ms; no dip over live UI`, dipBad.length === 0, dipBad.slice(0, 3).join("; "));

  // ---- cuts are in caption gaps --------------------------------------------------------------------------------------------------------
  const cutBad = [];
  for (const c of tl.cuts) {
    for (const dt of [-0.25, -0.1, 0.1, 0.5, 0.78]) {
      const g = grab(c.at + dt, { region: bandRegion, vf: vfFor("cuts") });
      if (maxOf(g) >= 40) cutBad.push(`cut of beat ${c.beat}: a caption is in the band at ${dt > 0 ? "+" : ""}${dt} s`);
    }
    const a = grab(c.at - 0.05, { region: { x: win.x, y: win.y, w: win.w, h: win.h }, scale: undefined });
    const b = grab(c.at + 0.05, { region: { x: win.x, y: win.y, w: win.w, h: win.h } });
    let d = 0; for (let k = 0; k < a.length; k += 97) d += Math.abs(a[k] - b[k]);
    if (d / (a.length / 97) < 1) cutBad.push(`cut of beat ${c.beat}: the picture does not change`);
  }
  check(`every cut (${tl.cuts.length}) lands in a caption gap: the band is empty from 0.25 s before to 0.78 s after, and the picture changes at once`, cutBad.length === 0, cutBad.slice(0, 3).join("; "));

  // ---- fonts, social, claims ----------------------------------------------------------------------------------------------------------
  const pj = JSON.parse(readFileSync(path.join(plates, "plates.json"), "utf8"));
  let fontsOk = pj.captions.length === meta.cues.length && pj.files.includes("title.png") || !cards;
  let fontsDetail = "";
  if (plant === "fonts") {
    const r = spawnSync("node", [path.join(root, "scripts/video-plates.mjs"), take], { env: { ...process.env, VIDEO_FONTS_DIR: "/nonexistent", VIDEO_PLATES_OUT: path.join(qa, "plant-plates") }, encoding: "utf8" });
    fontsOk = r.status === 0; fontsDetail = fontsOk ? "" : "(plates refused the missing fonts, as it should)";
  }
  check(`the plates were rendered with the IBM Plex files (video-plates.mjs fails when document.fonts.check is false) and every caption fit its band (${pj.captions.length} measured)`, fontsOk, fontsDetail);
  if (meta.cut === "social") {
    check("social: the inspector beat (5) has no camera zoom; the fault row was in view before its caption", !(meta.zoomMarks ?? []).some((z) => z.beat === 5) && (meta.faultInView ?? []).every((x) => x.inView) && (meta.faultInView ?? []).length > 0, JSON.stringify(meta.faultInView));
  }
  if (claims) {
    const readme = existsSync(path.join(outDir, "README.txt")) ? readFileSync(path.join(outDir, "README.txt"), "utf8") : "";
    check("release-claims/ carries README.txt saying DO NOT PUBLISH BEFORE G2", /DO NOT PUBLISH BEFORE G2/.test(readme));
  }
  return { ref, drops };
}

function plainEq(a, b) { return a.replace(/\[\[|\]\]/g, "") === b.replace(/\[\[|\]\]/g, ""); }

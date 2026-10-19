#!/usr/bin/env node
/*
 * video-qa.mjs: the checks of docs/design/launch/video-brief.md section 7.1 that a machine can make, on one recorded and encoded
 * take (a directory written by scripts/record-demo.mjs and scripts/encode-demo.sh). Looking at the frames is still the human
 * half: this script writes them to <take>/qa/ for that.
 *
 *   node scripts/video-qa.mjs video-out/main-<stamp>                       checks one take, exit 1 on any failure
 *   node scripts/video-qa.mjs video-out/main-<stamp> --compare other-take  also: determinism across two takes
 *
 * Checks: ffprobe of every deliverable (1920x1080 or the cut's size, 30/1, yuv420p, no audio, faststart, size budget); one frame
 * per caption midpoint with the caption box actually present (a caption frame without a caption fails); one frame per beat; the
 * red-pixel budget (a sample every 0.5 s: more than 0.3 % of a frame's pixels outside the caption band that look like the fault
 * colour fails the take); beat lengths within 150 ms of the table; the .vtt equal to the burned-in cues one for one; no forbidden
 * text on screen (the recorder's per-beat page-text scan); the transcript lists every beat; teaser: both ends are the flat canvas.
 * Writes qa/qa.json.
 */
import { spawn, spawnSync, execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { maxLumaStep, parseVtt, plain } from "./record-demo-lib.mjs";

const FLASH_STEP = 40;
function lumaSteps(file) {
  const out = execFileSync("ffmpeg", ["-v", "error", "-i", file, "-vf", "fps=30,scale=32:18:flags=area", "-f", "rawvideo", "-pix_fmt", "gray", "-"], { maxBuffer: 1 << 28 });
  const n = Math.floor(out.length / 576), means = [];
  for (let i = 0; i < n; i++) { let t = 0; for (let j = 0; j < 576; j++) t += out[i * 576 + j]; means.push(t / 576); }
  return { ...maxLumaStep(means), frames: n };
}
// Standalone: `node scripts/video-qa.mjs --luma <file.mp4>` prints the largest frame-to-frame brightness step of any video (the
// flash check on its own; used to prove it finds the one-frame dark-to-light switch of draft 1).
if (process.argv[2] === "--luma") {
  const { step, at, frames } = lumaSteps(process.argv[3]);
  console.log(`${frames} frames, largest brightness step ${step.toFixed(1)} (of 255) between frames ${at - 1} and ${at} (${((at - 1) / 30).toFixed(2)} s)`);
  process.exit(step > FLASH_STEP ? 1 : 0);
}
const take = process.argv[2];
if (!take || !existsSync(path.join(take, "meta.json"))) {
  console.error("usage: node scripts/video-qa.mjs <take directory> [--compare <other take directory>]");
  process.exit(2);
}
const compareIdx = process.argv.indexOf("--compare");
const other = compareIdx > 0 ? process.argv[compareIdx + 1] : null;
const meta = JSON.parse(readFileSync(path.join(take, "meta.json"), "utf8"));
// `--plant clip|overlap|flash`: a planted violation. The check it targets must FAIL on it (this is how the checks are proved).
const plant = process.argv.includes("--plant") ? process.argv[process.argv.indexOf("--plant") + 1] : null;
if (plant === "clip") meta.zoomStates = [...(meta.zoomStates ?? []), { beat: "planted", at: 0, scale: 1.4, window: { x0: 549, y0: 0, x1: 1920, y1: 771 }, clipped: ["Failed at step 6 after 17 s: lookup-ti"] }];
if (plant === "overlap") meta.cues[0].mid.overlap = { lines: 3, focus: true };
const qaDir = path.join(take, "qa");
mkdirSync(qaDir, { recursive: true });
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok, detail });
  console.log(`${ok ? "  ok  " : "  FAIL"} ${name}${detail ? `: ${detail}` : ""}`);
};
const master = path.join(take, "master-1080p30.mp4");
const W = meta.viewport.w, H = meta.viewport.h;

function frameAt(file, t, out) {
  execFileSync("ffmpeg", ["-v", "error", "-y", "-ss", String(t), "-i", file, "-frames:v", "1", out]);
}
function rawFrame(file, t, w = 960) {
  const h = Math.round((w * H) / W);
  const buf = execFileSync("ffmpeg", ["-v", "error", "-ss", String(t), "-i", file, "-frames:v", "1", "-vf", `scale=${w}:${h}:flags=area`, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], { maxBuffer: 1 << 28 });
  return { buf, w, h };
}
const probe = (f, entries) => execFileSync("ffprobe", ["-v", "error", ...entries, "-of", "default=nw=1", f]).toString().trim();

// ---- 1. deliverables -------------------------------------------------------------------------------------------------
const budgets = meta.cut === "teaser"
  ? { "teaser-1080p.mp4": 2.5, "teaser-1080p.webm": 1.5, "teaser-1080p-av1.mp4": 1, "teaser-960.gif": 4 }
  : { "demo-1080p.mp4": 12, "demo-1080p.webm": 8, "demo-1080p-av1.mp4": 5 };
const files = [];
for (const [name, budget] of Object.entries(budgets)) {
  const f = path.join(take, name);
  if (!existsSync(f)) { if (!name.includes("av1")) check(`${name} exists`, false); continue; }
  const mb = statSync(f).size / 1048576;
  const info = Object.fromEntries(probe(f, ["-select_streams", "v:0", "-show_entries", "stream=codec_name,width,height,r_frame_rate,pix_fmt"]).split("\n").map((l) => l.split("=")));
  const audio = probe(f, ["-select_streams", "a", "-show_entries", "stream=codec_name"]);
  const duration = Number(probe(f, ["-show_entries", "format=duration"]).split("=")[1]);
  const gif = name.endsWith(".gif");
  const sizeOk = gif ? info.width === "960" : info.width === String(W) && info.height === String(H);
  const rateOk = gif ? true : info.r_frame_rate === "30/1";
  const pixOk = gif ? true : info.pix_fmt === "yuv420p";
  let fast = true;
  if (name.endsWith(".mp4")) {
    const first = execFileSync("sh", ["-c", `ffprobe -v trace "${f}" 2>&1 | grep -o "type:'\\(moov\\|mdat\\)'" | head -1`]).toString().trim();
    fast = first === "type:'moov'";
  }
  files.push({ name, mb: +mb.toFixed(2), budget, ...info, duration, audio: audio !== "" });
  check(`${name}: ${info.codec_name} ${info.width}x${info.height} ${info.r_frame_rate} ${info.pix_fmt}, ${duration.toFixed(1)} s, ${mb.toFixed(2)} MB (budget ${budget} MB)`,
    sizeOk && rateOk && pixOk && audio === "" && fast && mb <= budget, !sizeOk ? "size" : !rateOk ? "frame rate" : !pixOk ? "pixel format" : audio !== "" ? "audio stream" : !fast ? "not faststart" : mb > budget ? "over budget" : "");
}
const poster = path.join(take, "poster.jpg");
if (existsSync(poster)) check(`poster.jpg ${Math.round(statSync(poster).size / 1024)} KB (budget 400)`, statSync(poster).size <= 400 * 1024);

// ---- 2. cues: burned in == sidecar, a frame per cue midpoint with the caption box present ----------------------------------
const vtt = parseVtt(readFileSync(path.join(take, "demo.vtt"), "utf8"));
const same = vtt.length === meta.cues.length && vtt.every((c, i) => c.text === plain(meta.cues[i].text) && Math.abs(c.start - meta.cues[i].start) < 0.002 && Math.abs(c.end - meta.cues[i].end) < 0.002);
check(`demo.vtt matches the ${meta.cues.length} burned-in cues one for one`, same);
const gaps = meta.cues.slice(1).map((c, i) => c.start - meta.cues[i].end);
check("captions are at least 250 ms apart", gaps.every((g) => g >= 0.24), `smallest gap ${Math.min(...gaps, 99).toFixed(2)} s`);
const reading = meta.cues.map((c) => ({ c, need: Math.min(6, Math.max(2, plain(c.text).length / 15 + 0.6)) })).filter((x) => x.c.end - x.c.start < x.need - 0.05);
check("every caption stays on screen at least max(2 s, characters / 15 + 0.6 s)", reading.length === 0, reading.map((x) => plain(x.c.text)).join("; "));

// The caption box: the recorder measured its rectangle in the page at the middle of each cue; here the pixels inside that rectangle
// must be a dark box with a population of light text pixels, and the rectangle must sit inside the safe areas (action-safe 5 %,
// title-safe 90 % of the width).
function boxPixels({ buf, w, h }, rect) {
  const x0 = Math.max(0, Math.round((rect.x / W) * w) + 2), x1 = Math.min(w, Math.round(((rect.x + rect.w) / W) * w) - 2);
  const y0 = Math.max(0, Math.round((rect.y / H) * h) + 2), y1 = Math.min(h, Math.round(((rect.y + rect.h) / H) * h) - 2);
  let dark = 0, light = 0, total = 0;
  for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) {
    const i = (y * w + x) * 3;
    const lum = 0.2126 * buf[i] + 0.7152 * buf[i + 1] + 0.0722 * buf[i + 2];
    total++;
    if (lum < 70) dark++;
    else if (lum > 170) light++;
  }
  return { dark: dark / (total || 1), light: light / (total || 1) };
}
const cueFrames = [];
const badBox = [];
meta.cues.forEach((c, i) => {
  const t = (c.start + c.end) / 2;
  const out = path.join(qaDir, `cue-${String(i + 1).padStart(2, "0")}.png`);
  frameAt(master, t, out);
  const rect = c.mid?.rect;
  const px = rect ? boxPixels(rawFrame(master, t, 1920), rect) : { dark: 0, light: 0 };
  const inSafe = !!rect && rect.x >= 0.05 * W && rect.x + rect.w <= 0.95 * W && rect.y >= 0.05 * H && rect.y + rect.h <= 0.95 * H && rect.w <= 0.9 * W;
  cueFrames.push({ file: out, t: +t.toFixed(2), text: plain(c.text), rect, ...px, inSafe });
  if (!(c.mid?.opacity > 0.95 && px.dark >= 0.55 && px.light >= 0.015 && px.light <= 0.4 && inSafe)) badBox.push(`${plain(c.text)} (dark ${px.dark.toFixed(2)}, light ${px.light.toFixed(3)}, safe ${inSafe})`);
});
check(`all ${cueFrames.length} cues show a dark caption box with text, fully opaque, inside the safe areas, at their midpoint`, badBox.length === 0, badBox.join("; "));
// Where the caption sits (polish pass): never on the thing the beat is about (a spotlight, a zoom target, what the cursor points at),
// and on a page at normal scale never on any line of text. Zoomed, the page is all text: the box then only keeps off the focus.
const covers = [];
meta.cues.forEach((c) => {
  for (const [when, m] of [["middle", c.mid], ["end", c.endMeasure]]) {
    if (!m?.overlap) { covers.push(`${plain(c.text)} (${when}: not measured)`); continue; }
    if (m.overlap.focus) covers.push(`${plain(c.text)} (${when}: on the focus)`);
    else if ((m.camera ?? 1) === 1 && m.overlap.lines > 0) covers.push(`${plain(c.text)} (${when}: over ${m.overlap.lines} line(s) of text)`);
  }
});
check(`no caption sits on the story's focus, or on text at normal scale (${meta.cues.length} cues, middle and end of each)`, covers.length === 0, covers.join("; "));
const zoomedCovers = meta.cues.filter((c) => (c.mid?.camera ?? 1) > 1).map((c) => `${plain(c.text)}: ${c.mid.overlap.lines} line(s)`);
if (zoomedCovers.length) console.log(`  note  zoomed captions cover background rows only: ${zoomedCovers.join("; ")}`);
const across = meta.beats.filter((b) => b.captionOnAtEnd);
check("no caption is still showing at the end of any beat (none across a cut)", across.length === 0, across.map((b) => b.id).join(", "));

// ---- 3. a frame per beat, beat lengths, text scan, transcript ----------------------------------------------------------------
meta.beats.forEach((b) => frameAt(master, (b.start + b.end) / 2, path.join(qaDir, `beat-${String(b.id).padStart(2, "0")}.png`)));
const off = meta.beats.filter((b) => Math.abs(b.end - b.start - b.plannedLen) > 0.15);
check("beat lengths within 150 ms of the table", off.length === 0, off.map((b) => `${b.id}: ${(b.end - b.start).toFixed(2)} vs ${b.plannedLen.toFixed(2)}`).join("; "));
check("no forbidden text (keys, tokens, emails other than owner@local.test, provider or model names, --live) on any beat", meta.textHits.length === 0, JSON.stringify(meta.textHits));
check("no console errors in the recorded page", meta.consoleErrors.length === 0, meta.consoleErrors.slice(0, 2).join(" | "));
check("the overlay mounted (cursor and caption elements present)", !!meta.overlay?.cur && !!meta.overlay?.cap);
const transcript = readFileSync(path.join(take, "transcript.md"), "utf8");
check("the transcript lists every beat", meta.beats.every((b) => transcript.includes(`\n${b.id}. `)));
if (!meta.releaseClaims) {
  const gated = meta.cues.filter((c) => /open source|self-hosted|apache|github\.com/i.test(c.text));
  check("the closing caption is the pre-G2 variant", gated.length === 0, gated.map((c) => c.text).join("; "));
}
const flat = meta.cues.filter((c) => /\blive\b|production|saas|enterprise/i.test(c.text));
check("no caption says live, production, SaaS or enterprise", flat.length === 0);

// ---- 3b. the camera: no zoomed state cuts a line of text mid-glyph; the hook; hops; length -------------------------------------------
const zs = meta.zoomStates ?? [];
const cutStates = zs.filter((z) => z.clipped.length > 0);
check(`no zoomed camera state cuts text mid-glyph (${zs.length} zoom states, every line of text measured at the settled frame)`, cutStates.length === 0,
  cutStates.map((z) => `beat ${z.beat} at ${z.at} s, ${z.scale}x window ${z.window.x0},${z.window.y0}-${z.window.x1},${z.window.y1}: "${z.clipped.slice(0, 3).join('", "')}"`).join("; "));
const zmin = meta.cut === "social" ? 1.09 : 1.29; // the narrow layout may need a gentler zoom to clear its text
check(`every zoom stays within ${meta.cut === "social" ? "1.1" : "1.3"}x-1.6x (the brief's ceiling is 1.6)`, zs.every((z) => z.scale >= zmin && z.scale <= 1.601), zs.map((z) => z.scale).join(", "));
if (meta.cut === "main") {
  check(`the main cut is 60-75 s (${meta.seconds.toFixed(1)} s)`, meta.seconds >= 60 && meta.seconds <= 75);
  check("the first caption is on screen within 0.5 s (the hook says what the product is at once)", meta.cues[0].start <= 0.5, `${meta.cues[0].start.toFixed(2)} s`);
  check("the film opens on a zoomed frame of the failed run (the hook), not a table", zs.length > 0 && zs[0].beat === 1 && zs[0].at <= 0.5);
  const slow = (meta.hops ?? []).filter((h) => h.seconds > 1.1);
  check(`a section change (hop) takes at most 1.1 s (${(meta.hops ?? []).length} hops)`, slow.length === 0, slow.map((h) => `beat ${h.beat}: ${h.seconds} s`).join("; "));
}
{
  const { step, at, frames } = plant === "flash" ? { step: 185, at: 1, frames: 0 } : lumaSteps(master);
  check(`no flash: the largest frame-to-frame brightness step is under ${FLASH_STEP} of 255 (${frames} frames)`, step < FLASH_STEP, `largest ${step.toFixed(1)} at ${((at - 1) / 30).toFixed(2)} s`);
}
const pt = (n) => { try { return readFileSync(path.join(take, "pagetext", `beat-${n}.txt`), "utf8"); } catch { return ""; } };
const wm = meta.beats.filter((b) => { const t = pt(b.id); return t && (/Agent Black Box/.test(t) || !/Agent BlackBox/.test(t)); });
check("the app's own wordmark reads Agent BlackBox on every beat (captions and card say the same)", wm.length === 0, wm.map((b) => b.id).join(", "));

// ---- 4. red budget: a sample every 0.5 s -------------------------------------------------------------------------------------
await new Promise((resolve) => {
  const w = 480, h = Math.round((w * H) / W), size = w * h * 3;
  const p = spawn("ffmpeg", ["-v", "error", "-i", master, "-vf", `fps=2,scale=${w}:${h}:flags=area`, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]);
  let pending = Buffer.alloc(0), n = 0, worst = { share: 0, t: 0 };
  const redFrames = [];
  p.stdout.on("data", (chunk) => {
    pending = Buffer.concat([pending, chunk]);
    while (pending.length >= size) {
      const f = pending.subarray(0, size);
      pending = pending.subarray(size);
      let red = 0;
      const bandTop = Math.round(h * ((H - 84 - 66) / H)); // the caption box has its own red words (allowed)
      for (let y = 0; y < bandTop; y++) {
        let row = 0;
        for (let x = 0; x < w; x++) {
          const i = (y * w + x) * 3;
          if (f[i] > 120 && f[i] - f[i + 1] > 60 && f[i] - f[i + 2] > 50) row++;
        }
        if (row / w < 0.5) red += row; // a row that is red across most of its width is the fault line itself (allowed)
      }
      const share = red / (w * bandTop);
      if (share > worst.share) worst = { share, t: n * 0.5 };
      if (share > 0.003) redFrames.push(n * 0.5);
      n++;
    }
  });
  p.on("close", () => {
    check(`red pixels (outside the fault line and the caption) stay under 0.3 % of the frame on all ${n} samples`, redFrames.length === 0, `worst ${(worst.share * 100).toFixed(3)} % at ${worst.t.toFixed(1)} s${redFrames.length ? `; over at ${redFrames.join(", ")} s` : ""}`);
    resolve();
  });
});

// ---- 5. teaser loop ends ---------------------------------------------------------------------------------------------------
if (meta.cut === "teaser") {
  const dur = Number(probe(master, ["-show_entries", "format=duration"]).split("=")[1]);
  const mean = ({ buf }) => [0, 1, 2].map((c) => { let s = 0; for (let i = c; i < buf.length; i += 3) s += buf[i]; return s / (buf.length / 3); });
  const first = mean(rawFrame(master, 0.0, 160)), last = mean(rawFrame(master, dur - 0.05, 160));
  const near = (m) => Math.abs(m[0] - 16) < 6 && Math.abs(m[1] - 20) < 6 && Math.abs(m[2] - 25) < 6;
  check("the teaser starts and ends on the flat canvas colour (the loop is a dip, not a jump)", near(first) && near(last), `first ${first.map(Math.round)} last ${last.map(Math.round)}`);
}

// ---- 6. determinism across two takes ---------------------------------------------------------------------------------------------
if (other) {
  const m2 = JSON.parse(readFileSync(path.join(other, "meta.json"), "utf8"));
  const key = (m) => JSON.stringify({ cues: m.cues.map((c) => [c.beat, plain(c.text)]), scrape: m.scrape, beats: m.beats.map((b) => b.id) });
  check("two takes: the same beats, captions and scraped page sentences", key(meta) === key(m2));
  check(`two takes: frame counts within 5 % (${meta.frames} and ${m2.frames})`, Math.abs(meta.frames - m2.frames) / meta.frames <= 0.05);
  const dl = meta.beats.map((b, i) => Math.abs((b.end - b.start) - (m2.beats[i].end - m2.beats[i].start)));
  check("two takes: every beat lasts the same within 150 ms", Math.max(...dl) <= 0.15, `largest difference ${Math.max(...dl).toFixed(3)} s`);
  const master2 = path.join(other, "master-1080p30.mp4");
  if (existsSync(master2)) {
    const low = [];
    for (const b of meta.beats) {
      // Three frames per beat (a quarter, half and three quarters in): a transition (a zoom, a dip) may be a few ms apart between two
      // takes at one of them; a beat is the same picture when its best frame matches.
      let best = 0, bestAt = 0;
      for (const f of [0.25, 0.5, 0.75]) {
        const t = b.start + (b.end - b.start) * f;
        const a = path.join(qaDir, `det-a-${b.id}.png`), c = path.join(qaDir, `det-b-${b.id}.png`);
        frameAt(master, t, a);
        frameAt(master2, t, c);
        const r = spawnSync("ffmpeg", ["-i", a, "-i", c, "-lavfi", "ssim", "-f", "null", "-"]); // ssim is printed on stderr
        const m = /All:([0-9.]+)/.exec(r.stderr.toString());
        if (m && Number(m[1]) > best) { best = Number(m[1]); bestAt = t; }
      }
      if (best < 0.97) low.push(`beat ${b.id} (best ${best.toFixed(3)} at ${bestAt.toFixed(1)} s)`);
    }
    check("two takes: a frame of every beat is the same picture in both takes (SSIM at least 0.97 at one of three sample times; relative times and cursor positions may differ)", low.length === 0, low.join("; "));
  }
}

writeFileSync(path.join(qaDir, "qa.json"), JSON.stringify({ take: path.basename(take), meta: { cut: meta.cut, seconds: meta.seconds, frames: meta.frames }, files, cueFrames, results }, null, 2));
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed; frames and qa.json in ${qaDir}`);
process.exit(failed.length ? 1 : 0);


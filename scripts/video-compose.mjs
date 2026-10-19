#!/usr/bin/env node
/*
 * video-compose.mjs: the film's compositor (docs/design/launch/video-cinematic-brief.md section 6). From a take written by
 * scripts/record-demo.mjs (--captions=off --card=off) and its plates (scripts/video-plates.mjs) it builds one ffmpeg graph:
 *
 *   canvas plate + the app frames scaled into the window  ->  dim plate faded in/out around each camera zoom
 *   -> one caption plate per cue (opacity fades only)       ->  dips through the canvas colour at the ends
 *   -> title card + app + end card (main, social) or the app alone with a dip at both ends (teaser)
 *
 * Every time comes from meta.json (cues, zoomMarks), so the same take always composes to the same picture. Writes
 * <out>/master-1080p30.mp4 (CRF 14, never published), <out>/compose.json (the timeline in film seconds, read by the QA), and the
 * sidecars demo.vtt, demo.srt, transcript.md and meta.json shifted to film time.
 *
 *   node scripts/video-compose.mjs <take>                 the default build, into <take>
 *   RELEASE_CLAIMS=1 node scripts/video-compose.mjs <take> --claims    ONLY the claims end card, into <take>/release-claims
 *
 * The claims variant is a separate folder with a README.txt that says DO NOT PUBLISH BEFORE G2 (the repository is public); the
 * default build never reads end-claims.png even if a plates run with the flag left it there.
 */
import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildSrt, buildVtt, plain } from "./record-demo-lib.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const take = path.resolve(process.argv[2] ?? "");
if (!process.argv[2] || !existsSync(path.join(take, "meta.json"))) {
  console.error("usage: node scripts/video-compose.mjs <take directory> [--claims]");
  process.exit(2);
}
const claims = process.argv.includes("--claims");
if (claims && process.env.RELEASE_CLAIMS !== "1") {
  console.error("video-compose: --claims needs RELEASE_CLAIMS=1 in the environment (the claims card is gated on G2)");
  process.exit(2);
}
const meta = JSON.parse(readFileSync(path.join(take, "meta.json"), "utf8"));
if (meta.film !== "cinematic") {
  console.error("video-compose: this take was recorded with captions or cards in the page; record with --captions=off --card=off");
  process.exit(2);
}
const SPEC = JSON.parse(readFileSync(path.join(root, "scripts/video-spec.json"), "utf8"));
const S = SPEC[meta.cut === "social" ? "social" : "main"];
const T = SPEC.time;
const teaser = meta.cut === "teaser";
const plates = path.join(take, "plates");
const out = claims ? path.join(take, "release-claims") : take;
mkdirSync(out, { recursive: true });
const need = (f) => { const p = path.join(plates, f); if (!existsSync(p)) { console.error(`video-compose: missing plate ${p} (run scripts/video-plates.mjs)`); process.exit(2); } return p; };

const L = meta.seconds; // the app segment, film seconds
const CV = SPEC.color.canvas.replace("#", "0x");
const round = (x) => Number(x.toFixed(3));
const cards = !teaser;
const titleLen = cards ? T.title : 0;
const endLen = cards ? (claims ? T.endClaims : T.end) : 0;
const endFile = claims ? "end-claims.png" : "end.png"; // the default build never names end-claims.png
const total = titleLen + L + endLen;

const inputs = [];
const input = (args) => { inputs.push(args); return inputs.length - 1; };
const loop = (file, len) => input(["-loop", "1", "-framerate", "30", "-t", String(round(len)), "-i", file]);
const iCanvas = loop(need("canvas.png"), L);
const iApp = input(["-f", "concat", "-safe", "0", "-i", path.join(take, "frames.ffconcat")]);
const zooms = (meta.zoomMarks ?? []).filter((z) => z.out !== null);
const iDim = zooms.map(() => loop(need("dim.png"), L));
const iCap = meta.cues.map((_, i) => loop(need(`cap-${String(i + 1).padStart(2, "0")}.png`), L));
const iTitle = cards ? loop(need("title.png"), titleLen) : -1;
const iEnd = cards ? loop(need(endFile), endLen) : -1;

const f = [];
f.push(`[${iApp}:v]scale=${S.window.w}:${S.window.h}:flags=lanczos+accurate_rnd:in_range=pc:out_range=tv,fps=30,setsar=1,format=yuv420p[app]`);
f.push(`[${iCanvas}:v]format=rgb24[cv]`);
f.push(`[cv][app]overlay=${S.window.x}:${S.window.y}:shortest=1:format=auto[st0]`);
let cur = "st0";
zooms.forEach((z, k) => {
  f.push(`[${iDim[k]}:v]format=rgba,fade=t=in:st=${round(z.in)}:d=${round(z.inEnd - z.in)}:alpha=1,fade=t=out:st=${round(z.out)}:d=${round(z.outEnd - z.out)}:alpha=1[dim${k}]`);
  f.push(`[${cur}][dim${k}]overlay=shortest=1:enable='between(t,${round(z.in)},${round(z.outEnd)})'[d${k}]`);
  cur = `d${k}`;
});
meta.cues.forEach((c, i) => {
  const a = round(c.start), b = round(c.end);
  f.push(`[${iCap[i]}:v]format=rgba,fade=t=in:st=${a}:d=${T.captionIn}:alpha=1,fade=t=out:st=${round(b - T.captionOut)}:d=${T.captionOut}:alpha=1[cap${i}]`);
  f.push(`[${cur}][cap${i}]overlay=0:${S.band.y}:shortest=1:enable='between(t,${a},${b})'[c${i}]`);
  cur = `c${i}`;
});
const dip = T.dip;
f.push(`[${cur}]trim=duration=${round(L)},setpts=PTS-STARTPTS,fade=t=in:st=0:d=${dip}:color=${CV},fade=t=out:st=${round(L - dip)}:d=${dip}:color=${CV},format=yuv420p[main]`);
if (cards) {
  f.push(`[${iTitle}:v]fps=30,fade=t=out:st=${round(titleLen - dip)}:d=${dip}:color=${CV},format=yuv420p[ti]`);
  f.push(`[${iEnd}:v]fps=30,fade=t=in:st=0:d=${dip}:color=${CV},format=yuv420p[en]`);
  f.push(`[ti][main][en]concat=n=3:v=1:a=0[v]`);
} else {
  f.push(`[main]null[v]`);
}
const script = path.join(out, "compose.filter");
writeFileSync(script, f.join(";\n"));
const args = ["-hide_banner", "-loglevel", "error", "-y", ...inputs.flat(), "-/filter_complex", script, "-map", "[v]",
  "-c:v", "libx264", "-preset", "slow", "-crf", "14", "-g", "60", "-keyint_min", "30", "-pix_fmt", "yuv420p", "-color_range", "tv", "-an", path.join(out, "master-1080p30.mp4")];
const t0 = Date.now();
const r = spawnSync("ffmpeg", args, { stdio: ["ignore", "inherit", "inherit"] });
if (r.status !== 0) { console.error("video-compose: ffmpeg failed"); process.exit(1); }

// ---- the timeline in film seconds, for the QA and the sidecars ---------------------------------------------------------------
const at = (x) => round(x + titleLen);
const endCues = claims
  ? [{ text: SPEC.claims.line, start: round(titleLen + L + dip), end: round(total) }, { text: SPEC.claims.url, start: round(titleLen + L + dip), end: round(total) }]
  : [];
const cues = [...meta.cues.map((c) => ({ beat: c.beat, text: c.text, start: at(c.start), end: at(c.end) })), ...endCues.map((c) => ({ beat: "end", ...c }))];
const timeline = {
  cut: meta.cut, claims, canvas: S.canvas, window: S.window, band: S.band, dim: SPEC.dimAlpha, dip,
  titleSeconds: titleLen, appSeconds: round(L), endSeconds: endLen, totalSeconds: round(total),
  cues, zooms: zooms.map((z) => ({ beat: z.beat, level: z.level, in: at(z.in), inEnd: at(z.inEnd), out: at(z.out), outEnd: at(z.outEnd) })),
  cuts: (meta.hops ?? []).map((h) => ({ beat: h.beat, at: at(h.at) })),
  beats: meta.beats.map((b) => ({ id: b.id, start: at(b.start), end: at(b.end) })),
  dips: cards
    ? [{ at: round(titleLen - dip), d: dip, what: "title out" }, { at: titleLen, d: dip, what: "app in" }, { at: round(titleLen + L - dip), d: dip, what: "app out" }, { at: round(titleLen + L), d: dip, what: "end in" }]
    : [{ at: 0, d: dip, what: "seam in" }, { at: round(L - dip), d: dip, what: "seam out" }],
};
writeFileSync(path.join(out, "compose.json"), JSON.stringify(timeline, null, 2));
const vttCues = cues.map((c) => ({ start: c.start, end: c.end, text: c.text }));
writeFileSync(path.join(out, "demo.vtt"), buildVtt(vttCues));
writeFileSync(path.join(out, "demo.srt"), buildSrt(vttCues));
if (claims) {
  copyFileSync(path.join(take, "meta.json"), path.join(out, "meta.json"));
  const tr = readFileSync(path.join(take, "transcript.md"), "utf8").trimEnd();
  writeFileSync(path.join(out, "transcript.md"), `${tr}\n\nEnd card. ${SPEC.claims.line} ${SPEC.claims.url}\n`);
  writeFileSync(path.join(out, "README.txt"), [
    "DO NOT PUBLISH BEFORE G2 (the repository is public).",
    "",
    "This folder holds the claims variant of the launch film: the same footage as the parent folder, with an end card that says",
    `"${SPEC.claims.line}" and shows ${SPEC.claims.url} (captions 26 and 28 of the positioning brief). Those statements are gated on`,
    "G2 (repository public) and on the acceptance criteria AC1 and AC12 being VERIFIED (docs/plans/active/phase-20-release-polish.md,",
    "claims table). Until then publish only the files in the parent folder.",
    "",
    "It exists only because the build ran with RELEASE_CLAIMS=1; a default build never writes this folder.",
    "",
  ].join("\n"));
}
console.log(`video-compose: ${path.relative(process.cwd(), path.join(out, "master-1080p30.mp4"))} ${total.toFixed(2)} s (title ${titleLen}, app ${L.toFixed(2)}, end ${endLen}) in ${((Date.now() - t0) / 1000).toFixed(1)} s${claims ? " [claims variant]" : ""}`);

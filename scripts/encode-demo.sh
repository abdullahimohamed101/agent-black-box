#!/usr/bin/env bash
# Encode a recorded take (scripts/record-demo.mjs) into every deliverable, then check each against its size budget.
#   scripts/encode-demo.sh video-out/main-<stamp>        the main cut
#   scripts/encode-demo.sh video-out/teaser-<stamp>      the teaser loop (also a README GIF)
# From <take>/frames.ffconcat (3840x2160 JPEG frames with real durations) it makes ONE master (lanczos downscale to 1080p, 30 fps
# constant, CRF 14; never published) and every file below comes from that master (docs/design/launch/video-brief.md section 5):
#   main:   demo-1080p.mp4 (H.264, faststart), demo-1080p.webm (VP9, two pass), demo-1080p-av1.mp4 (only when it beats VP9),
#           poster.jpg and poster.png (the red-line beat; this ffmpeg has no libwebp encoder)
#   teaser: teaser-1080p.mp4, teaser-1080p.webm, teaser-1080p-av1.mp4 (when it beats VP9), teaser-960.gif, poster.jpg
# demo.vtt, demo.srt, transcript.md and meta.json are already in the take directory (the recorder writes them from the real cue
# times). No audio track in any file (D17: silent, captions only). Prints one ffprobe line per output; exits 1 on a budget overrun.
# Environment: ENC_VP9_SPEED (default 2; the brief's 1 is smaller and about twice as slow on 2 CPUs).
set -euo pipefail

take="${1:-}"
if [[ -z "$take" || ! -f "$take/frames.ffconcat" || ! -f "$take/meta.json" ]]; then
  echo "usage: scripts/encode-demo.sh <take directory written by scripts/record-demo.mjs>" >&2
  exit 2
fi
for tool in ffmpeg ffprobe node; do
  command -v "$tool" >/dev/null 2>&1 || { echo "encode-demo: $tool is not installed (macOS: brew install ffmpeg)" >&2; exit 3; }
done
enc="$(ffmpeg -hide_banner -encoders 2>/dev/null)"
grep -q libx264 <<<"$enc" || { echo "encode-demo: this ffmpeg has no libx264" >&2; exit 3; }
have_vp9=0; grep -q libvpx-vp9 <<<"$enc" && have_vp9=1
have_av1=0; grep -q libsvtav1 <<<"$enc" && have_av1=1

cd "$take"
cut="$(node -e 'console.log(JSON.parse(require("fs").readFileSync("meta.json","utf8")).cut)')"
speed="${ENC_VP9_SPEED:-2}"
fails=0
q=(ffmpeg -hide_banner -loglevel error -y)
VF="scale=1920:1080:flags=lanczos,fps=30,format=yuv420p"
[[ "$cut" == social ]] && VF="scale=1080:1350:flags=lanczos,fps=30,format=yuv420p"
size() { stat -f %z "$1" 2>/dev/null || stat -c %s "$1"; }

echo "==> master (${cut})"
"${q[@]}" -f concat -safe 0 -i frames.ffconcat -vf "$VF" -c:v libx264 -preset slow -crf 14 -g 60 -keyint_min 30 -an master-1080p30.mp4

report() {  # report <file> <budget MB>: one ffprobe line, size against budget
  local f="$1" budget="$2" mb line audio
  mb="$(awk -v b="$(size "$f")" 'BEGIN { printf "%.2f", b / 1048576 }')"
  line="$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,r_frame_rate,pix_fmt,duration -of csv=p=0 "$f" 2>/dev/null | tr ',' ' ')"
  audio="$(ffprobe -v error -select_streams a -show_entries stream=codec_name -of csv=p=0 "$f" 2>/dev/null | wc -l | tr -d ' ')"
  if awk -v m="$mb" -v b="$budget" 'BEGIN { exit !(m + 0 > b + 0) }'; then
    echo "  OVER BUDGET  $f  ${mb} MB > ${budget} MB  [$line audio-streams=$audio]"; fails=$((fails + 1))
  else
    echo "  ok           $f  ${mb} MB (budget ${budget} MB)  [$line audio-streams=$audio]"
  fi
  [[ "$audio" == 0 ]] || { echo "  AUDIO TRACK PRESENT in $f"; fails=$((fails + 1)); }
}
faststart() {  # the moov atom must come before mdat so a browser can start before the download ends
  local first; first="$(ffprobe -v trace "$1" 2>&1 | grep -o "type:'\(moov\|mdat\)'" | head -1)"
  if [[ "$first" == "type:'moov'" ]]; then echo "  faststart    $1 (moov before mdat)"; else echo "  NOT FASTSTART $1 (first atom: ${first:-none})"; fails=$((fails + 1)); fi
}
vp9() {  # vp9 <in> <out> <bitrate> <crf> <gop>
  [[ "$have_vp9" == 1 ]] || { echo "  (no libvpx-vp9: $2 skipped)"; return 0; }
  "${q[@]}" -i "$1" -c:v libvpx-vp9 -b:v "$3" -crf "$4" -tile-columns 2 -g "$5" -row-mt 1 -quality good -speed 4 -pass 1 -an -passlogfile vp9log -f null /dev/null
  "${q[@]}" -i "$1" -c:v libvpx-vp9 -b:v "$3" -crf "$4" -tile-columns 2 -g "$5" -row-mt 1 -quality good -speed "$speed" -pass 2 -an -passlogfile vp9log "$2"
  rm -f vp9log-*.log
}
av1() {  # av1 <in> <out> <crf> <gop> <vp9 file to beat> <budget MB>
  [[ "$have_av1" == 1 ]] || return 0
  "${q[@]}" -i "$1" -c:v libsvtav1 -preset 4 -crf "$3" -g "$4" -pix_fmt yuv420p -svtav1-params tune=0:fast-decode=1 -movflags +faststart -an "$2" 2>/dev/null
  if [[ -f "$5" && "$(size "$2")" -ge "$(size "$5")" ]]; then
    echo "  $2 is not smaller than the WebM: not shipped"; rm -f "$2"
  else
    report "$2" "$6"; faststart "$2"
  fi
}
# poster: the middle of the red-line beat (the one frame that says what the product is for)
poster_at="$(node -e 'const m=JSON.parse(require("fs").readFileSync("meta.json","utf8"));const b=m.beats.find(x=>x.id===5||x.id==="T3");console.log(((b?b.start+1.4:3)).toFixed(2))')"
poster_check() {
  for f in "$@"; do
    kb="$(awk -v b="$(size "$f")" 'BEGIN { printf "%.0f", b / 1024 }')"
    if [[ "$kb" -gt 400 ]]; then echo "  OVER BUDGET  $f ${kb} KB > 400 KB"; fails=$((fails + 1)); else echo "  ok           $f ${kb} KB (budget 400 KB)"; fi
  done
}

if [[ "$cut" == teaser ]]; then
  echo "==> teaser deliverables"
  "${q[@]}" -i master-1080p30.mp4 -c:v libx264 -preset slow -crf 21 -profile:v high -level 4.1 -g 60 -pix_fmt yuv420p -movflags +faststart -an teaser-1080p.mp4
  vp9 master-1080p30.mp4 teaser-1080p.webm 900k 33 180
  "${q[@]}" -i teaser-1080p.mp4 -vf "fps=12,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" teaser-960.gif
  "${q[@]}" -ss "$poster_at" -i master-1080p30.mp4 -frames:v 1 -q:v 3 poster.jpg
  report teaser-1080p.mp4 2.5; faststart teaser-1080p.mp4
  [[ -f teaser-1080p.webm ]] && report teaser-1080p.webm 1.5
  report teaser-960.gif 4
  av1 teaser-1080p.mp4 teaser-1080p-av1.mp4 36 180 teaser-1080p.webm 1
  poster_check poster.jpg
else
  echo "==> main deliverables"
  "${q[@]}" -i master-1080p30.mp4 -c:v libx264 -preset slow -crf 20 -profile:v high -level 4.1 -g 60 -keyint_min 30 -pix_fmt yuv420p -movflags +faststart -an demo-1080p.mp4
  vp9 master-1080p30.mp4 demo-1080p.webm 1200k 31 240
  "${q[@]}" -ss "$poster_at" -i master-1080p30.mp4 -frames:v 1 poster.png
  "${q[@]}" -ss "$poster_at" -i master-1080p30.mp4 -frames:v 1 -q:v 3 poster.jpg
  report demo-1080p.mp4 12; faststart demo-1080p.mp4
  [[ -f demo-1080p.webm ]] && report demo-1080p.webm 8
  av1 master-1080p30.mp4 demo-1080p-av1.mp4 36 240 demo-1080p.webm 5
  poster_check poster.jpg poster.png
fi

[[ "$fails" == 0 ]] || { echo "encode-demo: $fails problem(s) above" >&2; exit 1; }
echo "encode-demo: all outputs within budget (take: $take)"

#!/usr/bin/env bash
# Encode a recorded WebM (scripts/record-demo.mjs) into an H.264 MP4 and a poster PNG, next to the input.
#   scripts/encode-demo.sh video-out/demo-<stamp>.webm [poster-second]
# MP4: yuv420p, faststart, crf 22, 25 fps, no audio track (no music, no voice-over: D17). The lead-in before the first scene is
# trimmed using the sidecar <name>.meta.json the recorder writes. Needs ffmpeg with libx264.
set -euo pipefail

in="${1:-}"
poster_at="${2:-3}"
if [[ -z "$in" || ! -f "$in" ]]; then
  echo "usage: scripts/encode-demo.sh <recording.webm> [poster-second]" >&2
  exit 2
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "encode-demo: ffmpeg is not installed. Install it (macOS: brew install ffmpeg) and retry; the WebM is usable as is." >&2
  exit 3
fi
if ! ffmpeg -hide_banner -encoders 2>/dev/null | grep -q libx264; then
  echo "encode-demo: this ffmpeg has no libx264 encoder." >&2
  exit 3
fi

base="${in%.webm}"
trim=0
if [[ -f "$base.meta.json" ]] && command -v node >/dev/null 2>&1; then
  trim="$(node -e 'const m=JSON.parse(require("fs").readFileSync(process.argv[1],"utf8"));console.log((m.startMs/1000).toFixed(3))' "$base.meta.json")"
fi

ffmpeg -hide_banner -loglevel error -y -ss "$trim" -i "$in" \
  -an -c:v libx264 -preset slow -crf 22 -pix_fmt yuv420p -r 25 -movflags +faststart "$base.mp4"
ffmpeg -hide_banner -loglevel error -y -ss "$poster_at" -i "$base.mp4" -frames:v 1 "$base-poster.png"
echo "wrote $base.mp4 and $base-poster.png (trimmed ${trim}s lead-in)"

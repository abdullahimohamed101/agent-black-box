#!/usr/bin/env bash
# Encode the PoC frames (poc-record.mjs) into the delivery formats and extract check frames.
#   poc-encode.sh <out-dir>
set -euo pipefail
out="${1:?out dir}"
cd "$out"
# Master: lossless-ish 4K intermediate is skipped; we go straight from the JPEG frames (q92) to the deliverables.
# Downscale 3840x2160 -> 1920x1080 with lanczos, constant 30 fps, yuv420p, faststart.
VF="scale=1920:1080:flags=lanczos,fps=30,format=yuv420p"
ffmpeg -hide_banner -loglevel error -y -f concat -safe 0 -i frames.ffconcat -vf "$VF" \
  -c:v libx264 -preset slow -crf 18 -tune animation -x264-params keyint=60:min-keyint=30 -movflags +faststart -an poc.mp4
ffmpeg -hide_banner -loglevel error -y -f concat -safe 0 -i frames.ffconcat -vf "$VF" \
  -c:v libvpx-vp9 -b:v 0 -crf 30 -row-mt 1 -deadline good -cpu-used 2 -g 240 -an poc.webm
ffmpeg -hide_banner -loglevel error -y -f concat -safe 0 -i frames.ffconcat -vf "$VF" \
  -c:v libsvtav1 -crf 34 -preset 6 -svtav1-params tune=0:keyint=240 -an poc-av1.mp4
mkdir -p check
ffmpeg -hide_banner -loglevel error -y -i poc.mp4 -vf "fps=1" check/sec-%02d.png
for f in poc.mp4 poc.webm poc-av1.mp4; do
  printf '%s: ' "$f"; ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,r_frame_rate,pix_fmt,bit_rate:format=duration,size -of compact=p=0:nk=1 "$f" | tr '\n' ' '; echo
done

#!/bin/bash
# Iteration 2: flat canvas; title and end cards joined by a dip through the canvas colour (no dissolve over UI text).
set -euo pipefail
P=${P:-plates}; T=${T:-take1}; OUT=${OUT:-poc-cinematic-v2.mp4}
ZI=${ZI:-3.709}; ZO=${ZO:-7.413}; APP_LEN=${APP_LEN:-8.3}; TITLE=${TITLE:-1.4}; END_LEN=${END_LEN:-2.2}
WX=112; WY=36; WW=1696; WH=954; CV=0x0d1015
C3S=0.3; C3E=2.4; C4S=3.9; C4E=6.9; DIP=0.25
nice -n 15 ffmpeg -hide_banner -loglevel error -y \
  -loop 1 -framerate 30 -t "$APP_LEN" -i "$P/canvas.png" \
  -f concat -safe 0 -i "$T/frames.ffconcat" \
  -loop 1 -framerate 30 -t "$APP_LEN" -i "$P/dim.png" \
  -loop 1 -framerate 30 -t "$APP_LEN" -i "$P/cap-3.png" \
  -loop 1 -framerate 30 -t "$APP_LEN" -i "$P/cap-4.png" \
  -loop 1 -framerate 30 -t "$TITLE" -i "$P/title.png" \
  -loop 1 -framerate 30 -t "$END_LEN" -i "$P/end.png" \
  -filter_complex "
    [1:v]scale=${WW}:${WH}:flags=lanczos:in_range=pc:out_range=tv,fps=30,setsar=1,format=yuv420p[app];
    [0:v]format=rgb24[cv];
    [cv][app]overlay=${WX}:${WY}:shortest=1:format=auto[st];
    [2:v]format=rgba,fade=t=in:st=${ZI}:d=0.7:alpha=1,fade=t=out:st=${ZO}:d=0.6:alpha=1[dm];
    [st][dm]overlay=shortest=1[s2];
    [3:v]format=rgba,fade=t=in:st=${C3S}:d=0.16:alpha=1,fade=t=out:st=$(python3 -c "print($C3E-0.12)"):d=0.12:alpha=1[c3];
    [s2][c3]overlay=shortest=1:enable='between(t,${C3S},${C3E})'[s3];
    [4:v]format=rgba,fade=t=in:st=${C4S}:d=0.16:alpha=1,fade=t=out:st=$(python3 -c "print($C4E-0.12)"):d=0.12:alpha=1[c4];
    [s3][c4]overlay=shortest=1:enable='between(t,${C4S},${C4E})',trim=duration=${APP_LEN},setpts=PTS-STARTPTS,
      fade=t=in:st=0:d=${DIP}:color=${CV},fade=t=out:st=$(python3 -c "print($APP_LEN-$DIP)"):d=${DIP}:color=${CV},format=yuv420p[main];
    [5:v]fps=30,fade=t=out:st=$(python3 -c "print($TITLE-$DIP)"):d=${DIP}:color=${CV},format=yuv420p[ti];
    [6:v]fps=30,fade=t=in:st=0:d=${DIP}:color=${CV},format=yuv420p[en];
    [ti][main][en]concat=n=3:v=1:a=0[v]
  " -map "[v]" -c:v libx264 -preset slow -crf 18 -g 60 -pix_fmt yuv420p -movflags +faststart -an "$OUT"
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate,pix_fmt:format=duration,size -of compact=p=0:nk=1 "$OUT"

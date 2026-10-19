Cinematic PoC for the launch film (docs/design/launch/video-cinematic-brief.md, section 7).
record.mjs    records ~9 s of the real app (fixtures build on :3102) at 2x with the cursor and the 1.6x camera; writes frames/ + frames.ffconcat + marks.json
plates.mjs    renders the film plates from HTML with the self-hosted Plex files: canvas.png (window frame + shadow), dim.png, cap-N.png, title.png, end.png, end-claims.png (RELEASE_CLAIMS=1 only)
spec.json     the composition numbers (one source for plates.mjs and the brief)
composite.sh  ffmpeg: title card -> framed window + dim + caption band -> end card, dip-through-canvas joins; prints ffprobe
out/          (gitignored) the clip, check frames, crops and plates of the last run
Run order, under the heavy mutex while a browser or server runs: start the fixtures build on 3102, node record.mjs, node plates.mjs, ./composite.sh.

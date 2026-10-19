# Embedding the demo video (landing page and README)

The recorder and encoder (`scripts/record-demo.mjs`, `scripts/encode-demo.sh`) write the files below into a take directory under
the gitignored `video-out/`. Nothing is committed; the files are copied to `site/assets/` (page) and attached to the release (full
cut) by hand at release time. The landing page is static HTML with no JavaScript (D2, D12), so the reduced-motion variant is two
`<video>` elements and a media query, not a script. Sizes and budgets are enforced by `scripts/encode-demo.sh`.

| File | What | Budget |
| --- | --- | --- |
| `teaser-1080p-av1.mp4` | AV1 teaser (only written when it is smaller than the WebM) | 1 MB |
| `teaser-1080p.webm` | VP9 teaser | 1.5 MB |
| `teaser-1080p.mp4` | H.264 teaser (fallback, faststart) | 2.5 MB |
| `teaser-960.gif` | the README image (GitHub autoplays GIFs, not video) | 4 MB |
| `poster.jpg` | poster for both elements (the red-line beat) | 400 KB |
| `demo-1080p.mp4`, `demo-1080p.webm`, `demo-1080p-av1.mp4` | the full cut | 12 / 8 / 5 MB |
| `demo.vtt`, `demo.srt` | captions as text, from the real cue times | |
| `transcript.md` | the text alternative (WCAG 1.2.1, video-only prerecorded) | |

## Hero teaser (autoplay, muted, loops)

```html
<video class="hero-motion" autoplay muted loop playsinline preload="metadata" poster="assets/poster.jpg" width="1920" height="1080">
  <source src="assets/teaser-1080p-av1.mp4" type='video/mp4; codecs="av01.0.08M.08"'>
  <source src="assets/teaser-1080p.webm" type="video/webm">
  <source src="assets/teaser-1080p.mp4" type="video/mp4">
</video>
<!-- the same poster, shown instead when the visitor asks for less motion; no script involved -->
<video class="hero-still" controls muted playsinline preload="none" poster="assets/poster.jpg" width="1920" height="1080">
  <source src="assets/teaser-1080p.webm" type="video/webm">
  <source src="assets/teaser-1080p.mp4" type="video/mp4">
</video>
<style>
  .hero-still { display: none; }
  @media (prefers-reduced-motion: reduce) { .hero-motion { display: none; } .hero-still { display: block; } }
</style>
```

`muted` is what lets it autoplay; `playsinline` keeps iOS inline; source order is most efficient first; the poster is the likely
Largest Contentful Paint element. The teaser's ends are both the flat film canvas colour (`#0d1015`, `film.canvas` in `scripts/video-spec.json`), so the loop reads as a blink.

## Full cut (controls, captions, transcript)

```html
<video controls preload="none" poster="assets/poster.jpg" width="1920" height="1080">
  <source src="assets/demo-1080p-av1.mp4" type='video/mp4; codecs="av01.0.08M.08"'>
  <source src="assets/demo-1080p.webm" type="video/webm">
  <source src="assets/demo-1080p.mp4" type="video/mp4">
  <track kind="captions" src="assets/demo.vtt" srclang="en" label="English" default>
</video>
<p><a href="assets/transcript.md">Read the transcript</a> (the video has no sound; its captions are also burned in).</p>
```

The captions are burned in (the README and social copies have no track support) and shipped as `.vtt`/`.srt` for the page.

## README

GitHub renders images, not video, from the repository: embed `teaser-960.gif` (under 4 MB) with the poster's description as `alt`,
linking to the full cut attached to the release (after G2):

```markdown
[![A failed run opened in Agent BlackBox: one sentence says where it broke, a red line marks the first error.](docs/assets/teaser-960.gif)](https://github.com/<owner>/agent-black-box/releases)
```

## Film v3 (framed window on a canvas): what changes for the site

The cut is now a framed window on a flat dark canvas, with the captions in a band under the window (not on the app), a title card
and an end card (main and social) and 250 ms dips through the canvas colour. Two consequences for `site/` (not edited here):

1. **One CSS rule: the hero box takes the film canvas colour.** The teaser has no cards; its seam is a 250 ms dip to `#0d1015` at both
   ends. If the `<video>` sits on the page background, the dip shows as a dark flash. Wrap it in a box with the film canvas colour in
   both colour schemes:

   ```css
   .hero-film { background: #0d1015; } /* film.canvas, scripts/video-spec.json: the loop's dip colour */
   ```

   (`check-site.sh` could assert this value equals `video-spec.json` `color.canvas`; not wired.)
2. **Posters.** `poster.jpg` is now a frame of the composite (window, red line, caption band), 1920x1080.

The `release-claims/` folder inside a take's output holds the variant whose end card says "Open source. Self-hosted. Apache-2.0." with
the repository URL; it is built only with `RELEASE_CLAIMS=1` and carries a `README.txt`: DO NOT PUBLISH BEFORE G2.

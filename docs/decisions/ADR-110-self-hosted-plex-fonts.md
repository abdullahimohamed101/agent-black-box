# ADR-110: Self-Hosted IBM Plex Through next/font/local

Status: Accepted (Phase 21).
Date: 2026-10-12
Phase: 21

## Context

The approved UI direction (`docs/design/ui-direction.md`) replaces the system font with the IBM Plex superfamily: Sans for body, Sans
Condensed for headings and labels, Mono for every number, id and event type. An observability tool should not make a third-party request
for a typeface (it would tell a font CDN which workspace is being viewed, and it would break in air-gapped installs). The CSP is
`default-src 'self'`, which already allows same-origin fonts.

## Decision

Seven static Latin-1 woff2 files (Sans 400/500/600, Sans Condensed 500/600, Mono 400/500, about 145 KB) taken from the official
`IBM/plex` repository (`packages/*/fonts/split/woff2`) are committed under `apps/web/src/fonts/` with the SIL OFL 1.1 text (`OFL.txt`).
`apps/web/src/app/layout.tsx` loads them with `next/font/local` (part of Next, so no new dependency): Next emits the `@font-face`,
fallback-metric adjustments and preload links, and serves the files from `/_next/static/media` on our own origin. Next names each `@font-face` family after the declaring constant, so the families are `IBMPlexSans`, `IBMPlexSansCondensed` and `IBMPlexMono`. Three CSS variables
(`--font-plex-sans`, `--font-plex-cond`, `--font-plex-mono`) feed the `--sans`, `--cond`, `--mono` tokens in `globals.css`, each with a
system fallback stack. `display: "swap"`. Files are vendored, never fetched at build or run time.

## Alternatives considered

- Google Fonts: a third-party request from the product; rejected.
- Hand-written `@font-face` plus `public/fonts` and preload links: more code, no generated fallback metrics, and the Dockerfile would need to copy `public/`.
- Full (all-script) Plex files: about 4x the bytes for scripts the UI never renders; the Latin-1 subsets cover the product's text.

## Consequences

- Glyphs outside Latin-1 (some arrows, status glyphs) fall back to the system font; they are decorative or paired with words.
- A wrong path fails `next build`; a missing face is a silent fallback, so the design spec asserts `document.fonts.check` for the three faces.
- The OFL requires the license to ship with the font files; `OFL.txt` sits beside them.
- `next/font` preloads every file of a declared family, so all seven are preloaded (the plan's "three files only" is not expressible per file).

## Amendment (design pass 2 step 4, ADR-166)

The product is Plex Sans and Plex Mono only. Sans Condensed and Serif are deprecated: still declared in `layout.tsx` and loaded on demand, but `preload: false`, so they are no longer preloaded (Sans and Mono are). `next/font` still cannot preload per file, so Sans Medium stays preloaded until step 5 deletes the 500 weight, the two deprecated faces and their files.

## Amendment (design pass 2 step 5b, ADR-166)

Step 5b deleted the deprecated faces. Four files are vendored and preloaded: Sans Regular (400) and SemiBold (600), Mono Regular (400) and the Mono Medium file registered as weight 600 (there is no Mono SemiBold file here, and a mono word at 600 is then drawn from real Medium glyphs instead of a synthetic bold). Sans Condensed, Serif and Sans Medium, `--cond`, `--serif` and the 500 weight are gone; the three static families `IBMPlexSansCondensed`, `IBMPlexSerif` and the Medium file of Sans no longer exist. Payload: about 191 KB, of which 145 KB preloaded after step 4, now about 100 KB, all preloaded. `e2e/design.spec.ts` asserts `document.fonts.check` for Sans 400, Sans 600 and Mono 400.

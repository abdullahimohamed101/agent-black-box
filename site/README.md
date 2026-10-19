# Landing page (Phase 20, step 13)

Status: the plain-HTML baseline. The owner has since allowed React and custom JS/CSS, so a richer v2 is expected to replace the
files marked "baseline" below. The checks, the Pages workflow and the CI job are stack-agnostic and stay.

## Build and check

```bash
node site/build.mjs                       # writes site/index.html, site/styles.css, site/fonts/*
node site/build.mjs --out /tmp/site       # a complete scratch or deploy build (what pages.yml publishes: site-dist/)
scripts/check-site.sh                     # build freshness, static checks, browser checks on site/
scripts/check-site.sh DIR [--static]      # the same checks on any built directory
SITE_SHOTS_OUT=/tmp/shots scripts/check-site.sh   # also save full-page screenshots (not committed)
```

## Files

| File | Role |
| --- | --- |
| `scripts/check-site.sh`, `scripts/check_site.py`, `scripts/check_site_browser.mjs` | stack-agnostic checks of a built directory |
| `.github/workflows/pages.yml`, job `site-check` in `.github/workflows/ci.yml` | stack-agnostic (the Build step is the one line to change) |
| `repo-url.mjs` | the single `REPO_URL` (override `ABB_REPO_URL`); keep |
| `build.mjs`, `src/`, `index.html`, `styles.css`, `screenshots.json`, `assets/pulse-glyph.svg`, `fonts/` | baseline |

`styles.css` begins with the block produced from `apps/web/design-tokens/tokens.json` by the app's own generator
(`apps/web/scripts/tokens-lib.mjs`), so there is one source of truth. Fonts are the app's IBM Plex woff2 files plus `OFL.txt`
(ADR-175); the page uses three of the four.

## Screenshots (optional, step 12 not built yet)

`build.mjs` reads `screenshots.json` and includes a figure only for files that exist in `docs/screenshots/release/` (or `--shots DIR`),
copying them to `assets/shots/`. Until step 12 delivers images the section is absent, so no link is ever broken. The file names, alt
texts and captions in `screenshots.json` are guesses: check them against the real images. Existing `docs/screenshots/pass-2` images
may be used as local stand-ins with `--shots`, never committed into the markup.

## Claims

Copy uses only claims the plan allows. The page says Apache-2.0 (a license fact), "install from source", "pre-release", and that the
page itself loads nothing from other sites (verified by `check-site.sh`). It does not say "open source", "self-hosted", "no API key
needed" or "deterministic demo". `[CONFIRM]` comments mark copy to re-check; `check-site.sh --release` fails while any remain.
Repository links point at `REPO_URL` and 404 until the repository is public.

# Landing site (Phase 20, step 13)

A Next 16 static export, a pnpm workspace package (`@abb/site`) that reuses the exact `next`, `react`, `react-dom`, Playwright, axe and
vitest versions of `apps/web` (the lockfile gained one importer and no package). Design: `docs/design/launch/landing-site-brief.md`;
copy: `docs/design/launch/positioning-brief.md`.

## Build and check

```bash
pnpm --filter @abb/site build        # site/out, for https://<owner>.github.io/agent-black-box/ (what Pages publishes)
pnpm --filter @abb/site dev          # http://localhost:3100/agent-black-box/
pnpm --filter @abb/site test         # vitest: the trace island and its model
make site-check                      # = scripts/check-site.sh: builds three exports and runs every check
SITE_PERF_STRICT=1 scripts/check-site.sh --only perf   # timing limits are advisory unless SITE_PERF_STRICT=1
scripts/check-site.sh --only perf    # one check (build fixture commands images links size claims nojs copy gallery
                                     #   network a11y motion perf shots planted)
SHOTS_DIR=docs/screenshots/site scripts/check-site.sh --only shots   # the 375/1440 light/dark screenshots (AC11; full-page PNGs are about 1 MB, so they live apart from the release set capped at 400 KB per image)
scripts/check-site.sh site/out --static --release                       # fails while any [CONFIRM ...] marker remains
```

Environment: `ABB_SITE_BASE_PATH` (default `/agent-black-box`; empty builds for a domain root), `ABB_SITE_OUT` (a second output
directory), `ABB_SITE_URL` (absolute origin for the Open Graph image), `ABB_RELEASE_CLAIMS=1` (below).

## Publication claims (gate G2)

`ABB_RELEASE_CLAIMS` is OFF by default. While it is off the export contains no "open source", no "self-hosted" as a product claim,
no "Source on GitHub", no link into the repository (it 404s until the repository is public) and no video caption saying so;
`check-site.sh claims` fails if one appears. "Apache-2.0" is stated as a licence fact (the `LICENSE` file exists; owner decision, review P3-2), flag on or off. Pages sets the flag for its deploy, so
running `.github/workflows/pages.yml` (inert: `workflow_dispatch` only) is the act of publication.

## `[CONFIRM ...]` markers

Copy that waits for a fact carries `data-confirm="[CONFIRM ...]"` (`src/lib/confirm.ts`) and the gallery marks every pass-2
stand-in image. `check-site.sh --release` lists them and fails while any remain; remove a marker when its fact is verified.

## Layout

| Path | Role |
| --- | --- |
| `src/app/` | layout (fonts, metadata, theme-color) and the one page |
| `src/components/` | one component per section; islands: `FaultTrace`, `ScreenGallery`, `CopyButton`, `TeaserVideo` |
| `src/lib/` | the trace model, code colouring, gallery data, `confirm` |
| `src/content/` | shared copy, the five quickstart commands, the generated image manifest |
| `src/styles/` | `tokens.css` (generated), `site.css`, `hero.css`, `trace.css`, `sections.css`; no colour literal |
| `fixtures/` | the hero's 15 events, validated against `packages/event-schema` by `tests/validate_fixture.py` |
| `screenshots.json` | gallery words, crops and callouts in source pixels; `scripts/site-images.mjs` encodes the images |
| `public/` | brand assets (from `scripts/brand.sh`), gallery WebP/PNG, `site.webmanifest`; `public/assets/video/` for the G1 teaser |
| `scripts/tokens.mjs` | writes `tokens.css` and the three Plex files from `apps/web` (`--check` for staleness) |
| `repo-url.mjs`, `base-path.mjs` | the repository address and the deploy path, each in one place |

The checks themselves are stack-agnostic and live in `scripts/` (`check-site.sh`, `check_site*.py`, `site-checks/*.mjs`).

## Not here yet

- Release screenshots (plan step 12): the gallery falls back to `docs/screenshots/pass-2` and marks each panel; drop
  `docs/screenshots/release/<release>-<light|dark>.png` (names in `screenshots.json`) and run `node scripts/site-images.mjs`.
- The teaser video (gate G1): put `poster.webp`, `teaser-1080p.mp4` (and optionally `-av1.mp4`, `.webm`, `teaser.vtt`,
  `demo-1080p.mp4`) in `public/assets/video/`; the Watch section appears when they exist.
- `docs/quickstart.md` (step 14): `check-site.sh commands` then compares the page's commands with it.

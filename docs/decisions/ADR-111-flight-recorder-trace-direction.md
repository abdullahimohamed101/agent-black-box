# ADR-111: "Flight recorder trace" Visual Direction

Status: Accepted (Phase 22, step 1).
Date: 2026-10-12
Phase: 22

## Context

Phase 21 gave the product a frame and IBM Plex but kept a status system of outlined, upper-case pills in five colours, a red that
also meant "delete" and "removed line", and a placeholder brand glyph. The Phase 22 plan (D5, D10) redesigns the run page around one
idea: colour only ever means a problem. This ADR records the visual language that every later step builds on. It extends
`docs/design/ui-direction.md` and ADR-110; it changes no data contract.

## Decision

- **Direction.** A flight-recorder trace: a fault line marks the first error on the timeline and waterfall, rows above it are quiet,
  rows below carry a faint tint. The line itself ships in later steps; this step ships the tokens it needs.
- **Palette** (light default, dark follows the system; both themes pass 4.5:1 for every text pair, asserted by
  `apps/web/tests/tokens-contrast.test.ts` and `tokens.test.ts`). Hex values are exactly the plan's:
  bg `#eef1f4`/`#12161b`, surface `#ffffff`/`#1a2027`, ink `#171b21`/`#e8ecf1`, muted `#566272`/`#a3adb9`, interaction accent
  `#2a5f8f`/`#7db3e6`, fail `#b3261e`/`#ff8a80`, retry and wait `#8a5a00`/`#ffcb6b`. Tints (`--fail-bg`, `--wait-bg`, `--accent-bg`) are
  derived. One derived value was adjusted: the selection colour `--sel` light is `#e6eef6` (was `#dceef5`) because the quiet green on
  it measured 4.42:1. No plan hex needed changing.
- **Old names stay as aliases** (`--fg`=`--ink`, `--bad`=`--fail`, `--warn`=`--wait`, `--info`=`--accent`, `-bg` pairs), so other stylesheets
  and tests keep working; new code uses the semantic names.
- **Red is for failure only.** `--fail`/`--bad` are read only by failure selectors. `apps/web/tests/tokens-use.test.ts` greps the
  stylesheets and fails if any other rule reads them. Moved off red in this step: destructive buttons (`button.danger`) and removed diff
  lines (`.diff-del`) now use amber, because a delete is a caution, not a failure. Green stays but is quiet (text colour, no fill).
  Amber means retry, waiting or caution. Steel blue is interaction (links, focus, selection) and a live run, never a verdict.
- **Status grammar.** Status is a glyph plus a word with no outline and no upper-case tracking. Only a failure-tone status (FAILED,
  TIMED OUT, BLOCKED) is a filled chip (surface-coloured text on `--fail`). `.badge`, `.tag` and the markup `StatusBadge` produces are
  unchanged (`data-tone`, `data-status`), so every consumer inherits the look.
- **Type scale.** Plex Sans body 13 px, Condensed for headings and column heads, Mono for numbers. New tokens: `--fs-headline` (1.5rem, the
  failure line), `--fs-title`, `--fs-body`, `--fs-small`. Decorative upper-case micro-labels (stat labels, drawer and context headings,
  table captions) became sentence case at 0.75rem; table column heads keep their small caps.
- **Plex Serif.** IBM Plex Serif Regular (400) and Medium (500), Latin-1 woff2 from `IBM/plex` `packages/plex-serif/fonts/split/woff2`, the
  same source as ADR-110, vendored in `apps/web/src/fonts/` and loaded with `next/font/local` as `--font-plex-serif`, exposed to CSS as
  `--serif`. The licence is the same SIL OFL 1.1 as the other Plex files and is already shipped beside them (`OFL.txt`, "Copyright
  IBM Corp." with Reserved Font Name "Plex"). Used only for the failure line and empty-state headlines, nowhere else.
- **Brand mark.** Three options are drawn (`apps/web/public/brand/option-{a,b,c}.svg`, rationale in
  `docs/design/brand-mark-options.md`). Option A (a trace line with one cut) is wired into `Brand` and `src/app/icon.svg`
  provisionally, pending the owner's choice.
- **Motion language** (applied in step 6): 120 to 200 ms, `cubic-bezier(0.2, 0, 0, 1)` (already `--ease-out`), `transform` and `opacity`
  only, no springs or bounce, no stagger; under `prefers-reduced-motion` the page is static and complete (the duration tokens already
  collapse to 0).

## Alternatives considered

- Keeping five status colours with outlined pills: the signal (a failure) is lost among decoration; rejected by D5.
- A dark-only "terminal" look: the audience reads this in daylight and in dark mode alike; the system setting decides.
- A serif for all headings: it would make the product read as editorial; reserved for the one sentence that matters.
- Red for destructive actions: conflates "you are about to delete" with "your agent failed"; rejected.

## Consequences

- Any new use of `--fail`/`--bad` needs an allowlist entry in `tokens-use.test.ts`, which makes the red rule a review point.
- The filled chip is the only coloured fill in status, so a failed row stands out in a list of quiet ones.
- Two more font files (about 47 KB) are preloaded. `next/font` preloads every file of a declared family, as in ADR-110.
- Glyph characters in `StatusBadge` still come from system fonts when outside Latin-1; each is paired with a word.

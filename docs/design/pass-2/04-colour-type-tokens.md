# Design pass 2, step 4: colour, type and tokens

Status: implemented 2026-10-16 (step 4 of `docs/design/design-pass-2-pipeline.md`; Prompt 5 sections A and B). Decisions: ADR-166
"Token decisions". Every table between `<!-- tokens:report:... -->` markers is GENERATED from
`apps/web/design-tokens/tokens.json` by `pnpm --filter @abb/web tokens:report`; the contrast numbers are computed, never
estimated, and `tokens:check` and `tests/tokens-structure.test.ts` fail when a table is stale. Prose outside the markers is
hand-written.

## 1. What exists now

| File | Role |
| --- | --- |
| `apps/web/design-tokens/tokens.json` | the one source: primitives, semantic tokens (light and dark), type, space, layout, radius, motion, the alias map, the contrast pair list |
| `apps/web/scripts/tokens-lib.mjs` | OKLCH to sRGB conversion, contrast maths, CSS and report renderers (stdlib node, no dependency) |
| `apps/web/scripts/tokens-build.mjs` | writes (or with `--check` verifies) `src/app/tokens.generated.css` and `src/app/tokens.aliases.css` |
| `apps/web/scripts/tokens-report.mjs` | rewrites (or checks) the tables in this file and exits 1 when a pair is below target |
| `apps/web/src/app/tokens.generated.css` | `--color-*`, `--type-*`, `--weight-*`, `--font-*`, `--measure`, `--space-*`, `--layout-*`, `--radius-*`, `--motion-*` (generated: do not edit) |
| `apps/web/src/app/tokens.aliases.css` | the old names (`--bg`, `--ink`, ...) as aliases of the new tokens (generated: do not edit) |
| `apps/web/tests/tokens-structure.test.ts`, `tokens-contrast-computed.test.ts`, `tokens-colour-rules.test.ts` | structure and freshness, the computed contrast gate, the raw-colour and red-line ratchets |
| `apps/web/tests/tokenCss.ts` | reads the colour properties the way the browser loads them, for the older token tests |

Commands (from `apps/web`): `pnpm tokens:build`, `pnpm tokens:check` (also run by `scripts/quality.sh`), `pnpm tokens:report`.
The theme mechanism is unchanged: light by default, dark through `prefers-color-scheme` (the app has no theme switch, and the
direction bans one).

## 2. Colour

### 2.1 Primitives

Authored in OKLCH where the direction gave an OKLCH value and kept as the ADR-111 hex where the direction said "kept". The hex is
what is rendered and what every contrast number below is computed on.

<!-- tokens:report:primitives:start -->
| Token | OKLCH | Hex (rendered) | Source |
| --- | --- | --- | --- |
| `color.primitive.neutral.0` | `oklch(100.0% 0.000 0)` | `#ffffff` | literal hex kept (ADR-111 / direction) |
| `color.primitive.neutral.50` | `oklch(97.8% 0.003 250)` | `#f6f8fa` | authored in OKLCH |
| `color.primitive.neutral.100` | `oklch(92% 0.006 250)` | `#e2e5e8` | authored in OKLCH |
| `color.primitive.neutral.150` | `oklch(90% 0.006 250)` | `#dbdee2` | authored in OKLCH |
| `color.primitive.neutral.200` | `oklch(82% 0.010 250)` | `#bfc5ca` | authored in OKLCH |
| `color.primitive.neutral.300` | `oklch(72% 0.012 250)` | `#9fa5ac` | authored in OKLCH |
| `color.primitive.neutral.400` | `oklch(62% 0.015 255)` | `#80878f` | authored in OKLCH |
| `color.primitive.neutral.450` | `oklch(57% 0.015 255)` | `#727880` | authored in OKLCH |
| `color.primitive.neutral.500` | `oklch(50% 0.020 255)` | `#5c646f` | authored in OKLCH |
| `color.primitive.neutral.600` | `oklch(42% 0.012 255)` | `#494d54` | authored in OKLCH |
| `color.primitive.neutral.700` | `oklch(32% 0.012 255)` | `#2f3339` | authored in OKLCH |
| `color.primitive.neutral.800` | `oklch(25% 0.015 255)` | `#1d2229` | authored in OKLCH |
| `color.primitive.neutral.850` | `oklch(24% 0.012 255)` | `#1b2025` | authored in OKLCH |
| `color.primitive.neutral.900` | `oklch(19% 0.012 255)` | `#101419` | authored in OKLCH |
| `color.primitive.accent.50` | `oklch(93% 0.030 250)` | `#d9eafc` | authored in OKLCH |
| `color.primitive.accent.100` | `oklch(94.5% 0.014 248)` | `#e6eef6` | literal hex kept (ADR-111 / direction) |
| `color.primitive.accent.200` | `oklch(86% 0.060 250)` | `#b4d5f8` | authored in OKLCH |
| `color.primitive.accent.300` | `oklch(74.9% 0.093 248)` | `#7db3e6` | literal hex kept (ADR-111 / direction) |
| `color.primitive.accent.600` | `oklch(47.3% 0.096 249)` | `#2a5f8f` | literal hex kept (ADR-111 / direction) |
| `color.primitive.accent.700` | `oklch(41% 0.100 250)` | `#164c7e` | authored in OKLCH |
| `color.primitive.accent.800` | `oklch(35% 0.090 250)` | `#0b3c67` | authored in OKLCH |
| `color.primitive.accent.900` | `oklch(30.2% 0.037 248)` | `#1f3040` | literal hex kept (ADR-111 / direction) |
| `color.primitive.red.300` | `oklch(75.9% 0.144 26)` | `#ff8a80` | literal hex kept (ADR-111 / direction) |
| `color.primitive.red.600` | `oklch(50.1% 0.178 29)` | `#b3261e` | literal hex kept (ADR-111 / direction) |
| `color.primitive.amber.300` | `oklch(86.9% 0.128 81)` | `#ffcb6b` | literal hex kept (ADR-111 / direction) |
| `color.primitive.amber.700` | `oklch(50.8% 0.108 73)` | `#8a5a00` | literal hex kept (ADR-111 / direction) |
| `color.primitive.orange.400` | `oklch(72.4% 0.187 49)` | `#ff7a1a` | literal hex kept (ADR-111 / direction) |
| `color.primitive.orange.600` | `oklch(63.8% 0.167 49)` | `#d9650f` | literal hex kept (ADR-111 / direction) |
<!-- tokens:report:primitives:end -->

### 2.2 Semantic tokens

<!-- tokens:report:semantic:start -->
| Token | CSS variable | Light | Dark | Use |
| --- | --- | --- | --- | --- |
| `color.bg.canvas` | `--color-bg-canvas` | neutral.50 `#f6f8fa` | neutral.900 `#101419` | the page; tables and panels sit directly on it |
| `color.bg.raised` | `--color-bg-raised` | neutral.0 `#ffffff` | neutral.850 `#1b2025` | menus, the phone inspector overlay, the search listbox |
| `color.bg.wash` | `--color-bg-wash` | accent.100 `#e6eef6` | accent.900 `#1f3040` | selected row, pressed toggle |
| `color.text.primary` | `--color-text-primary` | neutral.800 `#1d2229` | neutral.100 `#e2e5e8` | text, status glyphs, severity (by weight) |
| `color.text.secondary` | `--color-text-secondary` | neutral.500 `#5c646f` | neutral.300 `#9fa5ac` | secondary text, column heads, success and cancelled words |
| `color.text.disabled` | `--color-text-disabled` | neutral.300 `#9fa5ac` | neutral.600 `#494d54` | disabled controls (exempt from contrast) |
| `color.text.inverse` | `--color-text-inverse` | neutral.50 `#f6f8fa` | neutral.900 `#101419` | text on the filled accent button |
| `color.text.link` | `--color-text-link` | accent.600 `#2a5f8f` | accent.300 `#7db3e6` | links |
| `color.border.subtle` | `--color-border-subtle` | neutral.150 `#dbdee2` | neutral.700 `#2f3339` | every divider and row rule |
| `color.border.default` | `--color-border-default` | neutral.150 `#dbdee2` | neutral.700 `#2f3339` | alias of subtle (the schema asks for default / strong / subtle) |
| `color.border.strong` | `--color-border-strong` | neutral.200 `#bfc5ca` | neutral.600 `#494d54` | table head rule, section rule |
| `color.border.control` | `--color-border-control` | neutral.400 `#80878f` | neutral.450 `#727880` | field and button boundaries (3:1) |
| `color.interactive.default` | `--color-interactive-default` | accent.600 `#2a5f8f` | accent.300 `#7db3e6` | links, focus ring, selection edge, the one filled button, the running dot |
| `color.interactive.hover` | `--color-interactive-hover` | accent.700 `#164c7e` | accent.200 `#b4d5f8` | filled button hover |
| `color.interactive.active` | `--color-interactive-active` | accent.800 `#0b3c67` | accent.50 `#d9eafc` | filled button pressed |
| `color.interactive.disabled` | `--color-interactive-disabled` | neutral.200 `#bfc5ca` | neutral.600 `#494d54` | disabled control fill or border (exempt) |
| `color.status.fault` | `--color-status-fault` | red.600 `#b3261e` | red.300 `#ff8a80` | the fault line, the miniature dash, the words Failed / Timed out / Blocked. Nothing else. |
| `color.status.caution` | `--color-status-caution` | amber.700 `#8a5a00` | amber.300 `#ffcb6b` | the words Waiting, Retry, Expired, Near limit; a destructive button text. Text only, no fill |
| `color.status.success` | `--color-status-success` | color.text.secondary `#5c646f` | same | a check glyph in ink-2 (no green) |
| `color.status.running` | `--color-status-running` | color.interactive.default `#2a5f8f` | same | the running dot |
| `color.brand.mark` | `--color-brand-mark` | orange.600 `#d9650f` | orange.400 `#ff7a1a` | the brand glyph cut and the recording dot; never an interaction colour |
| `color.data.llm` | `--color-data-llm` | `#72649b` | `#9c8dca` | waterfall lane: model calls |
| `color.data.tool` | `--color-data-tool` | `#46729e` | `#6d9dce` | waterfall lane: tool calls |
| `color.data.shell` | `--color-data-shell` | `#846b34` | `#b89a5a` | waterfall lane: shell commands |
| `color.data.agent` | `--color-data-agent` | `#407754` | `#69a980` | waterfall lane: agents |
| `color.data.db` | `--color-data-db` | `#357a82` | `#56a7b0` | waterfall lane: database |
| `color.data.neutral` | `--color-data-neutral` | `#676f7a` | `#9099a5` | waterfall lane: custom and other |
| `color.data.agent-0` | `--color-data-agent-0` | `#2a5885` | `#85b6e9` | agent chip 0 in the waterfall and the timeline (text, 4.5:1; none is red-ish) |
| `color.data.agent-1` | `--color-data-agent-1` | `#2e5f3a` | `#87c293` | agent chip 1 in the waterfall and the timeline (text, 4.5:1; none is red-ish) |
| `color.data.agent-2` | `--color-data-agent-2` | `#714d1d` | `#ddae77` | agent chip 2 in the waterfall and the timeline (text, 4.5:1; none is red-ish) |
| `color.data.agent-3` | `--color-data-agent-3` | `#594a82` | `#b5a6e5` | agent chip 3 in the waterfall and the timeline (text, 4.5:1; none is red-ish) |
| `color.data.agent-4` | `--color-data-agent-4` | `#115d65` | `#70c0c9` | agent chip 4 in the waterfall and the timeline (text, 4.5:1; none is red-ish) |
| `color.data.agent-5` | `--color-data-agent-5` | `#575d24` | `#b5bf7b` | agent chip 5 in the waterfall and the timeline (text, 4.5:1; none is red-ish) |
| `color.data.agent-6` | `--color-data-agent-6` | `#4e5661` | `#a9b2be` | agent chip 6 in the waterfall and the timeline (text, 4.5:1; none is red-ish) |
| `color.data.agent-7` | `--color-data-agent-7` | `#71462e` | `#e2a989` | agent chip 7 in the waterfall and the timeline (text, 4.5:1; none is red-ish) |
| `color.syntax.keyword` | `--color-syntax-keyword` | `#5b21b6` | `#d2a8ff` | code keyword |
| `color.syntax.string` | `--color-syntax-string` | `#116329` | `#7ee787` | code string |
| `color.syntax.number` | `--color-syntax-number` | `#8a4600` | `#ffa657` | code number |
| `color.syntax.function` | `--color-syntax-function` | `#0a4fa3` | `#79c0ff` | code function name |
<!-- tokens:report:semantic:end -->

Status colours: the direction removes every fill, so there is no "background variant" of a status colour (the prompt's
`color.status.*.bg`). `success` is `text.secondary` (a check glyph, not green), `running` is the accent dot, `caution` and
`fault` are text colours.

### 2.3 Where the converted hex differs from the direction's approximate hex

The direction wrote "oklch(...) ≈ #hex" and allowed step 4 to nudge lightness, never hue. The OKLCH values are the spec, so the
accurate conversion is used (the approximate hexes were off by up to 2 points of lightness). No hue was changed and no lightness
was nudged: every OKLCH role passes its target as authored.

| Role | Direction's approximate hex | Converted from the direction's OKLCH | Why it matters |
| --- | --- | --- | --- |
| canvas light | `#f7f8fa` | `#f6f8fa` | none |
| canvas dark | `#15191e` (L 21 %) | `#101419` (L 19 %) | darker page; raised dark `#1b2025` (L 24 %) keeps the elevation step |
| raised dark | `#1f242b` | `#1b2025` | as above |
| hairline light / dark | `#dde1e6` / `#333a43` | `#dbdee2` / `#2f3339` | none |
| hairline-strong light / dark | `#c4cad2` / `#4a525c` | `#bfc5ca` / `#494d54` | 1.64:1 and 2.18:1: decorative only (see `border.control`) |
| ink light / dark | `#22272e` / `#e4e8ec` | `#1d2229` / `#e2e5e8` | text.primary 15.0:1 and 14.6:1 on canvas (AAA with room) |
| ink-2 light / dark | `#626c78` / `#a0a8b2` | `#5c646f` / `#9fa5ac` | the converted ink-2 is darker in light, so it passes AA with margin on every ground (5.6:1 on canvas, 5.1:1 on the wash, the tightest) |

One pair needed a value the direction did not list: `color.border.control` (field and button boundaries). The direction's
hairline-strong is 1.6:1 on the canvas, below the 3:1 WCAG 1.4.11 asks of a control boundary, so `border.control` is added
(`neutral.400` light, `neutral.450` dark: 3.41:1 and 4.15:1 on canvas, 3.63:1 and 3.68:1 on raised). `border.strong` stays the
direction's value for rules. Every other pair passes with the direction's values.

### 2.4 Contrast compliance (computed)

Method: WCAG 2.x relative luminance on the rendered sRGB hex, ratio `(L1 + 0.05) / (L2 + 0.05)`, in both themes. Targets (the
test fails below them): 7:1 for body text on canvas, raised and wash (`text.primary`, AAA); 4.5:1 for every other text pair (AA);
3:1 for graphics and control boundaries (WCAG 1.4.11); a stated floor below 3:1 only for decorative hairlines that are never the
only cue. "Meets AAA" is reported for information on every row. Pairs marked legacy exist only until step 5 removes their tokens.
The eight most common pairs in the app are the first eight text rows (ink, ink-2, link and fault on canvas and raised) plus the
selected row and the filled button.

<!-- tokens:report:contrast:start -->
| Foreground on background | Light | Dark | Target (the test fails below it) | Meets AA | Meets AAA | Used for |
| --- | --- | --- | --- | --- | --- | --- |
| `text.primary` on `bg.canvas` | 15.02 | 14.62 | 7:1 (AAA) | pass | yes | body text on the page |
| `text.primary` on `bg.raised` | 15.99 | 12.97 | 7:1 (AAA) | pass | yes | text in menus, the inspector overlay, the search listbox |
| `text.primary` on `bg.wash` | 13.65 | 10.68 | 7:1 (AAA) | pass | yes | text on the selected row |
| `text.secondary` on `bg.canvas` | 5.62 | 7.44 | 4.5:1 (AA) | pass | dark only | column heads, captions, ids, hints, success and cancelled words |
| `text.secondary` on `bg.raised` | 5.99 | 6.60 | 4.5:1 (AA) | pass | no | secondary text in menus |
| `text.secondary` on `bg.wash` | 5.11 | 5.44 | 4.5:1 (AA) | pass | no | secondary text on the selected row |
| `text.link` on `bg.canvas` | 6.30 | 8.33 | 4.5:1 (AA) | pass | dark only | links |
| `text.link` on `bg.wash` | 5.72 | 6.09 | 4.5:1 (AA) | pass | no | a link inside a selected row |
| `status.fault` on `bg.canvas` | 6.14 | 8.10 | 4.5:1 (AA) | pass | dark only | the words Failed, Timed out, Blocked; the fault line label |
| `status.fault` on `bg.raised` | 6.54 | 7.19 | 4.5:1 (AA) | pass | dark only | the failed word inside a menu or overlay |
| `status.fault` on `bg.wash` | 5.58 | 5.92 | 4.5:1 (AA) | pass | no | the failed word on a selected row |
| `status.caution` on `bg.canvas` | 5.57 | 12.33 | 4.5:1 (AA) | pass | dark only | Waiting, Retry, Expired, Near limit; a destructive button's text |
| `status.caution` on `bg.raised` | 5.93 | 10.94 | 4.5:1 (AA) | pass | dark only | caution words in menus |
| `status.caution` on `bg.wash` | 5.06 | 9.01 | 4.5:1 (AA) | pass | dark only | caution words on a selected row |
| `text.inverse` on `interactive.default` | 6.30 | 8.33 | 4.5:1 (AA) | pass | dark only | the one filled button |
| `text.inverse` on `interactive.hover` | 8.33 | 12.16 | 4.5:1 (AA) | pass | yes | the filled button on hover |
| `text.inverse` on `interactive.active` | 10.63 | 15.07 | 4.5:1 (AA) | pass | yes | the filled button pressed |
| `interactive.default` on `bg.canvas` | 6.30 | 8.33 | 3:1 (AA, graphics) | pass | yes | focus ring, the running dot, selection edge (graphics, 3:1) |
| `interactive.default` on `bg.raised` | 6.71 | 7.39 | 3:1 (AA, graphics) | pass | yes | focus ring inside a menu |
| `border.control` on `bg.canvas` | 3.41 | 4.15 | 3:1 (AA, graphics) | pass | no | form field and button boundaries (WCAG 1.4.11, 3:1) |
| `border.control` on `bg.raised` | 3.63 | 3.68 | 3:1 (AA, graphics) | pass | no | field boundaries inside menus and overlays |
| `border.strong` on `bg.canvas` | 1.64 | 2.18 | 1.3:1 (decorative floor, graphics) | n/a (decorative) | n/a | table head rule and section rules (decorative; never the only cue) |
| `border.subtle` on `bg.canvas` | 1.27 | 1.46 | 1.1:1 (decorative floor, graphics) | n/a (decorative) | n/a | row rules (decorative; never the only cue) |
| `brand.mark` on `bg.canvas` | 3.39 | 7.09 | 3:1 (AA, graphics) | pass | dark only | the brand cut and the recording dot (graphics, 3:1) |
| `data.llm` on `bg.canvas` | 4.92 | 6.23 | 3:1 (AA, graphics) | pass | yes | waterfall bar (graphics, 3:1) |
| `data.tool` on `bg.canvas` | 4.74 | 6.48 | 3:1 (AA, graphics) | pass | yes | waterfall bar (graphics, 3:1) |
| `data.shell` on `bg.canvas` | 4.77 | 6.87 | 3:1 (AA, graphics) | pass | yes | waterfall bar (graphics, 3:1) |
| `data.agent` on `bg.canvas` | 4.95 | 6.68 | 3:1 (AA, graphics) | pass | yes | waterfall bar (graphics, 3:1) |
| `data.db` on `bg.canvas` | 4.63 | 6.65 | 3:1 (AA, graphics) | pass | yes | waterfall bar (graphics, 3:1) |
| `data.neutral` on `bg.canvas` | 4.77 | 6.41 | 3:1 (AA, graphics) | pass | yes | waterfall bar (graphics, 3:1) |
| `data.agent-0` on `bg.canvas` | 6.96 | 8.68 | 4.5:1 (AA) | pass | dark only | agent chip text |
| `data.agent-1` on `bg.canvas` | 7.01 | 8.95 | 4.5:1 (AA) | pass | yes | agent chip text |
| `data.agent-2` on `bg.canvas` | 7.09 | 9.15 | 4.5:1 (AA) | pass | yes | agent chip text |
| `data.agent-3` on `bg.canvas` | 7.23 | 8.41 | 4.5:1 (AA) | pass | yes | agent chip text |
| `data.agent-4` on `bg.canvas` | 7.10 | 8.87 | 4.5:1 (AA) | pass | yes | agent chip text |
| `data.agent-5` on `bg.canvas` | 6.58 | 9.42 | 4.5:1 (AA) | pass | dark only | agent chip text |
| `data.agent-6` on `bg.canvas` | 6.98 | 8.62 | 4.5:1 (AA) | pass | dark only | agent chip text |
| `data.agent-7` on `bg.canvas` | 7.54 | 9.04 | 4.5:1 (AA) | pass | yes | agent chip text |
| `syntax.keyword` on `bg.canvas` | 8.44 | 9.49 | 4.5:1 (AA) | pass | yes | code highlighting |
| `syntax.keyword` on `bg.raised` | 8.98 | 8.43 | 4.5:1 (AA) | pass | yes | code highlighting in a raised surface |
| `syntax.string` on `bg.canvas` | 6.94 | 12.03 | 4.5:1 (AA) | pass | dark only | code highlighting |
| `syntax.string` on `bg.raised` | 7.39 | 10.68 | 4.5:1 (AA) | pass | yes | code highlighting in a raised surface |
| `syntax.number` on `bg.canvas` | 6.67 | 9.54 | 4.5:1 (AA) | pass | dark only | code highlighting |
| `syntax.number` on `bg.raised` | 7.10 | 8.47 | 4.5:1 (AA) | pass | yes | code highlighting in a raised surface |
| `syntax.function` on `bg.canvas` | 7.42 | 9.50 | 4.5:1 (AA) | pass | yes | code highlighting |
| `syntax.function` on `bg.raised` | 7.89 | 8.44 | 4.5:1 (AA) | pass | yes | code highlighting in a raised surface |
<!-- tokens:report:contrast:end -->

### 2.5 Dark mode: where it is not a simple inversion

| Token | Light to dark | Note |
| --- | --- | --- |
| `bg.canvas`, `bg.raised` | `neutral.50` to `neutral.900`; white to `neutral.850` | not mirrored: dark ink (L 25 %) is not the dark canvas (L 19 %). Raised stays the lighter surface in both themes (elevation reads the same way) |
| `text.secondary` | L 50 % to L 72 % | the dark value is lighter than a mirror would give so that it holds 4.5:1 on raised and wash (5.1:1 worst case, light wash) |
| `status.fault`, `status.caution` | `red.600` / `amber.700` to `red.300` / `amber.300` | completely different values: saturated dark text on a light page becomes pastel text on a dark page; no inversion of one gives the other |
| `interactive.*` | `accent.600` to `accent.300`; hover goes darker in light and lighter in dark | the steel blue is re-picked for the dark page (L 47 % to 75 %), as in ADR-111 |
| `bg.wash` | `#e6eef6` to `#1f3040` | a tint of the accent on each side, not an inversion |
| `border.control`, `border.subtle` | L 62 % / 90 % to L 57 % / 32 % | each theme is picked for its own contrast, not mirrored |
| `brand.mark` | `#d9650f` to `#ff7a1a` | brighter on dark to stay 7:1; still never an interaction colour |
| `text.inverse` | `neutral.50` to `neutral.900` | the colour of the label on the filled button; it follows the canvas, so the accent fill carries it at 6.3:1 and 8.3:1 |

## 3. Type

### 3.1 Scale

Seven steps, two weights, no letter-spacing, no upper case. The line heights are fixed in px-equivalent rem so bands do not
jitter. `display` appears only on sign-in, no-access and the first-run headline, never inside the shell. `mono` and `mono-small`
are the same sizes as `body` and `small` in Plex Mono (`font-variant-numeric: tabular-nums` is already set on the body).

<!-- tokens:report:type:start -->
| Step | Size / line | rem | Weight | Tracking | Family | Use |
| --- | --- | --- | --- | --- | --- | --- |
| `display` | 32px / 40px | 2rem / 2.5rem | 600 | 0 | Plex Sans | sign-in, no-access, first-run headline only (never inside the shell) |
| `h1` | 24px / 30px | 1.5rem / 1.875rem | 600 | 0 | Plex Sans | page title; the run headline sentence (ink, never red) |
| `h2` | 18px / 24px | 1.125rem / 1.5rem | 600 | 0 | Plex Sans | section heading |
| `h3` | 14px / 20px | 0.875rem / 1.25rem | 600 | 0 | Plex Sans | sub-section, settings field group, inspector section |
| `body-lg` | 14px / 20px | 0.875rem / 1.25rem | 400 | 0 | Plex Sans | prose: settings help text, approval detail, setup guide, empty states |
| `body` | 13px / 20px | 0.8125rem / 1.25rem | 400 | 0 | Plex Sans | default; table rows; navigation |
| `small` | 12px / 16px | 0.75rem / 1rem | 400 | 0 | Plex Sans | column heads (sentence case, ink-2), captions, ids, hints |
| `mono` | 13px / 20px | 0.8125rem / 1.25rem | 400 | 0 | Plex Mono | numbers, ids, paths, event types, commands (tabular-nums) |
| `mono-small` | 12px / 16px | 0.75rem / 1rem | 400 | 0 | Plex Mono | the same at 12/16 in dense rows |
<!-- tokens:report:type:end -->

Custom properties: `--type-<step>-size`, `--type-<step>-line`, `--type-<step>-weight`, `--type-<step>-tracking`,
`--weight-regular` (400), `--weight-strong` (600), `--tracking-none` (0), `--font-sans`, `--font-mono`, `--measure` (72ch). The
pre-pass-2 `--fs-headline` (1.5rem), `--fs-title` (1.125rem), `--fs-body` (0.8125rem) and `--fs-small` (0.75rem) alias `h1`, `h2`,
`body` and `small` size with identical values, so nothing moved. The 33 distinct font sizes in the stylesheets are not migrated in
this step; step 5 rebuilds each screen on the seven steps.

### 3.2 Font pairing and licences

| Role | Face | Fallback stack | Licence |
| --- | --- | --- | --- |
| Headings, text, navigation (primary and secondary: one family, weights 400 and 600) | IBM Plex Sans | `"IBM Plex Sans", ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif` | SIL OFL 1.1, free, self-hosted (`apps/web/src/fonts/OFL.txt`) |
| Code, ids, numbers, paths, event types, commands | IBM Plex Mono | `"IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace` | SIL OFL 1.1, free, self-hosted |
| Deprecated (removed in step 5) | Plex Sans Condensed, Plex Serif | `--cond`, `--serif` | SIL OFL 1.1; still declared, no longer preloaded (ADR-166 token decision 6) |

One superfamily gives two voices (text and data) with no third-party request: the files are served from our own origin by
`next/font/local` (ADR-110), which the strict nonce CSP already allows. Heading and body share Plex Sans on purpose: hierarchy is
carried by size and weight 600, not by a second face (the audit counted four faces on the run page). Rationale for keeping Plex
rather than a new pairing is in ADR-166 (alternatives).

### 3.3 Hierarchy rules

- **Which step:** `display` for the three entry screens only; `h1` for the page title and the run headline sentence; `h2` for a
  section; `h3` for a sub-section, a settings field group, an inspector section; `body-lg` for prose people read (help text,
  approval detail, setup guide, empty states); `body` for everything else, tables and navigation included; `small` for column
  heads, captions, ids and hints; `mono` for any value a person might copy.
- **Weight:** 600 for emphasis and headings, 400 for everything else. No 500, no 700. Weight carries emphasis; colour does not and
  caps do not.
- **Case:** sentence case everywhere. Upper case only for acronyms (API, ID, SQL). Never tracked, never in labels, tabs, column
  heads or buttons.
- **Italic and underline:** italic only inside code comments. Underline only for links in running prose; navigation, buttons and
  table links are not underlined (the focus ring and hover colour mark them).
- **Line length:** prose is capped at `--measure` (72ch), including ledes, settings help, approval detail, empty states and the
  run headline. Tables, the timeline and the waterfall use the full width; they are data, not prose. Rows keep to one line with
  an ellipsis and the full value on focus or in the inspector.
- **Colour in type:** `text.primary` for content, `text.secondary` for supporting text, `text.link` for links, `status.fault`
  only for the failed word, `status.caution` only for waiting, retry, expired, near limit and destructive text.

## 4. Token naming schema (Style Dictionary form)

`category.type.item` in `tokens.json`; the CSS variable is the path joined with hyphens, dropping `primitive` and `scale`
(an optional `css` field overrides it, used for `--font-sans`, `--font-mono`, `--measure` and `--weight-*`).

```text
color.primitive.<scale>.<step>        neutral 0..900, accent, red, amber, orange      --color-neutral-50
color.<group>.<name>                  group: bg | text | border | interactive | status | brand | legacy
                                      { "light": "{ref}", "dark": "{ref}", "use": "..." }   --color-bg-canvas
type.family.<name> | type.weight.<name> | type.tracking.<name> | type.measure.<name>
type.scale.<step>.<size|line|weight|tracking>                                          --type-body-size
space.<n> (4 px baseline: 1 2 3 4 6 8 12) | layout.<name> | radius.<none|control>      --space-4, --radius-control
motion.duration.<name> (zero under reduced motion) | motion.easing.<name>              --motion-duration-fast
alias.<old-name>                      { "to": "<token path>", "status": "aliased|kept|delete" }
$contrast                             the pair list the computed table and the test read
```

Rules: components read semantic tokens only (never `color.primitive.*`); a new role needs a row in `$contrast`; a literal colour in
a stylesheet fails `tests/tokens-colour-rules.test.ts`.

## 5. The alias layer: status of every pre-pass-2 custom property

Nothing was deleted in step 4. "Readers" counts `var(--name` occurrences in `src/**` and `e2e/**` (CSS, TSX and inline styles);
the older token tests read values by name through `tests/tokenCss.ts`. Status: **aliased** = now resolves to a new token,
step 5 migrates the readers and deletes the alias; **kept** = unchanged value and definition (no token for it yet);
**deleted in step 5** = the role is removed by the direction (or it is an ADR-111 alias of a removed role), kept alive only so
the old screens keep working.

<!-- tokens:report:aliases:start -->
46 old custom properties: 19 aliased to a faithful new token, 27 kept unchanged, 0 to be deleted in step 5 (their role is removed, or they alias a removed role).

| Old name | Status | Now | Readers | Note |
| --- | --- | --- | --- | --- |
| `--bg` | aliased | `--color-bg-canvas` | 4 |  |
| `--surface` | aliased | `--color-bg-raised` | 19 | collapses into canvas in step 5 (tables and panels sit directly on it) |
| `--border` | aliased | `--color-border-subtle` | 44 |  |
| `--border-strong` | aliased | `--color-border-strong` | 7 | faithful (old value was 1.8:1, new 1.6:1); step 5 moves field and button boundaries to color.border.control (3:1) |
| `--ink` | aliased | `--color-text-primary` | 19 |  |
| `--muted` | aliased | `--color-text-secondary` | 33 |  |
| `--accent` | aliased | `--color-interactive-default` | 24 |  |
| `--sel` | aliased | `--color-bg-wash` | 3 |  |
| `--fail` | aliased | `--color-status-fault` | 8 |  |
| `--wait` | aliased | `--color-status-caution` | 17 |  |
| `--mark` | aliased | `--color-brand-mark` | 0 |  |
| `--sans` | aliased | `--font-sans` | 25 |  |
| `--mono` | aliased | `--font-mono` | 25 |  |
| `--fs-headline` | aliased | `--type-h1-size` | 1 |  |
| `--fs-title` | aliased | `--type-h2-size` | 7 |  |
| `--fs-body` | aliased | `--type-body-size` | 7 |  |
| `--fs-small` | aliased | `--type-small-size` | 1 |  |
| `--ease-out` | aliased | `--motion-easing-out` | 5 |  |
| `--t-fast` | kept | (defined in globals.css, unchanged) | 0 | motion contract (tests/motion-contract.test.ts reads globals.css); motion.duration.* are the new names, step 5 aliases these |
| `--t-base` | kept | (defined in globals.css, unchanged) | 0 | as --t-fast |
| `--t-slow` | kept | (defined in globals.css, unchanged) | 0 | as --t-fast |
| `--t-land` | kept | (defined in globals.css, unchanged) | 1 | as --t-fast |
| `--top-h` | aliased | `--layout-topbar` | 4 |  |
| `--illus-accent` | kept | (unchanged) | 2 |  |
| `--insp-w` | kept | (unchanged) | 3 |  |
| `--pol-lh` | kept | (unchanged) | 3 |  |
| `--t-exit` | kept | (unchanged) | 1 | motion contract duration |
| `--t-fault` | kept | (unchanged) | 2 | motion contract duration |
| `--t-panel` | kept | (unchanged) | 1 | motion contract duration |
| `--wf-a0` | kept | (unchanged) | 1 | waterfall data colour (ADR-071); desaturation and token move in step 5 |
| `--wf-a1` | kept | (unchanged) | 1 | waterfall data colour (ADR-071); desaturation and token move in step 5 |
| `--wf-a2` | kept | (unchanged) | 1 | waterfall data colour (ADR-071); desaturation and token move in step 5 |
| `--wf-a3` | kept | (unchanged) | 1 | waterfall data colour (ADR-071); desaturation and token move in step 5 |
| `--wf-a4` | kept | (unchanged) | 1 | waterfall data colour (ADR-071); desaturation and token move in step 5 |
| `--wf-a5` | kept | (unchanged) | 1 | waterfall data colour (ADR-071); desaturation and token move in step 5 |
| `--wf-a6` | kept | (unchanged) | 1 | waterfall data colour (ADR-071); desaturation and token move in step 5 |
| `--wf-a7` | kept | (unchanged) | 1 | waterfall data colour (ADR-071); desaturation and token move in step 5 |
| `--wf-agent` | kept | (unchanged) | 1 | waterfall data colour (ADR-071); desaturation and token move in step 5 |
| `--wf-custom` | kept | (unchanged) | 1 | waterfall data colour (ADR-071); desaturation and token move in step 5 |
| `--wf-db` | kept | (unchanged) | 1 | waterfall data colour (ADR-071); desaturation and token move in step 5 |
| `--wf-label-w` | kept | (unchanged) | 3 | layout width |
| `--wf-llm` | kept | (unchanged) | 1 | waterfall data colour (ADR-071); desaturation and token move in step 5 |
| `--wf-meta-w` | kept | (unchanged) | 3 | layout width |
| `--wf-other` | kept | (unchanged) | 1 | waterfall data colour (ADR-071); desaturation and token move in step 5 |
| `--wf-shell` | kept | (unchanged) | 1 | waterfall data colour (ADR-071); desaturation and token move in step 5 |
| `--wf-tool` | kept | (unchanged) | 2 | waterfall data colour (ADR-071); desaturation and token move in step 5 |
<!-- tokens:report:aliases:end -->

## 6. Visual check

Screens before (the 19c tip) and after (this step), Runs list and a failed run, light and dark, 1440 and 375, in
`docs/screenshots/pass-2/step4-{before,after}-{runs,run}-{1440,375}-{light,dark}.png` (`apps/web/scripts/shots-step4.mjs`,
fixtures server). They are intentionally near-identical: this step changes values, not layout. What changed and was looked at:
the light canvas is a touch cooler (`#eef1f4` to `#f6f8fa`), the dark canvas darker (`#12161b` to `#101419`), borders lighter (the
direction's hairline), ink-2 text a shade lighter in dark; every label, status word, fault line and tinted row stayed legible.
Axe stays clean on every route in both schemes (the fixtures Playwright run, `e2e/design.spec.ts`).

## 7. Handed to step 5

1. Migrate readers off the aliases (counts above) onto `--color-*`, `--type-*`, `--space-*`, `--radius-*`, then delete each alias
   and its `color.legacy.*` token; `tokens:check` and `tests/tokens-structure.test.ts` ("every pre-pass-2 custom property still
   resolves") guard it.
2. Shrink the ratchets to zero: the red allow-list in `tests/tokens-use.test.ts` (KI-272), the raw-colour counts in
   `tests/tokens-colour-rules.test.ts` (KI-271).
3. Delete the Condensed and Serif faces, files, `--cond`, `--serif` and the 500 weight (KI-273); move controls to
   `color.border.control` (KI-274); alias `--t-*` to the motion tokens (KI-275).
4. Replace the 33 font sizes, 12 radii and 9 tracking values with the seven steps, two radii and none.

# Brand mark options (Phase 22)

Three marks drawn for the owner to choose from. All are 24 x 24 SVG on a 2 px stroke with round caps, drawn as paths (not a font glyph),
and use `currentColor` so they follow the ink colour in both themes. Files: `apps/web/public/brand/option-a.svg` (the chosen mark, shipped), and the unused options
`docs/design/brand-mark-options/option-b.svg`, `option-c.svg` (kept out of the public assets). Each was checked at 24 px and 16 px (the stroke stays 1.3 px or more at 16 px).

## Option A: trace with a cut (wired)

A recorder trace line (flat, a spike, flat) that stops, then resumes after a single slanted cut. It says what the product does in one
shape: a run is a line of events and we mark where it broke. Reads as "a pulse/trace" at 16 px; the cut is the memorable part and echoes
the fault line on the timeline. Colour: `currentColor` only, no red (red is reserved for failure, ADR-111).

## Option B: black box with a trace

A rounded square (the "box") holding a short trace, with a gap in the right edge of the outline, as if the box were being read out.
Literal and instantly "flight recorder", but the most generic of the three and the busiest at 16 px.

## Option C: tape reels with a fault dot

Two reels (the recorder tape) joined by a line carrying one dot in the failure red. The only option that uses colour: the dot is the
single place red appears, consistent with "red means failure". Strong as an illustration, weaker as an icon at 16 px (two circles and a dot
compete) and it needs the red to work, so it is poorer on a monochrome favicon.

## Wired choice

Option A is in `apps/web/src/components/shell/Brand.tsx` (inline SVG, so it takes the surrounding text colour) and
`apps/web/src/app/icon.svg` (favicon with a light/dark ink via `prefers-color-scheme`). Switching is a path swap in those two files.
No claim about the company's existing identity is made; these are new drawings.

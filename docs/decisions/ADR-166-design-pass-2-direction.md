# ADR-166: Design pass 2 direction ("One Red Line")

Status: Proposed (design pass 2, step 1; written 2026-10-16). Becomes Accepted when step 4 lands the tokens. Amends ADR-110
(fonts), ADR-111 (visual direction) and the Phase 21 decisions in `docs/plans/completed/phase-21-ui-shell-and-design-pass.md`
(D1 frame, D3 rail). Changes no data contract and no API. The number 166 is the one the pipeline reserved
(`docs/design/design-pass-2-pipeline.md`); ADR-159 to ADR-165 are left free for Phase 19b and 19c.
Date: 2026-10-16
Phase: design pass 2

## Context

After running the Phase 21/22 UI the user said it "looks very convoluted". The audit in `docs/design/pass-2/01-direction.md`
Part A counted why: a 224 px rail with two bordered switcher boxes and eight links plus a breadcrumb that repeats the rail; an
Overview with five stat blocks and two nine-column tables and no focal point; a runs list with three filtering systems and five
status hues; a failed run page with six bands of header before the first event, nine readouts, four typefaces, fourteen type
sizes and about twenty red elements; nine settings tabs with no shared layout; and stylesheets carrying 33 font sizes, 12 radii,
9 tracking values and six phase layers that win over each other by specificity.

ADR-111's rule "red means failure and nothing else" is obeyed by the code and still fails the reader, because it bounds the hue
and not the count. The "flight-deck instrument panel" metaphor (Phase 21) is the source of the condensed upper-case labels, the
readout strips and the key caps; ADR-111's serif failure sentence added a fourth voice.

## Decision

1. **Direction: "One Red Line".** A calm, editorial-technical surface (Linear / Stripe-docs restraint) whose single signature is
   the fault line. Red appears exactly twice in the product: the 2 px fault line (with its label, and a 16 px miniature dash before
   a failure sentence in lists) and the status word Failed / Timed out / Blocked. No red fills, tints, headlines, counts or bars.
   Dials: variance 2, motion 2, density 5 (data dense, chrome airy).
2. **Navigation: one top bar, five items.** The left rail, the switcher boxes and the rail foot are removed. The top bar holds the
   brand, Runs, Review, Analytics, Experiments, Settings, a scope control ("workspace / project"), a search button (`/`) and a
   person menu (email, sign out). Overview is merged into Runs; Approvals and Security are two tabs of one "Review" page (routes
   unchanged); Alerts moves under Settings. `nav[aria-label="Primary"]` keeps its name.
3. **Typefaces: Plex Sans and Plex Mono only.** Plex Sans Condensed and Plex Serif are removed from the app (amends ADR-110 and
   ADR-111 "Plex Serif"). Seven type steps (32/24/18/14/14/13/12 with fixed px line heights), weights 400 and 600, no
   letter-spacing, no upper-case labels, sentence-case column heads.
4. **Palette: eleven roles.** canvas, raised, hairline, hairline-strong, ink, ink-2, accent, accent-wash, fail, caution, mark
   (values in `01-direction.md` Part B.4, OKLCH with hex). Removed: `--surface-2`, `--ok`, `--ok-bg`, `--info`, `--fail-bg`,
   `--wait-bg`, the accent fill, and the ADR-111 aliases once nothing reads them. Success is a check glyph in ink-2; running is an
   accent dot; waiting, retry, expired and destructive are caution text.
5. **Status grammar kept, fills dropped.** Glyph plus word (ADR-111), severity by weight (ADR-131), policy outcomes by glyph and
   weight (ADR-141 UI) all stay. The one filled chip for a failed run (ADR-111) is replaced by the red word.
6. **Run page.** Three header lines (title line with status and age; the headline sentence in ink at 24 px; five figures with the
   rest under Details), five tabs (Timeline, Waterfall, Evidence, Findings, Evaluations), a Steps column, the inspector. The
   context column's Findings, Health, Agents and Evaluations sections are removed from the column; health dimensions are not shown
   on the run page at all (the findings say what matters; the numbers stay in the API).
7. **Motion.** Keep the fault-line draw, the inspector slide and the jump outline; remove the headline fade and the live-row rise.
   The reduced-motion contract and `tests/motion-contract.test.ts` are unchanged.
8. **One settings pattern** for all nine tabs (grouped left sub-nav; title, one-sentence lede, hairline sections with a label column
   and a control column, actions at the end of a section; no cards). Quotas' cards and progress bars become one row per quota.
9. **Shapes.** Radii 4 px on controls and 0 elsewhere; one hairline; no shadows; no tone bars; one notice style; one tab component;
   one table style. Illustrations only on the first-run guide and not-found.

## Alternatives considered

- **Keep the instrument-panel metaphor and tidy it** (fewer readouts, fewer colours, same faces). Rejected: the audit traces the
  extra typefaces, caps, strips and key caps to the metaphor itself; tidying leaves the cause and the next phase re-adds a strip.
- **A lighter rail (11-12 rem, nav only)** instead of a top bar. Rejected for the run page: the rail costs width the timeline needs
  whenever the inspector is open (today the pane drops under 480 px at 1408 px, `docs/screenshots/phase-21/README.md`), and with
  five items the bar costs no height the header stack does not already pay. Recorded because a rail is the easier revert: the
  `AppShell` keeps one nav component either way.
- **Keep Overview as a thin "today" page.** Rejected: its figures are one sentence; its two tables are the runs list.
- **Fold Approvals, Security and Alerts into one "Attention" area** (the pipeline's question). Partly adopted: Approvals and
  Security are queues a person must act on and share a list shape; Alerts is configuration with a log and belongs with API keys and
  quotas. Keeping Security separate from Approvals was the other option; rejected because both are "findings about agents a
  reviewer reads", both are workspace-scoped and both have a three-state tab bar today.
- **A new type pairing** (Geist Sans + Geist Mono, or Source Sans 3 + Source Code Pro, both OFL). Rejected: the complaint is
  quantity, not the face; a third family swap in two phases is its own noise. Plex stays, reduced to two families.
- **Keep the serif for the one failure sentence** (ADR-111's argument: "reserved for the one sentence that matters"). Rejected:
  with the fault line as the only red mark, the sentence no longer needs a second voice to be found; the serif was the
  fourth typeface on screen.
- **Keep green for success.** Rejected: on a list where most rows succeed, green is the most frequent colour and so the least
  informative; the check glyph in ink-2 carries it.
- **Keep the filled red chip for failed runs** (scans well in a list). Rejected: with no other colour in the list, a red word scans
  as well; the chip is a fill, and fills are what the eye counts.

## Which ADR-111 rules are kept or amended

| ADR-111 rule | Status |
| --- | --- |
| Fault line marks the first error; rows above are quiet | Kept; "rows below carry a faint tint" amended to no tint |
| Palette hex values | Kept for accent, fail, caution, mark; canvas and ink re-specified in OKLCH (`01-direction.md` B.4); surface collapses into canvas |
| Old names stay as aliases | Amended: aliases are deleted once `grep` finds no reader (step 4 proves it) |
| Red is for failure only; `tokens-use.test.ts` guards it | Kept and tightened: red only for the fault line and the failed word; the allow-list shrinks |
| Destructive buttons and removed diff lines in amber | Kept |
| Green stays but is quiet | Amended: green removed |
| Status is glyph plus word, no outline, no upper case | Kept |
| Only a failure-tone status is a filled chip | Amended: no filled chip |
| Type scale: Sans 13 px, Condensed headings, Mono numbers, Serif headline | Amended: Sans and Mono only; seven steps |
| Plex Serif vendored and loaded | Amended: removed from the app (files deleted; OFL note stays for Sans and Mono) |
| Brand mark option A | Kept |
| Motion language 120-280 ms, ease-out, transform and opacity, zero under reduce | Kept; two animations removed (headline fade, live-row rise) |
| Phase 13 severity by shield glyph and weight, never red | Kept |
| Phase 14 outcomes by glyph and weight; denial is ink; waiting amber | Kept |

## Consequences

- Steps 2 and 3 use this ADR and `01-direction.md` as the rubric; step 4 implements the tokens and type; step 5 the screens.
- Tests that assert copy or structure change in known places and are listed with reasons in step 5's plan: the "Overview" and
  rail links in `e2e/*` and `tests/*` (4 files name the Primary nav, 3 click Overview or Experiments by name), the settings tab
  labels (7 files), `tokens.test.ts` and `tokens-contrast.test.ts` (the token set), `tokens-use.test.ts` (the red allow-list),
  `security-severity.test.tsx` (pairs), the `.seg-item` radio tabs in the run-page specs.
- The ids, model and ordering of a run move from the header into Details; nothing is lost, one click further.
- Health dimensions leave the run page UI. The API, the findings and `rebuild-findings` are untouched (INV-2); a later phase may
  surface them on Analytics.
- The `docs/design/ui-direction.md` "Frame" and "Run page" sections are rewritten when step 5 lands; until then that file
  describes the shipped UI and this ADR the target.
- Font payload drops from 191.5 KB to about 100 KB (five files).

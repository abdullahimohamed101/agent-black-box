# Design pass 2: review and fixes

Status: complete on `feature/design-pass-2` (not pushed). The Fable design review (taste system, "convoluted" rubric,
accessibility and code pass) produced P1, P2 and P3 findings and a list of busy-screen items; this records the outcome of each.

| Finding | Outcome | Reason / change | Commit |
| --- | --- | --- | --- |
| P1-1 | fixed | Analytics and Runs sentences say what the counts include (failed or blocked, timed out) | f16bca6 |
| P1-2 | fixed | The experiment verdict decides on the printed precision | 4dcc2c2 |
| P2-1, P2-2 | fixed | A rate that only rounds to zero reads "under 1%"; retries count all runs | f16bca6 |
| P2-3, P2-5 | fixed | Verdict capitalised; says when it compares 2 of N variants | 4dcc2c2 |
| P2-4, P2-6 | fixed | Review without tabs renders no labelled tabpanel; the role test renders ReviewFrame for all six roles | 373b581 |
| P3-1 | fixed | `.st.st` is `.st` and the stale comment is gone | fd76539 |
| P3-2 | fixed | `!important` removed from `trace-notice.css` (selector `.trace-notice p.trace-notice-title` wins by specificity) | fd76539 |
| P3-3 | fixed | `ApprovalsRailCount` is `ApprovalsCount`; `PrimaryNav` uses `securityPath()` | fd76539 |
| P3-4 | fixed | One-off `shots-*`, `count-step5d`, `screens5d` scripts moved to `docs/design/pass-2/scripts/` (run by copying into `apps/web/scripts`); doc references fixed | 25ab7b3, 32b2cb1 |
| P3-5 | fixed | The Settings prefix of the title line is plain text, not a self-link (`TitleLine` takes an optional `href`) | b3b4b89 |
| P3-6 | fixed | Cost chart marks the cheaper variant | 4dcc2c2 |
| P3-7 | fixed | Runs Findings column uses the Security shield glyph (filled for critical) | e6d0a96 |
| P3-8 | fixed | Charts with fewer than three days cap the column at 1 rem and print the value beside it (closes KI-296) | 68a7b39 |
| P3-9 | fixed | "Where the money was wasted" readout strip is one sentence with the same figures | 04c1809 |
| P3-10 | fixed (documented) | Stress-checker allowances noted in `07-stress-test-report.md` | f8b079f |
| P3-11 | not in the fixer brief | Not listed in the resumed fixer's task; confirm against the review text | - |

Busy-screen items

| Item | Outcome | Reason / change | Commit |
| --- | --- | --- | --- |
| 1 Analytics Cost: three chart treatments | filed KI-302 | A design choice across charts, not a trivial fix | - |
| 2 Runs facets open at 1440 | filed KI-301 | Needs remembered state, an active-facet count and focus handling | - |
| 3 One heading size for block heads in Experiments | fixed | The create block moved from 18/24 to the 14/20 block-head step; KI-294 narrowed to Settings vs the rest | 851a472 |
| 4 Duplicate pending count | fixed | The Pending chip drops the number; the "N waiting" line keeps it | 89da070 |
| 5 Settings at 375: five stacked bands | filed KI-303 | Needs a layout decision | - |
| 6 Compare: four label vocabularies | filed KI-304 | Needs a naming decision across the view | - |

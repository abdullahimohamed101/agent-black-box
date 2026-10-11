# ADR-092: Run comparison by normalised step alignment, computed on read, bounded by a work budget, role-aware

Status: Accepted
Date: 2026-10-11
Phase: 11
Accepted: 2026-10-11 (Phase 11 implementation complete; pending security review). Final design as built: the query parameter is `with_run_id`; `divergence` is `{index, relation}` and the row is `steps[index]`; the content-projection divergence on the demo pair is the `file.read` that opens the second edit (immediately followed by the changed `file.modified`), and the metadata divergence is the second test run; a fixed work cap of 4,000,000 comparisons bounds the Myers diff next to `ABB_COMPARE_MAX_EDITS`.

## Context
Spec §98: comparison must identify divergence, align semantically equivalent actions and normalise generated ids and timestamps;
§86 metrics; INV-7 (no ad-hoc SQL against events); ADR-061 (`payload.read` withholds command text and paths; a comparison must not become an
equality oracle on them); low-cardinality step sequences (an agent alternating a few tools) are the worst case of LCS-style matchers.

## Decision
`GET /v1/runs/{id}/compare?with_run_id=` derives a step list per run from at most `ABB_COMPARE_MAX_EVENTS` events in canonical order
(closing lifecycle events and point events of the file, git, reliability, security and approval families), gives each step a key and a label built
from **the projection the caller may see**: with `payload.read`, `(family, normalised name)` where the table-driven normaliser replaces word-bounded
ULIDs/UUIDs, long hex, timestamps, durations and digit runs with `#`, strips temp and workdir prefixes, collapses whitespace and lower-cases; without
it, `(family, tool name | model | span kind)` with no command text, path or content span name anywhere. Alignment strips the common prefix and
suffix (which fixes the first structural divergence in linear time) and aligns the middle with a Myers O(ND) diff bounded by `ABB_COMPARE_MAX_EDITS`
and `ABB_COMPARE_MAX_STEPS`; beyond the budget the middle is reported as unaligned blocks and `alignment_quality: partial`. Status is excluded from
keys so the same action with a different outcome aligns as `changed`; `changed`, the divergence, the first outcome difference and the file figures
are computed from the same projection (for metadata actors `files` is per-side counts only). The response also holds summary deltas, cost by model
and the latest evaluation scores per evaluator; nothing is stored; extraction and alignment run in a thread. The alignment mode and quality are
stated in the response and on the page.

## Alternatives
Store comparisons (derived, cheap and role-dependent). Align by timestamp (skew and nondeterministic durations). `difflib.
SequenceMatcher` (quadratic on low-cardinality input with `autojunk=False`, and `autojunk=True` junks every frequent key). A plain O(n*m) LCS (4M
cells in Python at the cap). Full event-level diffs (noise; the step level is what §98's "aligned trace steps" means). Content keys with withheld
labels (an equality oracle).

## Consequences
Any two visible runs compare in one bounded request; divergence points can differ by role, by design; very long or very noisy runs
are compared partially, which the response and UI state (KI-105).

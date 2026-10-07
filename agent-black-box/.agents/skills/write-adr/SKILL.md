---
name: write-adr
description: Record an architecture decision as docs/decisions/ADR-NNN-*.md and update the DECISIONS.md index. Use when deviating from the spec or choosing among real alternatives.
---

# Write ADR

Create `docs/decisions/ADR-NNN-<slug>.md` (next free number; the spec §141 reserves
ADR-001..010 for its initial set) and add a line to `docs/DECISIONS.md`.

Required sections: Status (Proposed/Accepted/Superseded), Date, Context, Decision,
Alternatives, Consequences (positive and negative), Migration implications, Revisit
conditions (for deferrals: the measured trigger, e.g. "sustained >5,000 events/s per
API deployment", spec §57).

Rules: an ADR that deviates from the spec names the section it amends and explains how
original product goals are preserved. Never edit an Accepted ADR's decision; supersede it.
Scale-technology ADRs must include measured evidence, not estimates.

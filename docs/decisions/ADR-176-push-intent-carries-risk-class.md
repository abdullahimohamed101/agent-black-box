# ADR-176: The `github.push` intent carries the classifier's risk class

Status: Accepted (Phase 20, launch film). Owner request; implements option A of `docs/design/launch/video-cinematic-brief.md` section 8.2.
Date: 2026-10-19
Phase: 20

## Context

The approvals panel prints "Unclassified" for a push waiting for approval. Not a UI bug: `CodingRecorder.guard_push()` sent `github.push` with `risk_class=None`, while `guard_command()` sends the shell classifier's class for the same push (`git push` is R2, a forced or deleting push R3). The demo policy rule `approve-push-main` matches on capability and branch, so the request on screen was the unclassified one. Every push approval had the gap.

## Decision

1. `guard_push(remote, branch, *, force=False)` derives the class by classifying the exact command `git_push` runs (`git push -u [--force-with-lease] <remote> <branch>`) with the recorder's classifier (a custom classifier wins, as in `guard_command`). A plain push is R2, a forced one R3. The derivation is never invented: a class outside R0-R4, or a classifier that raises, leaves it unset and the ask still goes out (INV-4).
2. `git_push(remote, branch, *, force=False)` gains `force`; `True` runs `--force-with-lease`, asks with R3 and, because R3 defaults to fail-closed, an unreachable policy service now blocks it (the shell step was already R3 and closed).
3. The dry-run table (`apps/api/.../policies/mapping.py`, ADR-140) maps `git.push` to R2 so the dry run keeps predicting what enforcement sends (a test pins SDK and table together). The `git.push` event does not record force, so the dry run predicts the plain case; a forced push is still caught by its R3 shell step.
4. No schema change: `risk_class` was already an optional field of every intent; policy evaluation results change as described under Consequences. A public-API addition in the Python SDK with a default.
5. The demo never hard-codes a class: the approval shows R2 because the SDK computed it.

## Alternatives considered

- Hard-code `R3` in the demo call: shows a class the classifier did not compute. Refused.
- A separate push classifier: a second table that would drift from the shell one.

## Consequences

Every `github.push` approval shows its class. Documented in `docs/architecture/sdk-python.md`.

- Approval binding: `risk_class` is part of the SDK's canonical intent and of the server's approval binding (`apps/api` `approvals/intent.py`; the store's lock key is per run and key). An approval granted to a push intent from an older SDK (`risk_class` null) does not bind to the same push from this SDK, which sends R2 or R3. Only a run that upgrades the SDK mid-flight is affected: it asks again.
- Policy matching: a rule with `risk_class: [R2]` (or `[R2, R3]`) on `github.*` or `*` now matches live pushes and their dry runs, which previously fell through to the default. Rules without a risk filter are unchanged. A rule with `risk_class: [R3]` still does not match a plain push (R2). The capability is matched first, so `shell.execute` is not over-matched by a `github.*` rule.
- A forced push whose class cannot be derived (a classifier fault) fails closed whatever the configured fail mode; a plain push keeps the configured mode.
- `guard_push` validates `remote` and `branch` exactly as `git_push` does and raises `ValueError` for an unsafe name before asking anything.

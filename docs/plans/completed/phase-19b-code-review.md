# Phase 19b code review (security pass)

Branch `feature/phase-19b-data-lifecycle`, 2026-10-16. Independent, adversarial review of the data lifecycle (the `abb_retention` role and grants, policy storage and API, enforcement tiers and ordering, the web Retention page, backup and restore, secret rotation, project and workspace purge, `retention-e2e.sh`, `backup-restore-e2e.sh`) against the plan (D1-D6, eight gates), ADR-159 to ADR-163 and the AGENTS.md invariants, with a security pass. Reviewer: a Fable reviewer. Initial verdict: **not merge-ready**. Counts: no P0, one P1, five P2, several P3. Every P1 and P2 was fixed with a fail-before test or a corrected document; a delta review of the fixes (also Fable) found the branch **merge-ready**, with no P0, P1 or P2 and four P3s, recorded as the KI-254 note and KI-255 to KI-257. (Written at close-out from the findings and dispositions the reviewers handed back; it records those, not every minor remark.)

## P1

- **P1-1 A workspace purge wedged permanently on a row that arrived after the recorded `workspace_rows` step.** The fail-closed leftover check found the late row and refused to finish, and every rerun refused again. Fixed: the purge re-drains before the fail-closed leftover check; the drain is bounded by batch checkpoints.

## P2

1. **A pass that hit the rows cap silently skipped the rollup refresh, the audit tier and the operational prunes.** Fixed: each pass has its own allowance and a `Halt` is handled per pass.
2. **The audit tier could be erased by an admin after 7 days.** Fixed: an audit floor of 90 days (`ABB_RETENTION_AUDIT_FLOOR_DAYS`), `retention.audit.write` is owner-only, and `retention.*` audit rows are exempt for 365 days.
3. **No grace period after shortening a period.** Fixed: `ABB_RETENTION_GRACE_HOURS` (default 24); the longer period applies until `effective_at`, lengthening is immediate, and a shortening is cancelled by saving the previous value.
4. **The runtime role could `UPDATE` `runs.trace_state` and `purge_state`.** Fixed by migration 0105: `UPDATE` on `runs`, `projects` and `workspaces` is narrowed to named columns (KI-212, KI-217).
5. **The wording about the eligibility re-check at the `deleting` mark was wrong.** The re-check exists at the progress-row write, before any data is touched; the documents were corrected and the residual is KI-253.

## P3 (fixed)

A test that the API never imports the retention code; a clamp on the key-derivation iteration count; a note that Postgres 15 or later is required; a note on the compose environment pin; documentation drift; the stranger prune and `login_states` documented (KI-252); the e2e script cleans up its own share; the e2e scripts refuse a remote database.

## Delta review of the fixes

Merge-ready; no P0, P1 or P2. Four P3s recorded: a saved audit period below the new floor shows its saved number in the API and page (the note on KI-254), the Retention page's cancel-shortening saves an explicit number (KI-255), a no-op PUT after a shortening took effect rewrites `updated_at`/`updated_by` without an audit row (KI-256), and the e2e remote-database refusal ignores a `?host=`/`hostaddr=` query parameter (KI-257).

## Verified correct (reviewer's list)

Every `DELETE` and `UPDATE` carries `workspace_id` and no cross-tenant path exists; the retention role's grants are explicit and pinned by a test; the tar scan on restore is safe; the encrypted bundle is encrypt-then-MAC and the MAC is checked before decryption; the snapshot export is held across the dump and the artifact copy; the grants enumeration is complete.

## Residual risks

KI-192 to KI-219 (those still open), KI-224, KI-250 to KI-257 in `docs/KNOWN_ISSUES.md`, filed as GitHub issues in the close-out PR.

## CI note

A test-isolation bug failed CI once on #206: a runner-settings test read the `ABB_DATABASE_URL` that CI sets, which satisfied the local fallback. Fixed in `58a6bae`. Lesson: emulate CI's environment (`ABB_DATABASE_URL` set, `--extra otel`) when verifying.

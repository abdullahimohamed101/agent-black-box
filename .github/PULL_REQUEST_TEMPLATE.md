## What and why

<!-- One paragraph. Link the issue or plan this implements. -->

## Checks

- [ ] `scripts/quality.sh quick` passes (and `full` when migrations, builds or the API contract changed)
- [ ] Tests added or updated; no test was removed, skipped or weakened to get green
- [ ] UI changes were exercised in a browser
- [ ] No architectural invariant in `AGENTS.md` is broken (immutable events, workspace-scoped data, SDK never crashes the host, redaction before export, typed events, storage behind contracts); a deviation has an ADR
- [ ] No secrets, `.env` contents or real payloads in the diff
- [ ] Docs updated where behavior changed

By submitting this pull request I license my contribution under Apache-2.0 (section 5 of `LICENSE`).

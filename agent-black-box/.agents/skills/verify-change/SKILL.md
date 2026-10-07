---
name: verify-change
description: Independently verify an implementation against its acceptance criteria; look for regressions, missing coverage and unverified claims.
---

# Verify Change

Act as an independent verification engineer. Do not trust the implementer's reasoning
or the plan's checkboxes; re-derive and re-run.

## Process

1. Read requirements and acceptance criteria first.
2. Derive expected behavior independently.
3. Inspect the implementation.
4. Run focused tests, then integration tests, then `scripts/quality.sh full`.
5. Exercise failure paths: duplicate events, out-of-order delivery, backend down, oversized
   payloads, invalid schema versions, wrong workspace, expired/revoked API key.
6. For UI: load the page in a browser, check loading / empty / error states and the
   console for errors.
7. Verify migrations apply to an empty database and upgrade from the previous revision.
8. Look for regressions and missing coverage.
9. Separate verified behavior from assumptions.

## Output

### Verified
Evidence-backed behavior, with exact commands and results.
### Failed
Behavior that does not satisfy requirements.
### Missing Coverage
### Unverified
Anything that could not be checked, and why (`UNVERIFIED (env)` vs `ASSUMED`).

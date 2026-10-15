# Phase 19a code review (security pass)

Branch `feature/phase-19a-abuse-resistance`, 2026-10-15. Independent, adversarial review of abuse resistance (shared limiter, failed-auth throttling, quotas, edge hardening, sessions and invitation-only people, login cookie binding, alert port allow-list, search fairness, the hardening e2e) against the plan (D1-D6, eight gates), ADR-152 to ADR-158 and the AGENTS.md invariants, with a security pass. Reviewer: a Fable reviewer. Initial verdict: **not merge-ready**. Counts: no P0, one P1, P2 and P3 findings below. The P1 and every P2 listed here were fixed with fail-before tests; the P3 items are fixed or noted in the ADRs. Residual risks are filed as KI-191 and the KI-173 to KI-190 rows. (Written at close-out from the findings and dispositions the reviewer handed back; it records those, not every minor remark.)

## P1

- **P1-1 Cold valid keys were locked out while a flood kept the global failed-auth window closed.** Reproduced: a valid key this API process had not authenticated recently was admitted only in roughly 100 ms gaps at window boundaries; after a deploy every key is cold. Fixed (ADR-153 point 3, KI-175): a per-process set of active key ids (ids only, refreshed every 60 s in the background, capped at 100,000, loaded by a bounded query) lets a key whose own credential window is open go to full authentication even when the global or address window is closed; random ids are refused from memory with no lookup. Tests: `tests/test_auth_cold_keys.py` and the cold-key case in hardening gate 2.

## P2

- **P2-1 One scope's timeout disabled the shared limiter for every scope for 5 s.** Fixed: a breaker per scope (ADR-152).
- **P2-3 Counter pruning ran on the request path.** Fixed: a single-flight background task (ADR-152).
- **P2-4 The `summary_state` flake (KI-172) diagnosis.** A transient first-attempt failure of the summarize job under load, rescheduled behind a backoff and hidden by the test helper `Api.drain()`; not a product race and not made likelier by 19a. `drain()` now fails loudly with the job's own error when a retry is pending (KI-172 / #184 widened).
- **P2-5 The body cap did not reserve at `Content-Length` admission.** Fixed: the declared size is reserved at admission and reconciled on arrival, timeout or release, bounded by the read timeout (ADR-155).

## P3

- The docs CSP exemption now applies only where the docs routes are served.
- A missing or malformed key logs one rate-limited line per window with a count.
- Migration 0100 revokes DELETE on the three quota tables and adds the partial index on live API keys.
- The startup role check counts ownership of any table of the schema.
- Pseudonym wording for the client-address digest corrected.
- Timeouts on the two long e2e CI jobs.
- `base-uri 'none'` in the page CSP.
- ADR-154 notes that the quota limit is read before the transaction.

## Verified correct (reviewer's list)

Limiter math and bounds; quota admission order and races; the login cookie flow; session IDOR and revocation; the un-invited sign-in branches; invitation revocation; the port allow-list; search gate accounting; migrations 0097 to 0099; authz registry coverage; docs honesty (no claim beyond what is built).

## Residual risks

- KI-191: a person who knows an API key id can close that key id's own failed-auth window with 10 bad secrets and delay that key's first use for up to 60 s (known-valid keys in the process cache keep working).
- KI-187 (plain-http deployments have no `__Host-` cookie protection, #204) and KI-188 (the https e2e uses a test-only TLS proxy, #205) were reported by the cookie-fix step but not written into the register at the time; they were added and filed in this close-out.
- The remaining new rows are KI-173, KI-174, KI-176 to KI-186, KI-189 and KI-190, filed as #187 to #202.

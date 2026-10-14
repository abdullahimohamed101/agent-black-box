# Phase 17 code review (security pass)

Branch `feature/phase-17-alerting`, 2026-10-14. Independent, adversarial review of alerting (destinations, rules, evaluation, delivery, Alerts page) against the plan (D1-D6, eight gates), ADR-150 and ADR-151 and the AGENTS.md invariants, with a security pass. Reviewer: a Fable reviewer. Initial verdict: **not merge-ready**. Counts: no P0, P1 1, P2 6, P3 9. Every P1 and P2 and P3 items 1-5, 8 and 9 were fixed with fail-before tests; P3-6 and P3-7 are noted in the ADRs and not fixed. Dispositions and the tests added are in the plan (`phase-17-alerting.md`, Progress step 5); the amendments are in ADR-150 and ADR-151. Merged as PR #171.

## P1

- **P1-1 A pruned dedupe row let a cost rebuild re-alert every old failed run.** Run-failed alerts relied on a dedupe row that retention prunes; a cost rebuild re-summarises old runs, so each failed run looked new. Fixed: a failed run is announced only on the transition into a failed status, and never when it ended before the retention window. Regression tests added.

## P2

- **P2-1 An owner delete could block behind a delivery tick's row locks.** Fixed: `SKIP LOCKED` in the skip helpers; owner writes `SET LOCAL lock_timeout` and fail fast with `409 ALERT_BUSY`; one email send is capped at one SMTP timeout; the real bounds are documented (KI-163).
- **P2-2 A delivery tick monopolised the worker.** Fixed: default batch 5, and the leases of claimed jobs queued behind a slow one are extended. Those jobs still wait their turn (KI-169 stays open).
- **P2-3 `approval_requested` was non-deterministic for an approval decided quickly.** Fixed: the job loads the approval in any state and the payload carries `details.approval_state`; the e2e suite counts it in the exact-once set again.
- **P2-4 The sweep chain was window-sensitive** (the likely cause of an intermittent missing about-to-expire alert), and a real sweep versus rule-edit deadlock existed (reproduced). Fixed: evaluate and deliver jobs re-arm the sweep, the successor is queued after the checks, and the global lock order is written in ADR-150; the e2e suite now fails on worker "job failed" and "alert check failed" lines.
- **P2-5 Concurrent delivery ticks could deadlock in `apply_health`** (reproduced). Fixed: health is applied in id order.
- **P2-6 Documentation overclaimed.** Corrected in SECURITY, OPERATIONS, the runbook and the ADRs.

## P3

Items 1-5, 8 and 9 fixed (`::/96` refused; the sweep just after UTC midnight also checks the day that ended; the expiring batch uses an anti-join; retention of at least 7 days; one write token per send test; mutation `gcTime: 0`; e2e provisioning fails loudly). P3-6 and P3-7 are noted in the ADRs and not fixed.

## Verified correct (nothing to change)

- **SSRF sender:** pinned connect to the checked address, every DNS answer checked, no redirects, TLS verification on, header and path injection rejected, proxy environment variables ignored.
- **Sealing:** AES-256-GCM with AAD binding row, workspace and purpose; fails closed; decrypting callers limited to the sender and send-test.
- **Payloads:** content-free by construction (fixed shape, ids and names only).
- **Authorization and tenancy:** owners and admins manage; other roles never see destinations; nothing crosses workspaces.
- **Migrations** 0095 and 0096, and the **delivery mechanics** (claiming, retries, dead letters, auto-pause, caps).

## Residual risk

KI-160 (no watchdog on the sweep chain), KI-162 (a destination may be any public host and port; bounded by rate, cap, deadline and auto-pause; the review rated it S2 and suggested a 443-only default, kept S3 pending the egress allow-list in Phase 19), KI-163, KI-166 and KI-169 are open and tracked in `docs/KNOWN_ISSUES.md`.

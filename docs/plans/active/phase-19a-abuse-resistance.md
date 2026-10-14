# Phase 19a: Abuse resistance (production hardening, part 1 of 3)

Status: Planned (2026-10-14). Decisions D1-D6 were settled with the user one question at a time (grill, 2026-10-14). Phase 19 is split into 19a (this), 19b (data lifecycle: retention, backups and a performed restore drill, rollback notes, KI-057, KI-170, secret rotation) and 19c (operability: metrics, tracing, SLOs, dependency health, timeouts and circuit breaking, runbooks, CI pinning, lockfile audit). Each part has its own plan, review and PR.
Owner: api + web. Branch: `feature/phase-19a-abuse-resistance` (from `main` after PR #185).
Depends on: Phases 2 (ingestion limiter), 15 (roles, audit, sessions), 14/17 (policy, approval and alert budgets), 22 (UI grammar).
Spec refs: §94, §106, §116 (context), §62 invariants (INV-1, INV-3, INV-4 style bounds), §90 threat model.

## Outcome

A hosted or self-hosted installation cannot be pushed over by an anonymous flood, a runaway agent or one noisy actor: every limit holds across API processes, failed authentication is throttled without hurting valid traffic, every project has honest quotas that refuse loudly, and the web and API edges carry the standard protections. Closes the two S1 issues (KI-018, KI-019).

## Non-goals

- No Redis, Kafka or other new always-on service (the measured trigger is recorded in the benchmark; see D1).
- No retention, backup or restore work (19b); no metrics, tracing, SLOs or circuit breakers (19c).
- No invitation email (KI-071), no MFA/SSO/SCIM, no per-project roles.
- No alert trigger for quota usage (a later addition).

## Decisions (settled with the user)

- D1 Shared limiter: PostgreSQL-backed fixed-window counters (one small table, `INSERT ... ON CONFLICT DO UPDATE`), with the in-memory limiter kept in front as a cheap local guard. The shared check backs the limits that matter: failed auth, writes per workspace and key, the expensive routes, policy, approval, alert and search budgets (KI-017, 141, 147, 161, 082). Fail open to the local limiter if the database is unreachable (same blast radius as everything else). Edge (reverse proxy) IP limiting is documented as defence in depth, not the only layer. The ADR records the Redis trigger: revisit if the shared counter's measured per-request cost exceeds a stated bound.
- D2 Quotas (KI-018, 040, 055): reject loudly. Per project: 500 distinct agents, 50,000 runs a day, 5 million events a day, 5 GiB total artifact bytes; the per-run event cap stays. Environment defaults, per-project overrides by owner or admin, audited. A refused item gets a fixed code `QUOTA_EXCEEDED` naming the quota, counted in the batch response and visible to the SDK; existing data and runs keep working. Usage is visible on the project settings page and in the project API, with a warning past 80%. No silent folding or dropping of accepted run data.
- D3 Failed-auth throttling (KI-019, 073): count failures only; three shared counters (per client address when `ABB_TRUST_PROXY=1`, per credential prefix or target account, a global failed-auth ceiling); exceeding any gives 429 with `Retry-After`; fixed windows, not escalating; a bounded short-lived in-memory negative cache for key hashes already seen invalid; sign-in starts move onto the shared counters (callbacks and existing sessions are never refused); the first throttle per window writes one capped audit row. Without a trusted proxy the credential and global counters still apply and the docs say address throttling needs the proxy header.
- D4 Rest of the bundle with defaults: global request-body bytes-in-flight cap per process (503 BUSY with Retry-After before the body is read, KI-026); secure headers on web and API (strict CSP, nosniff, frame denial, referrer policy, HSTS only when the deployment says it is behind HTTPS); `/docs` and `/openapi.json` off when `ABB_ENVIRONMENT=production` (KI-023); production refuses to start if the database role can alter tables or is a superuser, a warning in development (KI-031); sessions page with list, revoke one, revoke all others (KI-068); the `__Host-` cookie problem (KI-075) is solved by an ADR-first design reviewed before it is built; no `users` row until an invitation is accepted, with a plain "ask the owner for an invite" page for others (KI-076); demoting or removing a person revokes the open invitations they created, audited (KI-077); search permits become a fair share per workspace (KI-082); alert destinations get a port allow-list defaulting to 443 with a setting to add ports (KI-162).
- D5 Out of 19a on purpose: KI-170 (to 19b), KI-071 (stays out), KI-057 (19b), the 19c items.
- D6 Gates: (1) limits hold across real API processes (two or more behind one database share each budget); (2) a failed-auth flood is bounded with a stated database-load bound, valid ingestion and sessions unaffected, negative cache bounded; (3) each quota rejects with its fixed code, existing runs keep working, usage never crosses projects, usage visible, overrides audited; (4) headers present with no CSP violations in the browser on every page, `/docs` off in production, the body cap sheds load with 503 instead of exhausting memory, the API refuses to start as the database owner in production; (5) sessions list/revoke, no `users` row without an invitation, inviter demotion revokes invitations, cookie ADR implemented and tested, all in the authz matrix and route registry; (6) alert ports outside the allow-list refused at save and delivery, and one actor cannot take all of a workspace's search permits; (7) the shared counter's overhead is measured against a stated bound and the Redis trigger figure is recorded; (8) no existing test weakened, authz matrix and registry updated, clean CI, Fable review with a security pass and no open P0/P1.

## Known issues considered

Pulled in: KI-017, 018, 019, 023, 026, 031, 040, 055, 068, 073, 075, 076, 077, 082, 141, 147, 161, 162. Stays out: KI-170, 057 (19b); KI-021, 072, 103 (19b retention); KI-024, 060 (19c); KI-071.

## Acceptance criteria

The eight gates of D6, each evidenced by a command and its observed output; plus the repo standard: format, lint, mypy (no args), tests, migration check (empty to head and previous head to new head), OpenAPI/client checks, UI exercised in a browser, `scripts/quality.sh full` green in CI, adversarial review with security pass and no open P0/P1.

## Verification plan

Unit and property tests for the counters (window edges, concurrency, fail-open); API tests for each quota, throttle, header and identity change; authz matrix and route registry updates; `hardening-e2e.sh` against a real multi-process stack (two API processes, a worker, the web, a flood generator) with a CI job; benchmark of limiter overhead and flood load; my own gate run; then a Fable review with security pass.

## Risks

- The shared counter sits on the hot ingestion path: measured, with the local guard in front and the Redis trigger recorded.
- Throttling can lock out real users: failures-only counting, fixed windows, and valid traffic never spends the budget.
- Quotas refuse data: loud, fixed codes, generous defaults, visible usage, audited overrides.
- CSP can break the UI: verified in a browser on every page, no inline script.
- Identity changes (no `users` row until invitation, cookie binding) touch login: ADR first, authz matrix, e2e through the real fake provider.
- Overclaiming: limits are abuse rails, not a WAF; the docs say so.

## Ordered steps (one commit unit each)

1. Shared limiter (table, migration, counter service, local guard, fail-open) and move the existing limiters onto it; ADR.
2. Failed-auth throttling (three counters, negative cache, audit) and sign-in starts.
3. Quotas: counters and caps, `QUOTA_EXCEEDED`, overrides with audit, usage API and settings page, SDK handling.
4. Edge: headers, `/docs` per environment, body-bytes cap, startup role check.
5. Identity: sessions page, no `users` row before invitation, invitation revocation on demotion, cookie ADR and implementation.
6. Alerts port allow-list and search fairness.
7. `hardening-e2e.sh` (multi-process), CI job, benchmarks, runbook updates, closing evidence.
8. Review, fixes, push and PR.

## Progress

- 2026-10-14, step 1 (shared limiter) built: `apps/api/src/abb_api/limits/` (closed `Scope` enum, `SharedCounters` over migration 0097's `rate_limit_counters`, `GuardedLimiter` with credit blocks, factory), ADR-152, `scripts/bench_limiter.py`, `docs/benchmarks/phase-19a-limiter.md`. Moved onto it with settings, values and 429 bodies unchanged: ingestion events/bytes, artifact count/bytes, sign-in starts, policy publish/validate/dry-run, approval decisions (key, workspace), alert write/preview/send-test. Left per process on purpose: stream and approval long-poll concurrency caps (KI-017, KI-147 narrowed and open), the denial-audit cap (KI-173). KI-141 and KI-161 resolved (GitHub issues close with the close-out PR); KI-017 and KI-147 resolved in part. New: KI-173, KI-174. Evidence: `tests/limits/` (27 tests: exact counts under 200 concurrent callers, window edge, tenancy, expiry and bounded pruning, fail-open against an unreachable database and a locked table, local guard writes nothing, two simulated processes within the stated bound, two application objects sharing each moved budget, runtime-role privileges) and `tests/test_migrations.py` (0097 up/down).
- 2026-10-14, step 1 gates observed: `ruff check`, `ruff format --check`, `mypy` (no args) clean; `python -m abb_api.openapi --check` exit 0 (no route changed); migrations 0097 up/down and 0096 to 0097 to head pass; authz suite, config, runtime-role, `tests/limits` (28 tests) pass; **full API suite: 3421 passed** (23 min 52 s). Benchmark: default configuration adds 0.03 ms at p95 / 0.12 ms mean to an ingestion request and ~27 counter writes per 1,000 requests; the worst case (a write per request) adds ~3 ms p50 / ~5.7 ms p95 (`docs/benchmarks/phase-19a-limiter.md`). The benchmark found and the fix commit closes a window-edge bug (a granted reservation in the last milliseconds of a window refused its own request).
- 2026-10-14, step 2 (failed-auth throttling, KI-019) built: `apps/api/src/abb_api/auth/throttle.py` (three closed `Scope`s on the step 1 limiter: address, presented key id, global; fixed 60 s windows; 429 `AUTH_THROTTLED` with `Retry-After`; memory-only gate so a throttled request does no lookup and no counter write; bounded known-invalid and known-valid token caches; one capped `auth.throttled` audit row per workspace per window; fail open), `core/client_address.py` (`ABB_TRUST_PROXY` now applies to the API), wiring in `authz/dependencies.py`, ADR-153, settings `ABB_AUTH_FAIL_*`, OPERATIONS and SECURITY text, `scripts/bench_auth_throttle.py` and `docs/benchmarks/phase-19a-auth-throttle.md`. Deviations from D3, recorded in ADR-153: sign-in starts and callbacks are not moved onto the failure counters (not failure-shaped, no address or account at the API, F1), so KI-073 stays open; the audit row exists only where the failing key id belongs to a workspace (`audit_log` is per workspace). Benchmark found and fixed a gate that reopened ten times a second. New: KI-175, KI-176, KI-177. KI-019 resolved (issue closes with the close-out PR). Evidence: `tests/test_auth_throttle.py` (21 tests).


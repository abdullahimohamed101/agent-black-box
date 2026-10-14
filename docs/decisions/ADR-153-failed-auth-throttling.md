# ADR-153: Failed-authentication throttling on the shared counters

Status: Accepted (Phase 19a, step 2). Implements D3 and gate 2 of the Phase 19a plan. Uses the limiter of ADR-152.
Date: 2026-10-14
Phase: 19a

## Context

Every request with a bad API key cost a key lookup (and a hash comparison) and nothing limited how many a caller could send
(KI-019, S1). An anonymous flood therefore translated one-to-one into database reads, and a single key id could be guessed at
without limit. The throttle must bound that load without ever refusing a valid key, and without becoming the easiest way to deny
service to the people it protects.

## Decision

1. **Count failures only.** A request that authenticates never spends a budget. Three counters, each a closed `Scope` of the shared
   limiter (ADR-152), fixed windows (default 60 s), not escalating:
   * `auth_fail_address`: per client address, only when `ABB_TRUST_PROXY=1` and the address came from the trusted header (30 by default);
   * `auth_fail_credential`: per presented key id, whether or not such a key exists (10);
   * `auth_fail_global`: one ceiling for the whole installation (600), which is what bounds the database load of a distributed flood.
   Exceeding any of them answers `429 AUTH_THROTTLED` with `Retry-After` (the remaining part of the window) in the standard error envelope.
   The body is the same for an existing and an unknown key id and for every scope; it carries only the retry time.
   Settings: `ABB_AUTH_FAIL_ENABLED`, `_WINDOW_SECONDS`, `_PER_ADDRESS`, `_PER_CREDENTIAL`, `_GLOBAL`, `_NEGATIVE_SECONDS`,
   `_KNOWN_VALID_SECONDS`, `_CACHE_ENTRIES`; and `ABB_TRUST_PROXY` (API side; the web server already had it).
2. **A throttled request does no work.** The gate is memory only. When a failure uses up a budget the process remembers that the
   (scope, key) is closed until the window ends; later requests for it are refused before any key lookup and without a counter write
   (tested: zero lookups and zero counter calls across 50 throttled requests). The only computation is one SHA-256 of the presented
   token. Other processes learn about a closed window on their own next failure, which costs one counter write; so the fleet does at most
   `limit + N` lookups per scope per window.
3. **Valid traffic is not refused, with a stated exception.** Because a throttled request is refused before the lookup, the gate cannot
   by itself tell a valid key from a guess. Two bounded in-memory caches, keyed by a SHA-256 of the *whole* presented token, close that gap:
   * *known valid* (default 300 s): a token that authenticated in this process skips the gate. It still goes through full
     authentication on every request, so a key revoked a second ago stops working at once (tested). The cache decides only whether the gate is
     consulted. A guessed key id with a wrong secret has a different digest and gets no exemption;
   * *known invalid* (default 30 s): a token that failed is answered with the same 401 from memory, without a lookup, and still counts as
     a failure. Successes are never put here. A key created later has a different token, so nothing cached about earlier guesses can
     affect it (tested). Both caches are bounded (`ABB_AUTH_FAIL_CACHE_ENTRIES`, oldest evicted) and expire.
   The stated exception: while a window is closed for a key id, an address or the whole installation, a valid key that **this process
   has not authenticated in the last five minutes** (a brand-new key, a process that just restarted, a key used first on another process)
   is refused with `429` and `Retry-After` at most one window long; SDKs retry on 429. Established ingestion streams, whose keys are
   in the cache, are not touched. This is the price of not touching the database for refused requests and is recorded as KI-175.
4. **Which requests count.** Only a well-formed key (`abb_live_<id>.<secret>`) that fails authentication. A missing or malformed
   `Authorization` header costs no lookup, is not counted and is not gated, so a signed-out browser (which has no key) never meets a 429
   because of someone else's flood. Requests authenticated by session cookie never enter this path.
5. **Address handling.** The API had no notion of a client address (it sat behind the web relay, which strips `X-Forwarded-*`).
   Key traffic from SDKs reaches it directly, so `ABB_TRUST_PROXY=1` now applies to the API too, with the web server's rule: the *last*
   `X-Forwarded-For` entry is the one the trusted proxy appended, earlier entries are the client's; it must parse as an IP address.
   IPv6 is bucketed by /64 and IPv4-mapped addresses fold to IPv4, so rotating low bits does not evade. Without the setting the header is
   ignored entirely, so it can neither evade a bucket nor frame an address (tested). Without it the credential and global counters still
   apply; address throttling needs the proxy header, and the proxy's own IP limit is recommended defence in depth (OPERATIONS.md).
   The counter row stores a salted SHA-256 prefix of the address, never the address. That is a pseudonym, not anonymisation (an IPv4
   space can be enumerated), and it lives at most one window plus the 60 s grace before it is pruned; nothing logs it.
6. **Audit: one capped row, where there is a workspace.** The first time a budget closes in a window the process logs one warning
   with the scope names only. If the failing key id belongs to a workspace (a real key, wrong secret or revoked), an `auth.throttled` row is
   appended to *that workspace's* audit log by actor `system:auth-throttle`, outcome `denied`, resource the (public) key id, details
   the scope names and the window; at most one per workspace per window across all processes (a `limit = 1` shared counter, itself fail-closed:
   if the cap cannot be proven no row is written). `audit_log` is per workspace with a foreign key, and a flood of unknown key ids or
   addresses has no workspace; widening that table to system rows would be a tenancy change this step does not warrant, so those throttlings
   are visible in the log only. No credential, address or count is ever written.
7. **Fail open.** Counter trouble is the limiter's fail-open path (ADR-152): the local bucket for that scope, which is the same budget
   refilled evenly, still bounds a key id or an address, and the gate (memory only) needs no database. Any unexpected exception in the
   throttle is logged by class and the request continues to its normal 401. A database outage therefore cannot lock anyone out.
8. **Sign-in is deliberately not moved onto these counters.** A sign-in start is not a failure and has no credential; the callback's
   failures (bad state, bad token) cannot be attributed to an address (the API sees only the web relay) or an account (there is no
   password and the target is unknown until the provider answers), and a shared pool anonymous callers could drain is exactly the
   outage security review F1 removed. So starts keep the web server's per-address bucket and the API's global 6000/min ceiling (shared
   across processes since ADR-152), and callbacks and sessions are never refused. KI-073 therefore stays open, unchanged except that the
   ceiling is now counted across processes. We tested that a closed failed-auth window does not affect sign-in starts or callbacks.

## The throttle as a denial-of-service lever

Anyone who learns a key id (they appear in the UI and in the audit log, not in secrets) can close that key id's window by sending
ten bad secrets, for up to a minute. Consequences, stated plainly: a key in the process cache keeps ingesting; a key not in the cache
(first use, restart) waits at most one window. Anyone behind the same trusted address as an attacker (a shared NAT) shares the address
budget; an attacker who can close the global window delays first use of new keys installation-wide while it lasts, and bounded to the
window, never beyond it, with no escalation. People's sign-in, their sessions and read paths are never affected. The alternative that avoids
this (looking every key up regardless) is exactly the database load the throttle exists to cap.

## Database load bound

Per window, across N processes: key lookups at most `auth_fail_global + N` (plus the lookups of valid requests, unchanged), counter
writes at most roughly `3 x (that) / block` plus one refusal write per process per scope, and nothing at all for a refused request.
Measured with `scripts/bench_auth_throttle.py` (`docs/benchmarks/phase-19a-auth-throttle.md`).

## Alternatives considered

* **Escalating or sliding penalties.** More moving parts and a worse lock-out story; fixed windows are explainable.
* **Refusing nothing valid by always looking the key up.** Defeats the purpose (database reads per flood request).
* **A positive cache of key rows to verify secrets in memory.** Would let throttled requests be fully verified, but delays
  revocation by the cache lifetime. Chosen instead: the cache only exempts from the gate and authentication still runs.
* **Edge-only limiting.** Not available to a self-hosted install that has no edge; documented as defence in depth, not the only layer.
* **A system-audit table or nullable `audit_log.workspace_id`.** A tenancy-surface change for a log line; rejected for now.

## Consequences

* Positive: KI-019 closes; the flood bound is a setting and a number; valid ingestion is unaffected in the measured case.
* Negative: the cold-key exception (KI-175); a guessed key id can briefly close that key id's window; address throttling needs a
  correctly configured trusted proxy; sessions are not covered (a flood of bad session cookies still costs one indexed lookup each; KI-176).

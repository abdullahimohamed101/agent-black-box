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
3. **Valid traffic is not refused.** Because a throttled request is refused before the lookup, the gate cannot by itself tell a valid key from a guess.
   Three bounded in-memory helpers close that gap:
   * *known valid* (default 300 s, keyed by a SHA-256 of the *whole* presented token): a token that authenticated in this process skips the gate. It still goes
     through full authentication on every request, so a key revoked a second ago stops working at once (tested). The cache decides only whether the gate is
     consulted. A guessed key id with a wrong secret has a different digest and gets no exemption;
   * *active key ids* (review fix, resolves KI-175; `auth/active_keys.py`): a per-process set of the **public ids** of keys that are neither revoked nor expired,
     loaded by one bounded query at startup and refreshed in a background task every `ABB_AUTH_FAIL_ACTIVE_KEYS_REFRESH_SECONDS` (60), plus insertion when this process
     creates a key. While the *address* or *global* window is closed, a request whose key id is in the set is let through to full authentication; a key id that is not
     in it (random, revoked, expired, created elsewhere since the last refresh) is refused from memory with no lookup. The key id's **own credential window still
     applies**, so the lookups an attacker can cause while the global window is closed are at most `ABB_AUTH_FAIL_PER_CREDENTIAL` (10) per real key id per window (plus
     the in-flight and per-process slack of point 2): bounded by the installation's data, not by the attacker. The set holds ids only (no secret, hash or workspace) and is
     a hint about which requests may reach authentication, never a substitute for it. The load is off the request path, bounded by a 5 s timeout and `LIMIT cap + 1`
     (`ABB_AUTH_FAIL_ACTIVE_KEYS_CAP`, 100,000; above it the set holds an arbitrary subset, one warning is logged, and keys beyond it behave as the old exception did); a failed refresh keeps
     the previous set; until the first load succeeds the set is "not loaded", requests behave as before and a nudge makes the task retry (floor 2 s between attempts);
   * *known invalid* (default 30 s): a token that failed is answered with the same 401 from memory, without a lookup, and still counts as a failure. Successes are
     never put here. A key created later has a different token, so nothing cached about earlier guesses can affect it (tested). Both caches are bounded
     (`ABB_AUTH_FAIL_CACHE_ENTRIES`, oldest evicted) and expire.
   Small design details decided in the review fix: the refresh interval is 60 s and the load timeout 5 s (not settings of their own beyond the interval and cap); the set is
   re-merged with keys created in this process while a load ran, so a snapshot older than the commit cannot drop a new key; a revoked key stays in the set until the next refresh
   and is rejected by the normal lookup in between (a bad credential that trips a closed window is answered 429 rather than 401, as any counted failure is). The residual, stated plainly: a key
   created by the CLI or another process during a closed window is admitted after the next refresh (at most 60 s); and anyone who knows a key id can still close **that key id's**
   own window with ten bad secrets (next section).
4. **Which requests count.** Only a well-formed key (`abb_live_<id>.<secret>`) that fails authentication. A missing or malformed
   `Authorization` header costs no lookup, is not counted and is not gated, so a signed-out browser (which has no key) never meets a 429
   because of someone else's flood. Requests authenticated by session cookie never enter this path.
5. **Address handling.** The API had no notion of a client address (it sat behind the web relay, which strips `X-Forwarded-*`).
   Key traffic from SDKs reaches it directly, so `ABB_TRUST_PROXY=1` now applies to the API too, with the web server's rule: the *last*
   `X-Forwarded-For` entry is the one the trusted proxy appended, earlier entries are the client's; it must parse as an IP address.
   IPv6 is bucketed by /64 and IPv4-mapped addresses fold to IPv4, so rotating low bits does not evade. Without the setting the header is
   ignored entirely, so it can neither evade a bucket nor frame an address (tested). Without it the credential and global counters still
   apply; address throttling needs the proxy header, and the proxy's own IP limit is recommended defence in depth (OPERATIONS.md).
   The counter row stores a domain-separated SHA-256 prefix of the address (fixed prefix, no secret salt), never the address. That is a pseudonym, not anonymisation (an IPv4
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

Anyone who learns a key id (they appear in the UI and in the audit log, not in secrets) can close **that key id's own** credential window by sending ten bad secrets, for up
to a minute; during it that key id's valid requests that are not in the process cache get `429` (an SDK retries; a key in the cache keeps ingesting). This is the one remaining
form of the lever and is recorded as KI-191. It no longer extends to other keys: a flood that closes the global window or an address window does not lock out any other
valid key (point 3, active key ids). Anyone behind the same trusted address as an attacker (a shared NAT) shares the address budget but, for the same reason, is not locked out
if their key id is real. People's sign-in, their sessions and read paths are never affected. The alternative that avoids the per-key-id lever (looking every key up regardless)
is exactly the database load the throttle exists to cap.

## Database load bound

Per window, across N processes: key lookups at most `auth_fail_global + N` for unknown key ids and `auth_fail_per_credential` (+ in-flight) per real key id (plus the lookups of valid requests, unchanged), counter
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
* Negative: a guessed key id can briefly close that key id's window (KI-191); the active-ids refresh is one more small background task and query per process; address throttling needs a
  correctly configured trusted proxy; sessions are not covered (a flood of bad session cookies still costs one indexed lookup each; KI-176).

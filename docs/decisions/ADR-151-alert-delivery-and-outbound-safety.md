# ADR-151: Alert delivery, outbound safety, retries and noise limits

Status: Accepted (Phase 17, step 2). Implements D3 and the delivery half of D4 of the Phase 17 plan and gates 2, 3, 4, 5 and 7 of D6.
Builds on ADR-150 (the model, the sealed secrets, the `pending` notification rows), ADR-141 (the `server_secrets` mechanism) and the outbox of spec §72.
Date: 2026-10-14
Phase: 17

## Context

Step 1 writes a `pending` notification row per rule, destination and event. Step 2 sends them. The sender is an outbound-request cannon pointed wherever an owner says, carrying a secret the owner chose
and a body that must stay content-free, so the decisions below are about what it may reach, how often, how it fails and what it may ever write down.

## Decision

1. **`Notifier` is an internal contract with three in-repo implementations.** `Notifier.send(destination, alert) -> SendResult` (`alerts/notifiers.py`); `build_registry(settings)` maps `webhook`, `slack` and `email`
   to their implementation. No plug-in is loaded at runtime. A notifier receives an `OpenDestination` (the sealed columns opened for this send, never stored again) and an `Alert` (the stored content-free payload,
   the public `alert_id`, the attempt number, the web origin) and returns `SendResult(ok, error_code, retryable)`; it never raises for a delivery problem and never returns or logs what the target answered.
   Sealed columns are opened in exactly two places: the delivery tick and the send-test path (`delivery.open_destination`).
2. **Webhook.** One `POST` of the stored payload plus an absolute `link` (`ABB_WEB_ORIGIN` + `link_path`; no origin means `link: null`, never a guess). Headers: `X-ABB-Alert-Id` (the `alt_...` id receivers dedupe on),
   `X-ABB-Delivery-Attempt`, `X-ABB-Timestamp` (unix seconds) and `X-ABB-Signature: v1=<hex HMAC-SHA256(signing secret, timestamp "." body)>`. A receiver recomputes the MAC over the exact bytes it received and
   should reject timestamps outside a tolerance it chooses. **Slack**: a Slack-shaped `text` plus one `mrkdwn` section; `unfurl_*` off. **Email**: an operator SMTP relay (`ABB_SMTP_*`; the password only from the
   environment), STARTTLS by default and **fail-closed** (a relay that does not offer it receives nothing), authentication when configured, 10 s socket timeout, plain text plus a minimal HTML alternative, a
   `Message-ID` derived from the alert id and `X-ABB-Alert-Id`. The SMTP host is operator configuration, not owner input, so it is not subject to the SSRF guard; it runs in a worker thread bounded by a
   `wait_for` (a tarpit relay can hold that thread until its socket timeouts fire, which is the operator's own relay).
3. **Names are hostile text and each channel treats them so.** The only free text in a payload is the project, agent and rule name. `clean_text` NFKC-normalises (full-width `<` and `@` become ASCII) and drops
   control, format (bidirectional overrides, zero-width), surrogate and private-use characters, collapses whitespace to single spaces and caps the length at 80. Slack then escapes `& < >` (so a name cannot form a
   link, a channel or user mention or a special `<!...>` command) and removes `* ~ \` |`; email puts the cleaned name in the subject (one line; `email.message` additionally refuses a newline) and `html.escape`s it
   in the HTML part. Tests carry mentions, links, formatting, CR/LF, Unicode line separators, bidi and zero-width text, MIME encoded words and `<script>`.
4. **The outbound HTTPS sender is the SSRF defence** (`alerts/outbound.py`; the save-time guard of ADR-150 is only an early filter and uses the same `is_public_ip`). Per request: https only; the name is resolved **at
   delivery** through an injectable resolver; **every** returned address must be public (private, loopback, link-local including 169.254.169.254, CGNAT, multicast, reserved, IPv6 unique-local, and IPv4-mapped, 6to4, Teredo and
   NAT64 forms judged by the IPv4 they carry), otherwise `blocked_address` and no connection is made (one bad answer poisons the set); an IP-literal URL is judged the same way. The connection is made to the checked
   address (**pinned**) with the original hostname as TLS SNI and as the name the certificate must match, so the name is resolved once and a rebinding answer can never be used. **No redirect is followed** (`http_3xx`).
   One total deadline (`ABB_ALERTS_REQUEST_TIMEOUT_SECONDS`, 5 s) covers resolve, connect, TLS, write and the status line; the response is read only up to a complete status line within 16 KiB and the body is never read,
   stored or logged. Nothing logs a URL, header or response.
   *Why hand-written HTTP.* `httpx` (already a dependency) cannot pin the connection address while keeping SNI, `Host` and certificate verification on the hostname without a custom transport that reimplements
   the same connect step; the stdlib `asyncio.open_connection(ip, port, ssl=ctx, server_hostname=host)` does exactly this. The request is one `POST` with `Connection: close`, header names and values are rejected if
   they contain a control character, the path is percent-encoded, and the code is ~100 lines with a test matrix (IPv4, IPv6, mixed answers, rebinding, redirects, stalls, endless heads, wrong or untrusted
   certificates, header injection). **No new dependency**, so the five questions of the build prompt do not arise: the standard library and `cryptography` (already direct, ADR-150) cover everything. Proxy
   environment variables are not honoured (a proxy would resolve the name itself and defeat the pin).
   *Residual:* an owner can point a destination at any public host and port, so the server can be used for blind requests to public services at the rate limits below; the answer is never shown.
5. **Delivery is a job tick per workspace in the outbox framework.** `deliver_alerts` is enqueued, coalesced per workspace, in the transaction that writes a `pending` row; a tick that leaves rows waiting for a retry
   schedules a second job under another key (`<workspace>:retry`) at the earliest due time (an existing one is only ever moved earlier), so a retry delay never holds back a fresh alert. A tick claims at most
   `ABB_ALERTS_DELIVER_BATCH` (20) due rows with `FOR UPDATE SKIP LOCKED` (concurrent ticks never take the same row), asks for another tick if rows are still due, and runs sequentially inside the job's
   transaction (the lease heartbeat covers batch x request timeout). A worker that dies mid-tick rolls back and the rows are sent again: **at-least-once**, receivers dedupe on `alert_id`. `ABB_ALERTS_DELIVERY_ENABLED=false`
   is an operator hold: rows stay `pending` and are sent when it is lifted (tests use it by default so nothing leaves the process).
6. **Retries and the dead letter.** A retryable failure (`timeout`, `connect_failed`, `dns_failed`, `http_5xx`, HTTP 408, 425 and 429, an SMTP 4xx, `internal_error`) is retried with exponential backoff
   (30 s, 60 s, ... capped at 30 min, +-25 % jitter), up to `ABB_ALERTS_MAX_ATTEMPTS` (5), then the row is `dead` with its last fixed code. A failure no retry will fix (`blocked_address`, `tls_failed`,
   `http_3xx`, `http_4xx` other than the three above, `bad_response`, `config_unreadable`, an SMTP 5xx) is `dead` at once. The codes are a closed vocabulary; the history shows the code and the attempt count,
   never a body. The `failed` status exists in the table for compatibility with the CHECK but a row between attempts stays `pending` with `attempts > 0` and a future `next_attempt_at`.
7. **Retry-storm bound.** Per tick: at most the batch size claimed per workspace; after a destination fails once in a tick its other due rows are pushed out (backoff) without spending an attempt, so a down
   receiver gets one request per tick, not twenty. Over time: the attempt cap, the backoff and auto-pause (below) bound the total.
8. **Health.** Failed attempts add to `consecutive_failures`, a success resets it. `failing` from `ABB_ALERTS_FAILING_AFTER` (3), `paused` with `paused_reason = failures` from `ABB_ALERTS_PAUSE_AFTER_FAILURES` (10).
   The auto-pause is audited with the system actor (`actor_kind = system`, `alert-delivery`, action `alert.destination.auto_pause`, details `reason` and the count; no target). Rows of a paused destination
   are closed as `skipped (destination_paused)`; a person resumes it (which clears the count). A paused destination stays paused when an in-flight alert succeeds.
9. **Noise control at the destination: excess is skipped, not queued.** Per destination, `ABB_ALERTS_RATE_PER_MINUTE` (20) and `ABB_ALERTS_DAILY_CAP` (500) count *delivered* notifications; a due row beyond either is
   closed as `skipped` with `rate_limited` or `daily_cap` (migration 0096 adds the two reasons). Deferral was rejected: a queue that waits out a flood is an unbounded backlog and delivers stale alerts;
   dropping with a recorded reason keeps the table bounded and the history honest about what was not sent. The cooldown and the skipped-row cap of ADR-150 still apply upstream.
10. **Re-check at send time.** The destination must exist and not be paused and the rule must exist (a deleted one closes the row as `destination_deleted` or `rule_deleted`). A rule that was *muted* after the
    row was written still delivers it: the alert was already decided; the mute stops new ones.
11. **Send test** (`POST /v1/alerts/destinations/{id}/test`, `alert.write`: owner and admin; the write budget plus its own `ABB_ALERTS_TESTS_PER_MINUTE`, 10) sends a fixed `kind: test` payload (no project,
    agent or subject; `link_path` is the Alerts page) through the same registry and sender synchronously with the short timeout, after releasing the database connection, and returns `{ok, error_code}`. It **does not
    touch the failure counter or state and writes no history row**: an owner debugging a URL must not pause it, and a successful test must not hide a real outage by resetting the count. It is audited
    (`alert.destination.test`: type, ok, fixed code). A paused destination can be tested before it is resumed. Foreign and unknown ids are the same 404.
12. **Retention.** `ABB_ALERTS_RETENTION_DAYS` (30): an `alert_prune` job per workspace deletes at most `ABB_ALERTS_PRUNE_BATCH` (2000) `delivered`, `dead`, `skipped` or `failed` rows older than the window and
    never a `pending` row. It is armed when a notification row is written, runs every `ABB_ALERTS_PRUNE_INTERVAL_SECONDS` (6 h) while rows remain, continues after 5 s when a batch was full and ends when no row
    is left; like the sweep (KI-160) it is restarted by the next notification if the chain dies.

## What is not done

No push-style health notice (the in-app indicator is step 3; the API already returns `state`, `consecutive_failures` and `paused_reason`). No per-receiver adaptive limits. No webhook secret overlap on rotation (the
old secret stops signing at once). Email has no receiver-side dedupe beyond the `Message-ID`; duplicates are possible on a retry after a lost acknowledgement (at-least-once). Delivery holds a transaction
open across the network wait (bounded by batch x timeout); a send-heavy installation should run several workers. IPv6-only environments are untested. The retention job and the sweep share KI-160's
"no external watchdog" limitation.

## Consequences

Secrets are decrypted for the length of one request and appear in no log, audit row, history row or response. The hot ingestion path is untouched. A misconfigured or malicious target costs at most one bounded
request per attempt per row, at most 20 rows per tick, and the destination is paused after ten consecutive failures. The price of at-least-once with dedupe on the receiver is that a receiver which ignores
`alert_id` may see an alert twice.

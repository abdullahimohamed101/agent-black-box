# ADR-150: Alerting model, secret encryption and rule evaluation

Status: Accepted (Phase 17, step 1). Implements D2 and D4 of the Phase 17 plan and the model side of gates 4 to 8; delivery (D3) is step 2.
Builds on ADR-141 (the `server_secrets` mechanism), ADR-130/131 (findings), ADR-061/062 (authorization, audit) and the outbox of spec §72.
Date: 2026-10-13
Phase: 17

## Context

People who are not watching the dashboard must be told when something needs them, through a target they control, without the alert becoming a way to leak captured content,
to leak the target's secret, or to reach the internal network. This step decides the stored model, how its secrets are kept, who may see and change what, and how an
event becomes a notification row. Nothing is sent yet: rows are written with status `pending` for the delivery worker of step 2.

## Decision

1. **Four tables (migration 0095), every one workspace-scoped with composite keys.** `alert_destinations` (a named target of type `webhook`, `slack` or `email`; the target and a webhook's
   signing secret only as ciphertext; `masked_target`; `state` `active|paused|failing`; `consecutive_failures`; `paused_reason` `manual|failures`; created/updated by and at),
   `alert_rules` (one trigger, closed per-trigger `filters` JSON, `cooldown_seconds` 0..7 d, `enabled`, `last_fired_at`; `project_id` null means every project),
   `alert_rule_destinations` (many to many) and `alert_notifications` (one row per rule, destination and event; its `id` is the `alert_id` receivers dedupe on). Triggers, states, statuses, skip
   reasons and severities are CHECK constraints. Destinations and rules are **hard-deleted** by an owner or admin (audited); a deleted destination's waiting notifications are first closed as
   `skipped` (`destination_deleted`), a rule left with no destination is switched off, and the history rows stay with the reference cleared (`ON DELETE SET NULL (column)`, which clears one column
   and never `workspace_id`). The runtime role keeps full DML on all four: notifications are pruned by the retention job of step 2, and nothing else needs a privilege the role lacks.
2. **Secrets at rest: AES-256-GCM under a key held in `server_secrets`.** `alerts/sealed.py` seals a destination's config JSON (`{url}` or `{recipients}`) and the signing secret.
   The key material is a row named `alerts-encryption` in the append-only table of ADR-141, created on first use under an advisory lock in a transaction of its own and shared by every API process and worker
   through the database; the AES key is `HKDF-SHA256(material, info="abb-api/alerts/destination-secrets/v1")`. Each blob is `0x01 || key_id(8) || nonce(12) || ciphertext||tag`, so it names the `server_secrets`
   row that sealed it (the `key_id` column records the config blob's key for operators). The associated data is `(purpose, workspace_id, destination_id)`: a blob moved to another row, another workspace or the
   other purpose fails to open. The table's name pattern does not allow dots, hence `alerts-encryption`, not `alerts.encryption.v1`; the version lives in the HKDF `info` and the blob header.
   **Decryption happens only in the sender and the "send test" path (step 2)**; the API's modules write sealed bytes and never read them back. Responses carry `masked_target`, computed once at save; the signing
   secret is returned once, on create and on rotate, and appears in no log, audit row or later response. **Rotation:** insert a newer `alerts-encryption` row as the owner role (for example
   `INSERT INTO server_secrets (name, secret) VALUES ('alerts-encryption', decode('<64 hex characters from openssl rand -hex 32>', 'hex'))`) and restart the API and worker processes (they cache the current key);
   new and re-saved destinations are sealed under the new key, existing ones keep opening with their own; no re-encryption job is needed. A lost or truncated `server_secrets` row makes the destinations sealed under it
   unreadable (fail closed, `SealError`); a backup must include the table. (The rotation line in ADR-141 and migration 0094 uses `gen_random_bytes`, which needs the `pgcrypto` extension that the stock image does not load;
   the form above works everywhere.)
3. **Dependency: `cryptography`, made direct.** The five questions of the build prompt: (1) *Is it needed?* Authenticated encryption is required and the standard library has none; hand-rolling one is
   excluded. (2) *Is it already present?* Yes: `cryptography` 50.0.2 is locked as a transitive dependency of `pyjwt[crypto]` (the OIDC verification path), so it ships in the image already and
   adds no new wheel, no new supply-chain edge. (3) *Maintenance and trust?* It is the PyCA library behind most Python TLS and JWT stacks, with an active security process. (4) *Size and licence?*
   Already installed; Apache-2.0/BSD. (5) *Can it be avoided or isolated?* Only `alerts/sealed.py` imports it (AESGCM, HKDF). `pyproject.toml` now lists `cryptography>=44` directly, so removing `pyjwt[crypto]`
   later cannot drop it silently. Alternatives considered: Fernet (AES-128-CBC with HMAC, no associated data, so no row binding) and PyNaCl (a new native dependency).
4. **Early target guard, not the defence.** Saving a destination never resolves a name. `alerts/targets.py` refuses what is wrong on its face: not https, credentials in the URL, a fragment, a bad port, over-long input,
   an IP literal that is not globally routable (private, loopback, link-local including 169.254.169.254, CGNAT, multicast, IPv6 unique-local, IPv4-mapped/6to4/Teredo/NAT64 forms judged by the IPv4 they carry), forms some
   resolvers read as IPv4 (`127.1`, `0x7f000001`, `2130706433`), single-label names and the conventional internal suffixes (`.local`, `.internal`, `.localhost`, `.lan`, `.corp`, `.home.arpa` and similar).
   Refusals are `422 DESTINATION_TARGET_INVALID` with `details.reason` from a fixed list and never echo the value. A public-looking name can still point inward: the sender resolves at delivery, refuses non-public
   addresses and connects to the address it checked (step 2). Email recipients are 1 to 10 normalised addresses and need `ABB_SMTP_HOST` and `ABB_SMTP_FROM` (`422 EMAIL_NOT_CONFIGURED` otherwise); the SMTP password
   comes from the environment only.
5. **Authorization.** Three actions, none held by any API key scope: `alert.read` (rules, the preview, the history) for every role that reads runs (owner, admin, developer, viewer, security; not billing),
   `alert.write` (create, change, mute, delete, rotate, pause) and `alert.destination.read` (destinations, with masked targets and health) for owner and admin only. A rule shown to a role without
   `alert.destination.read` carries `destination_count` and an empty `destinations` list; a history row shows the destination's type but not its id or name; the `destination_id` history filter is the same
   403 as the destinations list; `approval_id` is withheld from roles without `approval.read`. A project, destination or rule id that is unknown or another workspace's is the same 404. Writes share a per-workspace
   budget (`ABB_ALERTS_WRITES_PER_MINUTE`, 30) and the preview has its own (20 a minute). Every change commits with its audit row in one transaction (`alert.destination.create|update|pause|resume|rotate_secret|delete`,
   `alert.rule.create|update|delete`); details are ids, the destination type, the trigger and the **names** of changed fields, never a URL, recipient, secret or free text. Counts per workspace are bounded (100 rules,
   50 destinations, 10 destinations per rule).
6. **The alert is content-free by construction.** `alerts/payload.py` builds the payload from a `Subject` (stored ids, names, statuses, severities, counts, amounts, times) and fixed strings: `alert_id`, `kind`,
   `severity`, a fixed `title` per trigger, `workspace_id`, the project (id, slug, name) and agent (slug, name), the rule (id, name), the subject ids, a closed `details` (run status, detector id, finding severity, `expires_at`,
   `seconds_to_expiry`, spend day, `spend_usd`, `threshold_usd`, period), a relative `link_path` and `created_at`. It never reads an event, an attribute, evidence, an error message or a run's name (a run is
   identified by its id). Names are metadata the product already shows every reader; the sender must still escape them for each channel (step 2). A test plants a sentinel in every path, command, host, argument, error
   message and run name and scans notification rows, jobs, audit rows and logs.
7. **Evaluation rides the outbox.** The producers only enqueue one small `evaluate_alerts` job keyed by (trigger, subject id), in their own transaction: `summarize_run` when the derived status is `FAILED`,
   `TIMED_OUT` or `BLOCKED`; the findings job for each security finding that is **new or worse than what was stored before the pass** (it reads the run's stored security severities first, so an unchanged finding
   rewritten by a rebuild enqueues nothing); the approval service when it creates an approval. No matching runs on those paths. The job loads the fact as it is *now* (a recovered run or a deleted finding alerts nobody; a
   *requested* approval is announced whatever state it has reached, see below), selects the workspace's enabled rules of that trigger, applies project, agent, minimum severity and run-status filters, locks each rule row, and inserts notification rows.
8. **Dedupe and cooldown.** The unique key is `(workspace, rule, destination, dedupe_key)` and every insert is `ON CONFLICT DO NOTHING`, so redelivered jobs, rebuilds and re-summaries add nothing. Keys: run
   `run_id:STATUS`; finding `finding_id:detector_version:severity` (a raised severity is a new alert, an unchanged finding after a rebuild is not); approval `approval_id:requested` and `approval_id:expiring`; spend
   `rule_id:YYYY-MM-DD` (UTC). **Cooldown is per rule and destination**: while a real (non-skipped) notification for that pair was created within the last `cooldown_seconds`, a new event is recorded as `skipped` with
   reason `cooldown`; a paused destination records `destination_paused`. A skipped event's key is spent (it does not fire later). Skipped rows are capped per pair and window (`ABB_ALERTS_SKIPPED_PER_WINDOW`, 25
   within the larger of the cooldown and one hour); beyond it a suppression is only a log line, so a flood cannot fill the table while the history stays honest about what happened. **The dedupe row is not the only guard** (review P1-1: `alert_prune` deletes rows after `ABB_ALERTS_RETENTION_DAYS`, and a cost rebuild re-summarises every run, so a pruned row alone would let every old failed
   run alert again): `summarize_run` asks for a `run_failed` evaluation only on the **transition into** a failed status (it reads the stored status before the derivation overwrites it), and the job ignores a run whose
   end (`completed_at`, else `started_at`) is older than the retention window. A run that fails again after recovering is a transition and is announced once per status as before. Severity of an alert: finding
   severity; run `FAILED`/`TIMED_OUT` `high`, `BLOCKED` `warning`; approval requested `info`, expiring `warning`; spend `warning`.
9. **Scheduled checks.** `alert_sweep` is one job per workspace that re-enqueues itself every `ABB_ALERTS_SWEEP_INTERVAL_SECONDS` (60) while the workspace has an enabled `approval_expiring` or `spend_over` rule,
   and is started by the API in the transaction that creates, edits or enables such a rule; it ends by itself when none is left. Each check runs in a savepoint and a failing check is logged by class only, so the
   job does not fail and break the chain; several workers are safe (one claims the job; writes are idempotent). *Approval about to expire*: pending approvals whose time to expiry is at most the rule's lead (60 s to
   24 h), at most 500 per sweep, dedupe `approval_id:expiring`. *Spend over*: today's UTC-day total of `cost_calculations.total` by the run's start day (the definition of the daily rollups, read live so a
   crossing is seen without the rollup's delay) for the rule's project and agent; fires when the total is **strictly greater** than the threshold, once per rule per day.
10. **Preview.** `GET /v1/alerts/rules/{id}/preview?days=` (1 to 30, default 7) returns counts per UTC day computed over stored findings, approvals, runs and daily spend, before cooldown, with a statement
    timeout (`ABB_ALERTS_PREVIEW_TIMEOUT_SECONDS`, 5; `504 ALERT_PREVIEW_TIMEOUT`). It delivers and stores nothing.

## What is not done

No delivery, retry, dead-letter, rate limit, daily cap, auto-pause, send-test endpoint, history retention or `Notifier` contract (step 2). No UI (step 3). No rate-based or anomaly alerts and no native PagerDuty or Teams (plan non-goals).
The scheduled chain has no external watchdog: a sweep job that exhausts its attempts on a database outage is dead-lettered and the chain restarts the next time a scheduled rule is written or the job is requeued (KI-160).
Rotation is manual SQL plus a restart; there is no key-age report. A per-workspace daily cap on notifications is part of delivery (step 2). Per-process limits apply to the write budgets (KI-161).

## Consequences

Secrets are unreadable in the database and in backups without the `server_secrets` row, and a database reader who has both can read them, as with the event-id key: protect database read access as you protect the runtime
credential. The hot paths pay one INSERT (or, in the findings job, one indexed SELECT plus an INSERT per new or worse security finding). History is honest about suppression without being unbounded.

## Amendment 1 (Phase 17 adversarial review)

- **Approval requested is a fact about the request (P2-3).** The first version loaded the approval only if it was still `REQUESTED` when the job ran, so a request decided within a second was announced or not
  depending on which came first. Now the `approval_requested` evaluation loads the row in any state and adds `details.approval_state` (the current `approvals.state`: `REQUESTED`, `APPROVED`, `DENIED`, `EXPIRED`
  or `CANCELLED`; a closed key in `DETAIL_KEYS`), so a receiver reads "requested, already approved". The `approval_expiring` alert still requires `REQUESTED`. The state in the details is the one the job saw, so it
  can still differ between runs of the same race; the alert's existence cannot.

# Runbook: alerting (Phase 17)

For operators of a deployment and for the owners and admins who set alerts up. Design: ADR-150 (rules, destinations, sealed secrets, evaluation), ADR-151 (delivery, outbound safety, noise limits and
the end-to-end test network), `docs/SECURITY.md` (alerting paragraphs), `docs/OPERATIONS.md` (settings table). End-to-end evidence: `scripts/alerts-e2e.sh` (`make alerts-e2e`), numbers in
`docs/benchmarks/phase-17-alerts-e2e.md`.

## What alerts do, and what they do not promise

An alert tells someone who is not watching the dashboard that **one checkable thing** needs them: an approval is waiting, an approval is about to expire, a security finding at or above a severity
appeared, a run failed, timed out or was blocked, or a project's (or agent's) spend for the UTC day went over a threshold. It carries ids, the project, agent and rule names, a severity, counts,
times and a link back to the app. **It never carries captured content**: no path, command, host, tool argument, error text or finding evidence. The link is where the viewer's role rules apply.

- **Best effort, at least once.** An alert can arrive twice (a worker that dies after sending and before recording sends again) and, if every attempt fails, not at all (a dead letter, visible in
  History). Receivers must dedupe on the alert id (`X-ABB-Alert-Id`, also `alert_id` in the body). This is a notification feature, not a paging system with guarantees; do not use it as the only
  control for something that must never be missed.
- **The fact is read when the job runs.** Evaluation happens a moment after the event, off the request path, and reads the run, approval or finding as it is *then*. A run that recovered or a finding that was
  deleted in that moment is not alerted. A requested approval is always announced, even when it was decided within a second; the alert's `details.approval_state` says what state it had reached when the job ran
  (`REQUESTED`, or already `APPROVED`, `DENIED`, `EXPIRED`, `CANCELLED`). An approval that has been decided is never reported as about to expire.
- **Once per event.** Each rule fires once per event and destination (a run per status, a finding per detector version and severity, an approval once as requested and once as about to expire, spend once
  per UTC day). Re-summarising, re-ingesting or rebuilding does not alert again while the history row exists, and a run that ended before the retention window (`ABB_ALERTS_RETENTION_DAYS`, 30 days) is never announced, so pruning history cannot make a rebuild re-alert old runs. A cooldown on the rule records further events as `skipped (cooldown)`; they are not sent later.
- **Limits protect the destination**: 20 deliveries a minute and 500 a day per destination by default (`ABB_ALERTS_RATE_PER_MINUTE`, `ABB_ALERTS_DAILY_CAP`); the excess is recorded as `skipped (rate_limited)`
  or `skipped (daily_cap)`, never queued. Up to 5 attempts with exponential backoff (30 s, 60 s, ... capped at 30 min), then `dead`. Two delivery ticks that overlap in time can each send up to one batch (5) past the minute or daily limit; the limits are protection against floods, not a quota. After 3 consecutive failed attempts a destination shows as failing and
  after 10 it is paused (an audit row by the system actor; a person resumes it).

## Setting it up

1. **Back up the key first.** Destination targets and webhook signing secrets are sealed (AES-256-GCM) under a key in the append-only `server_secrets` table (`alerts-encryption`, created on first use). The
   key lives in the database next to the ciphertext, so a database read gives both: protect read access as you protect the runtime credential. **Back the table up with the database**; without its row
   the sealed targets cannot be opened and delivery fails closed (`config_unreadable`).
2. **Email (optional).** Set `ABB_SMTP_HOST`, `ABB_SMTP_FROM` and, if the relay needs it, `ABB_SMTP_PORT` (587), `ABB_SMTP_USERNAME` and `ABB_SMTP_PASSWORD` in the API **and the worker** environment
   (the password only ever comes from the environment). STARTTLS is on by default and fails closed (a relay that does not offer it receives nothing); `ABB_SMTP_STARTTLS=false` is for a relay on a
   trusted local network only. Without `ABB_SMTP_HOST` an email destination cannot be created and the Alerts page says why. `ABB_WEB_ORIGIN` must be set or alerts carry no link.
3. **Destinations** (Alerts page, Destinations tab; owners and admins). A webhook or Slack incoming-webhook URL must be **HTTPS** and a public address; an email destination takes up to 10 recipients.
   Creating a webhook shows the **signing secret once** (copy it into the receiver now; it cannot be shown again, only rotated). Use **Send test**: it sends a fixed `test` alert through the same sender,
   changes nothing about the destination's health and is not listed in History.
4. **Rules** (Rules tab). One trigger, its filters (project, optional agent, minimum severity or run statuses, lead time before expiry, or the daily spend threshold), one or more destinations and an
   optional cooldown. After saving, the page shows how often the rule would have fired in the last 7 days. Mute a rule instead of deleting it to keep its history.

## Verifying a webhook

Each request is one JSON `POST` with `X-ABB-Alert-Id`, `X-ABB-Delivery-Attempt`, `X-ABB-Timestamp` (unix seconds) and `X-ABB-Signature: v1=<hex>`, where the value is `HMAC-SHA256(signing secret,
"<timestamp>.<raw body>")`. Recompute it over the **exact bytes received**, compare in constant time, reject timestamps outside a window you choose (five minutes is typical) and ignore an alert id you
have already handled:

```python
mac = hmac.new(secret.encode(), timestamp.encode() + b"." + raw_body, hashlib.sha256).hexdigest()
ok = hmac.compare_digest(signature, f"v1={mac}") and abs(time.time() - int(timestamp)) <= 300
```

A Slack destination needs no secret (the incoming-webhook URL is the secret). The body is a Slack message with `text` and one `mrkdwn` section; names are escaped so they cannot become mentions or links.

## Rotating secrets

- **A signing secret** (suspected leak, or a person left): Destinations, Rotate secret (or `PATCH {"rotate_signing_secret": true}`). The new secret is shown once; **the old one stops signing at once**
  (there is no overlap window), so update the receiver immediately; alerts delivered between the two moments fail verification at the receiver.
- **A webhook or Slack URL**: replace the destination's target (`PATCH` with `target`; the stored URL is never returned, so supply it again) or delete and re-create it. Revoke the old URL at the source.
- **The encryption key** (a suspected database read): follow `docs/OPERATIONS.md`, "Alert secrets and their key": insert a new `alerts-encryption` row as the database owner, restart the API and worker,
  then re-save each target and rotate each signing secret so nothing remains under the old key. `SELECT id, name, key_id FROM alert_destinations` shows which key sealed each destination.

## Reading the delivery log and dead letters

Alerts page, History tab (or `GET /v1/alerts/notifications`; owners and admins also filter by destination). Status `pending` with attempts above zero is waiting for a retry; `delivered`; `skipped` with
a reason (`cooldown`, `destination_paused`, `destination_deleted`, `rule_deleted`, `rate_limited`, `daily_cap`); `dead`. The fixed `error_code` tells what to fix; the response body is never stored.

| error_code | what it means | what to do |
| --- | --- | --- |
| `timeout`, `connect_failed`, `dns_failed`, `http_5xx` | the receiver is down or slow | retried automatically; fix the receiver |
| `http_4xx` | the receiver rejected it (revoked hook, wrong path); 408, 425, 429 are retried | fix or replace the URL, then Send test |
| `http_3xx` | the receiver redirects; **redirects are never followed** | put the final URL in the destination |
| `blocked_address` | the name resolved to a private, loopback, link-local, metadata or otherwise non-public address, at delivery | the target must resolve publicly; never retried |
| `tls_failed` | the certificate does not verify for that name against the system trust store | fix the receiver's certificate (private CAs are not supported) |
| `smtp_failed` | the relay refused or was unreachable (4xx retried) | check `ABB_SMTP_*` and the relay |
| `config_unreadable` | the sealed target cannot be opened (the key row is missing or changed) | restore `server_secrets`, or re-save the target |

SQL for an operator: `SELECT status, last_error_code, count(*) FROM alert_notifications WHERE created_at > now() - interval '1 day' GROUP BY 1, 2;` and
`SELECT name, state, consecutive_failures, paused_reason FROM alert_destinations WHERE state <> 'active';`. A dead alert is not retried again: fix the cause, use Send test, and the next event alerts normally.
History is pruned after `ABB_ALERTS_RETENTION_DAYS` (30).

## Pausing, resuming and holding delivery

- **A destination**: Pause on the Destinations tab (`PATCH state: paused`); alerts for it are recorded as `skipped (destination_paused)` and are not sent later. Resume clears the failure count.
- **A rule**: mute it (`enabled: false`); alerts already decided still deliver.
- **Everything** (maintenance, an incident at the receivers): `ABB_ALERTS_DELIVERY_ENABLED=false` on the worker holds delivery; rows stay `pending` and are sent when it is switched back on and the next alert
  of that workspace queues a tick. Pending rows are never pruned, but an alert sent after a long hold is stale: hold for hours, not weeks.

## When something is wrong

- **Nothing arrives.** Run Send test. Then History: `skipped (destination_paused)` means auto-pause (look for the audit row `alert.destination.auto_pause`); `rate_limited`/`daily_cap` means the limits
  held; no row at all means no rule matched (check the project, agent and severity filters, and that the rule is on) or the worker is not running (`deliver_alerts` and `evaluate_alerts` are outbox jobs;
  see `docs/runbooks/worker-lag.md`). The scheduled triggers (about to expire, spend) are checked every `ABB_ALERTS_SWEEP_INTERVAL_SECONDS` (60) by a chain that restarts when a scheduled rule is saved.
- **A change answers "Alerts are being sent right now" (`409 ALERT_BUSY`).** A delivery tick is holding the rows the change touches (deleting a destination or rule that has waiting alerts); nothing was changed. Repeat it in a few seconds; a tick lasts at most about 25 s (50 s if every alert is an email that times out).
- **A flood.** Per destination the limits above hold; the history shows what was dropped. Mute the rule, fix the cause, resume.
- **A suspected misuse of the sender.** Anyone who can create destinations can aim the server at any **public** host and port, rate limited; private, loopback and link-local addresses, plain HTTP and
  redirects are refused. Restrict who is an owner or admin; every destination change is in the audit log (`alert.destination.*`) with the actor and never the target.

## The end-to-end suite

`make alerts-e2e` boots the real stack and local receivers and prints PASS/FAIL per check (see `docs/TESTING.md`). It sets `ABB_ALERTS_TEST_NETWORK_FILE`, which **only** exists for it: never set it
on a deployment (it is refused unless `ABB_ENVIRONMENT` is `development` or `test`).

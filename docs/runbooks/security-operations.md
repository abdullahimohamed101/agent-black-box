# Runbook: security operations

Spec 126 lists six things that need operational monitoring: unusual API-key usage, repeated cross-tenant authorization failures, policy bypass attempts, high-rate secret detections,
repeated approval failures and administrative role changes. This page says, for each, what shows it **today**, how to look, and what is **not** monitored. Read the gaps as real: where it
says "no alert" there is no alert. The gaps are filed as KI-242.

Two facts shape everything below. Metrics never carry a tenant, project, user or key as a label (that is a design rule, ADR-165), so a per-tenant or per-key question cannot be answered
from a metric. And the audit log (Settings, Audit log; table `audit_log`) is the product's record of who did what: it is append-only through the product's APIs, holds counts and ids and never
content, and is the place to answer a per-actor question.

Dashboard row for the aggregate signals: **Controls: limiter, throttles, quotas, body cap**. Alerts that exist: `AbbAuthThrottleBlocks`, `AbbSessionThrottleBlocks`, `AbbPrivilegedDatabaseRole`.

## Privileged database role

`db_role_privileged{reason}` is 1 while the connected role could rewrite events (`events_rewritable`, `events_owner`), bypass row security (`bypass_rls`), is a superuser, or can create schema
objects (`schema_create`). The API and worker re-check every `ABB_ROLE_CHECK_INTERVAL_SECONDS`. Alert `AbbPrivilegedDatabaseRole` pages after 5 minutes. In production the API also reports `role`
as failing on `/readyz`.

1. Read the reason: it names the privilege class, never a role name. The log line from the role check has the same fixed reasons.
2. Was a grant made by hand after the deploy? `\du+` and `\dp events` as the owner. Revoke it; the next check, within the interval, clears the gauge and restores readiness.
3. Was the process started with the owner's URL? Compose and the runbooks use `abb_runtime` for the API and worker (OPERATIONS, "Database roles and the startup check"). Restart with the runtime URL.
4. The retention process and the CLIs are not checked (KI-231); do not give their credentials to the API.

## Unusual API-key usage

Shows today:

- `auth.throttled` audit rows (one per window per workspace) and `auth_throttle_blocks_total{kind}`: someone is presenting **wrong** keys at a rate. `AbbAuthThrottleBlocks` fires at 50 in 10 minutes.
- `api_key.create` and `api_key.revoke` audit rows with the actor: who made or ended a key.
- `http_requests_total{status="401"}` in aggregate.
- The request log line carries `actor_id` (`key:...`), `workspace_id`, `project_id`, so a suspected key can be followed in logs by id.

**Not monitored:** a **valid** key used in an unusual way: from a new place, at a new hour, at ten times its normal volume, or against routes it never used. There is no baseline per key, no
last-used alert, and the address is not logged. A leaked key that behaves like the real client is invisible. Mitigation today: revoke on suspicion (Settings, API keys), keep keys per project, rotate
on staff changes ([secrets](secrets.md)).

## Repeated cross-tenant authorization failures

Shows today: a request for another workspace's or project's resource is answered `404` (not `403`) so that existence is not revealed, and `403 PERMISSION_DENIED` when the role lacks a permission. Both
appear in `http_requests_total` by `route` and `status`, aggregate only. A key presented for the wrong project gets `403 PROJECT_KEY_REQUIRED` or `INSUFFICIENT_SCOPE`.

**Not monitored:** a counter that separates a cross-tenant probe from an ordinary missing resource, and any per-actor count. A person or key walking other tenants' ids looks like a stream of 404s in the
log (`actor_id`, `route`, `status`) with nothing aggregating them. To investigate: search the request log by `actor_id` for a burst of 403 and 404 across many `workspace_id` values. No alert exists.

## Policy bypass attempts

Shows today: the server writes `policy.decision`, `policy.action.blocked` and `approval.*` events itself and refuses those event types from an ingestion key (KI-143), so an agent cannot forge a
decision. A run where the SDK could not ask the policy service carries a `policy.warning` (fail-open warning) on its timeline; `policy.publish` is in the audit log.

**Not monitored:** an agent that **skips** the policy check entirely (an SDK without enforcement, an unprotected tool) produces no event at all, so there is nothing to count: absence is
not visible. No metric counts blocked actions, fail-open warnings or denials, and no alert exists. The policy decision availability SLI (spec 117) has no metric either (KI-242).

## High-rate secret detections

Shows today: the server scans every string of every event and artifact before storing (ADR-130); detections become `sec.*` findings on the run (for example `sec.credential_in_output`,
`sec.secret_file_read`), visible on the run and in the findings list per project. `security.settings.update` audit rows record mutes and allowlist changes.

**Not monitored:** the **rate** of detections across the installation. There is no counter of detections or redactions, so a sudden jump (a prompt change that makes an agent print secrets, an
integration leaking tokens) is not alerted; you find it by looking at the findings list. A muted detector (by a project admin) hides its findings; the audit row is the only trace.

## Repeated approval failures

Shows today: `approval.consume_refused`, `approval.cancel`, `approval.decide` and `approval.expire` audit rows (actor, approval id, reason class; no content). A run waiting on approval shows
`waiting`; an approval that was never decided becomes expired by itself.

**Not monitored:** no counter of refused consumes, expiries or denials, so a key repeatedly trying to spend an approval it does not own, or an approval queue that nobody answers, raises no alert.
The audit query is the check:

```sql
SELECT date_trunc('hour', occurred_at) AS hour, action, count(*) FROM audit_log
WHERE action IN ('approval.consume_refused', 'approval.expire') AND occurred_at > now() - interval '1 day'
GROUP BY 1, 2 ORDER BY 1 DESC;
```

## Administrative role changes

Shows today: `member.add`, `member.update`, `member.remove`, `invitation.create`, `invitation.revoke`, `invitation.revoke_inviter_lost_rights`, `session.revoke`, `session.revoke_others` and the
CLI recovery rows (`actor_kind=cli`, [auth and access](auth-and-access.md)) are audit rows with the actor and the role transition. `invitation_only_refusals_total` counts sign-ins refused
because the person was not invited.

**Not monitored:** no alert on a new owner or admin, on many role changes in a short time, or on a CLI recovery. To review: Settings, Audit log, filter on `member.*`, or
`SELECT occurred_at, actor_kind, actor_id, action, details FROM audit_log WHERE action LIKE 'member.%' ORDER BY id DESC LIMIT 50;`.

## When to start the incident process

Any of: a privileged-role page that was not a known grant; a key you did not create; an owner you did not add; findings showing credentials in agent output across several projects at once. Then follow
the incident steps in the spec (section 124), revoke the credential first, and keep the audit rows (they are the record; see OPERATIONS "Data retention" for how long they are kept).

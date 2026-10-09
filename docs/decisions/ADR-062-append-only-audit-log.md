# ADR-062: Append-Only, Bounded Workspace Audit Log

Status: Accepted
Date: 2026-10-09
Phase: 15

## Context
Spec §91 and §124 expect administrative actions to be auditable; INV-1 and the `events` table show how to make a table immutable for the
runtime role. An append-only table the runtime cannot purge must not be a write path any member can grow without limit.

## Decision
- `audit_log (workspace_id, id)` records every mutating administrative action (API, UI and CLI) and, bounded, denied requests by people.
  Columns: `actor_kind` (`user`|`api_key`|`cli`), `actor_id`, `action`, `resource_kind`, `resource_id`, `outcome` (`allowed`|`denied`),
  `details` (a JSON object of names, roles and scopes, never a secret, a token, a cookie or an email; validated by `clean_details`;
  `CHECK (pg_column_size(details) < 8192)`), `request_id`, `occurred_at`. `id` is an identity column so the newest-first cursor needs no tie-break.
- The runtime role has SELECT and INSERT only (the same mechanism as `events`). Migration `0047` exports `APPEND_ONLY_TABLES` and
  `test_runtime_role.py` loads it and asserts no UPDATE/DELETE/TRUNCATE on those tables and full DML on every other one.
- Denials by people pass a per-actor token bucket (`ABB_AUDIT_DENIALS_PER_MINUTE`, 10) held in memory with a bounded number of actors; beyond it
  the denial is a log line only. API-key denials are logged, not audited. A denial row stores the HTTP method and the route template only.
- The audit insert runs in its own short transaction after the request's; a failed audit write is logged and never changes the response.
- `GET /v1/audit` (`audit.read`: OWNER, ADMIN, SECURITY) pages newest first with a keyset cursor and an optional `since`. Reads are not audited.
  Sign-in and sign-out are person-level and go to structured logs.
- CLI actions write rows with `actor_kind=cli`, `actor_id=cli:<os user>`; `relink-user` and `create-session` write one row per workspace the person
  belongs to (the log is per workspace); rows carry the user's public id, never an email.

## Alternatives
Reusing `events` (telemetry, not administration; different tenancy and retention). Logs only (not queryable per workspace, not tamper-evident).
Auditing every request or every denial (volume without value; disk fill by any member). Coalescing denials with a counter (needs UPDATE on an
append-only table).

## Consequences
Administrative actions are attributable and immutable to the runtime role; a flood of denials is visible in logs and capped in the table; the
log's retention is Phase 19's privileged owner job (runbook `auth-and-access.md`); a future "who viewed what" requirement needs an explicit
decision because reads are not recorded. The audit insert's own failure is invisible to the caller by design (it is logged).

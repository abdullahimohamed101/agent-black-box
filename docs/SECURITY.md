# Security

Agent Black Box stores source code, prompts, shell output, file names, database query text and
tool arguments. Treat it as a high-value target and all captured telemetry as hostile input.
Source: spec §32, §38, §90-93, §126; threat model to be expanded as phases land.

## Assets and boundaries
Customer prompts/responses, diffs, terminal output, API keys, tool arguments, policy config,
approval authority, trace history, user identity. Boundaries: customer agent process | internet |
ingestion edge | control plane | database/object storage | human approvers.

## Requirements (enforced by tests where stated)
- API keys (implemented, Phase 2): `abb_live_<key_id>.<secret>`; `key_id` is public and indexed, `secret` is 32 random
  bytes shown once; only `sha256(secret)` is stored and compared in constant time (a dummy comparison runs for unknown
  keys). Scopes `events:write`, `runs:read`, `artifacts:write`, `policy:check` (database CHECK constraint); optional expiry;
  revocation; `last_used_at` written at most once a minute. A project-bound key reads and writes its project only; a
  workspace-wide key (no project) can read across projects but cannot ingest. Unknown, malformed, wrong-secret, revoked and
  expired keys all produce the same 401 (`API_KEY_INVALID`); a valid key missing a scope gets 403 with the required scope.
  Keys are issued only by the CLI (`python -m abb_api.cli create-key`); the secret goes to stdout once and is never logged.
  `make seed` writes a dev key to the gitignored, owner-only `.local/dev-api-key` and refuses to run when
  `ABB_ENVIRONMENT=production`.
- Tenancy (implemented, Phase 2): every tenant table is keyed `(workspace_id, id)` with composite foreign keys, so a row
  cannot reference another tenant's parent (database-enforced, tested); repositories are built with a tenant and no method
  takes a workspace argument; other tenants' and other projects' resources are indistinguishable from nonexistent (404, same
  body). Cross-workspace and cross-project tests cover every read route. Optional Postgres RLS remains a later defence in depth (§73.4).
- Redaction: SDK-side before export, server-side backstop, payload modes FULL/METADATA_ONLY/DISABLED. (Phases 3, 13)
- Never log secrets or payload bodies (implemented): request logs carry method, path, status, duration, request id and the
  safe identifiers `workspace_id`, `project_id`, `key_id`; a test posts secret-looking keys and payloads and asserts none
  appear in any log record. The reason a key was rejected is logged server-side only.
- Request hardening (implemented): body size limits are enforced while streaming for the compressed and the decompressed
  bytes (a 100 MB gzip bomb is rejected without being expanded, tested by peak memory); only gzip/identity encodings and
  `application/json` are accepted; validation errors never echo submitted values; NUL characters and lone surrogates are rejected
  at the boundary (PostgreSQL cannot store them); unhandled errors return a generic 500 without internals.
- Response headers (implemented): every response carries `X-Content-Type-Options: nosniff` and `Cache-Control: no-store`; full secure-header policy is Phase 19.
- Denial of service (partly implemented): per-project token buckets (per process), bounded batches, bounded cursors and page
  sizes, bounded metadata; a shared limiter and quotas are Phase 19.
- Rendering: payloads displayed as text; sanitize any markup; no `dangerouslySetInnerHTML` on trace data.
- Approvals bound to the exact action hash, single use, atomic consume. (Phase 14)
- Rate limits, size limits, bounded queues against telemetry flooding. (Phases 2, 19)
- Dependencies: lockfiles, scheduled vulnerability scans, minimal SDK deps.
- Database roles (KI-020, INV-1): migrations run as the database owner; the API, worker and CLI run as `abb_runtime`
  (migration `0007`), which has SELECT/INSERT on `events` and no UPDATE, DELETE, TRUNCATE or DDL anywhere. Enforced by
  `apps/api/tests/test_runtime_role.py`, and the whole API test suite runs as a role inheriting it. The deletion back door is closed too (migration 0008): `events` references `runs` with RESTRICT and the role cannot DELETE runs, agents, projects or workspaces, so events cannot vanish as a cascade. Retention (Phase 19) must be a privileged owner job. Caveats: a role with CREATEROLE/superuser or the owner can still alter privileges; `ALTER ROLE ... PASSWORD` is sent by `alembic upgrade` (set `log_statement` below `ddl` or keep migrations out of statement logs); Alembic only sets the password on an upgrade that ends at head. A superuser or the owner can still rewrite
  history: protect those credentials, and use `ABB_MIGRATION_DATABASE_URL` for the owner connection in deployments. Set
  `ABB_RUNTIME_DB_PASSWORD` for `alembic upgrade` to give the role a login; without it the role cannot log in.
- Local/demo credentials are generated, documented as dev-only, never real.

## Known gaps (tracked in KNOWN_ISSUES.md)

No dashboard users or RBAC yet (Phase 15): the read API is reached with a `runs:read` API key. No API key management
endpoints (CLI only). Rate limits are per process. No audit log of administrative actions yet (CLI actions are not
recorded in the database). The Python SDK redacts on the client (Phase 3: key rules, best-effort secret patterns, callback, payload modes; default drops payloads);
server-side redaction arrives in Phase 13, so **other clients are responsible for not sending secrets**, and inline payloads are stored as received.

## Process
Security-sensitive changes require a `review-change` pass and a note in the phase plan.
Incidents: severity scale and flow in spec §124; cross-tenant exposure is SEV-1.

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
  Keys are issued by the CLI (`python -m abb_api.cli create-key`) or, since Phase 15, by `POST /v1/api-keys` for people holding
  `api_key.create` (the token is returned once with `Cache-Control: no-store`, only its hash is stored, a person can mint only keys
  whose non-ingestion actions they hold themselves, ingestion keys need a project); the secret is never logged.
  `make seed` writes a dev key to the gitignored, owner-only `.local/dev-api-key` and refuses to run when
  `ABB_ENVIRONMENT=production`.
- Tenancy (implemented, Phase 2): every tenant table is keyed `(workspace_id, id)` with composite foreign keys, so a row
  cannot reference another tenant's parent (database-enforced, tested); repositories are built with a tenant and no method
  takes a workspace argument; other tenants' and other projects' resources are indistinguishable from nonexistent (404, same
  body). Cross-workspace and cross-project tests cover every read route. Optional Postgres RLS remains a later defence in depth (§73.4).
- Redaction (SDK Phase 3, server Phase 13 step 1, ADR-130): the SDK redacts before export; the server redacts again **before an event or artifact is
  stored** (accepted events are immutable, so this is the only moment it can). Per-project capture mode, enforced on the server with the SDK's names:
  `FULL` (payloads kept after redaction), `METADATA_ONLY` (default for a project with no settings: `payload`, `payload_ref` and artifact references
  dropped even if the SDK sends them), `DISABLED` (also no `error.message` or `metadata.*`). The scanner has the SDK's kinds with patterns bounded
  for hostile input (no backtracking blow-up; 64 KiB windows; a size and a time budget), never logs or returns a value, and is idempotent. Fail
  closed: a value that cannot be scanned is dropped with `redaction.dropped_reason` (`scan_failed`, `too_large`); an artifact over its budget is
  stored as a one-line note; unreadable settings refuse the batch (503). `redaction.server_count`, `redaction.server_kinds` and
  `redaction.dropped_reason` are metadata for every role. Settings changes (`security.settings.update`) are audit-logged in the same transaction.
  Tests plant secrets and scan every table and artifact file for them (`tests/test_ingestion_redaction.py`, `tests/secret_scan_helpers.py`).
  Artifact names are scanned too (found by the end-to-end gate). The backstop gate passed (`make security-e2e`): a client that skips redaction sends an AWS-style
  key and a PEM block in a command, a payload, an error message and an artifact, and a scan of every text column of the database, the artifact directory and the logs finds
  them nowhere. KI-032 and KI-042 are resolved. **Not done:** stored events and artifacts are never scrubbed retroactively; unknown-shape secrets still pass in FULL mode
  (inherent to pattern detection); a credential that an SDK already redacted into an artifact is not a finding (KI-134).
- Security findings (Phase 13 step 2, ADR-131): six deterministic, versioned detectors (`sec.secret_file_read`, `sec.credential_in_output`,
  `sec.destructive_command`, `sec.risky_git`, `sec.privileged_db`, `sec.unexpected_destination`) report that a rule matched, never what it matched
  on. A finding has a category, a fixed sentence, a severity, a count and event links; no path, command, host or secret is ever interpolated, and
  evidence keys are allowlisted, so a viewer sees exactly what an owner sees and learns nothing the category does not say (tests plant sentinels in every
  channel). Detection runs over the full events whatever the reader's role; the evidence link opens the event, where `payload.read` still decides what is
  shown. Findings are not searchable and no facet mentions them (ADR-101). `GET /v1/security/findings` is `run.read` (BILLING, which cannot read runs, gets
  403); the per-project settings (capture mode, mutes, destination allowlist) need `project.write` and are audit-logged. Patterns miss obfuscated or indirect
  commands (KI-132), and an unconfigured destination allowlist means no destination findings by design. A mute, an unmute or an allowlist change queues the
  project's recent runs for re-analysis in the same transaction as its audit row (bounded, newest first, coalesced; ADR-131 Amendment 1, KI-133 resolved).
  Operator notes: `docs/runbooks/security-observability.md`.
- Never log secrets or payload bodies (implemented): request logs carry method, path, status, duration, request id and the
  safe identifiers `workspace_id`, `project_id`, `key_id`; a test posts secret-looking keys and payloads and asserts none
  appear in any log record. The reason a key was rejected is logged server-side only.
- Request hardening (implemented): body size limits are enforced while streaming for the compressed and the decompressed
  bytes (a 100 MB gzip bomb is rejected without being expanded, tested by peak memory); only gzip/identity encodings and
  `application/json` are accepted; validation errors never echo submitted values; NUL characters and lone surrogates are rejected
  at the boundary (PostgreSQL cannot store them); unhandled errors return a generic 500 without internals.
- Response headers (implemented): every response carries `X-Content-Type-Options: nosniff` and `Cache-Control: no-store`; the full secure-header policy (strict CSP, frame denial, referrer policy, HSTS on https) landed in Phase 19a (ADR-155).
- Denial of service (partly implemented): per-project token buckets in front of a PostgreSQL-backed shared limiter (Phase 19a step 1, ADR-152: the
  ingestion, artifact, sign-in-start, policy, approval-decision and alert budgets hold across API processes; it fails open to the per-process
  bucket when the database cannot answer, so a database outage is also a limiter outage), bounded batches, bounded cursors and page sizes,
  bounded metadata, and per-project quotas that refuse loudly (Phase 19a step 3, ADR-154: distinct agents, runs and events per UTC day, total artifact bytes). These are abuse rails, not a WAF: put a reverse proxy with
  address limits in front of any exposed deployment.
- Live streams (implemented, Phase 5): the stream endpoint uses the same authentication, `runs:read` scope and tenant/project visibility as
  the other reads (another tenant's or project's run is a 404, tested over a real socket). Payloads are never streamed, only `has_payload`.
  The `NOTIFY` that wakes streams carries ids only and is never trusted for content: streams re-read rows under their own tenant context.
  Resources are bounded per process (50 streams, 10 per key, 15 min lifetime, 10 s write timeout) so one visitor cannot hold the pool
  (a stream borrows a connection per poll, never while idle). Since Phase 15 an open stream re-checks its credential every
  `ABB_STREAM_REAUTH_SECONDS` (30): a revoked or expired key, a revoked session or a removed or demoted member ends it with `STREAM_UNAUTHORIZED`
  (KI-033, tested over a real socket and in the browser); the cap of 10 is per person (`user:<id>`) or key, not per web server.
- Rendering: payloads displayed as text; sanitize any markup; no `dangerouslySetInnerHTML` on trace data.
- Approvals (implemented, Phase 14 step 2, ADR-141): an approval authorizes one intent, once. The intent hash is SHA-256 over a canonical, domain-separated JSON of
  workspace, project, run, agent, capability, resource, risk class and arguments as the SDK sent them (NFC, sorted, integers only; golden vectors shared with the SDK), recomputed from the presented
  intent at consume, compared in constant time and again in the `UPDATE`; it is never returned, logged or put in an event. Only scrubbed forms are stored or shown, and content that cannot be
  scanned is dropped, not stored. The state machine (`REQUESTED` to `APPROVED`/`DENIED`/`EXPIRED`/`CANCELLED`; an unspent `APPROVED` may be spent once or expire) is enforced by CHECKs and a trigger as
  well as by single-statement transitions, so races have one winner and the application role cannot rewrite, un-spend, delete or truncate an approval; the trigger guards integrity, not
  authorization (a holder of the runtime credential can make a legal transition by hand: each is an event and an audit row). Consume re-evaluates the current policy (a deny published after the grant refuses the spend, `APPROVAL_POLICY_DENIED`, under a shared policy-scope lock), and ingestion rejects the event types the server writes (`RESERVED_EVENT_TYPE`), so a client cannot forge a decision or an approval event (ADR-141 Amendment 1). Only a signed-in person whose role satisfies the rule's
  `approver_role` and who holds `payload.read` decides; no API key can; the person whose key made the request cannot decide it unless the rule allows. Approvals are bound to the asking key, project
  and workspace; every other id is one 404. The server writes the decision events and reserves its emitter name. Decisions, waits and open requests are rate-limited and capped. Tests attack replay
  (other command/resource/argument/agent/run/risk class, second use, expiry, before approval, cross-workspace), 50-way races, direct SQL as the runtime role, role/self-approval matrices, content leaks and floods
  (`apps/api/tests/approvals/`, `tests/authz/`). Known limits: KI-142 to KI-147.
- Rate limits, size limits, bounded queues against telemetry flooding. (Phases 2, 19)
- Dependencies: lockfiles, scheduled vulnerability scans, minimal SDK deps.
- Database roles (KI-020, INV-1): migrations run as the database owner; the API, worker and CLI run as `abb_runtime`
  (migration `0007`), which has SELECT/INSERT on `events` and no UPDATE, DELETE, TRUNCATE or DDL anywhere. Enforced by
  `apps/api/tests/test_runtime_role.py`, and the whole API test suite runs as a role inheriting it. The deletion back door is closed too (migration 0008): `events` references `runs` with RESTRICT and the role cannot DELETE runs, agents, projects or workspaces, so events cannot vanish as a cascade. Retention is the one deletion path: the `abb_retention` role (migration 0101, ADR-159) is the only role with DELETE on the retained tables, used by a separate process whose credentials the API never holds; INV-1 reads "immutable until expired by the retention job". Its grants are pinned by `apps/api/tests/retention/test_retention_role.py`; the application role can read but neither write nor erase retention reports, tombstones or the job's progress. Enforcement (`ABB_RETENTION_MODE=enforce`, ADR-160) is off unless an operator sets it, bounded per tick and per batch, stoppable at every batch, refuses to run without the artifact directory, and writes counts-only audit entries (system actor) and permanent tombstones; the job holds `UPDATE` on exactly two columns (`runs.trace_state`, `project_artifact_usage.bytes_stored`). Caveats: a role with CREATEROLE/superuser or the owner can still alter privileges; `ALTER ROLE ... PASSWORD` is sent by `alembic upgrade` (set `log_statement` below `ddl` or keep migrations out of statement logs); Alembic only sets the password on an upgrade that ends at head. A superuser or the owner can still rewrite
  history: protect those credentials, and use `ABB_MIGRATION_DATABASE_URL` for the owner connection in deployments. Set
  `ABB_RUNTIME_DB_PASSWORD` for `alembic upgrade` to give the role a login; without it the role cannot log in.
- **Project and workspace purge (Phase 19b step 5, ADR-163).** The most destructive operation is an owner-run command, not an endpoint: `python -m abb_api.retention purge-project|purge-workspace` with the retention role's credentials, which the API process never holds (the API does not even import the code; tested). It is a dry run unless `--confirm` repeats the exact slug, marks the scope `purging` first (keys answer the uniform 401, the project or workspace reads as not found) and leaves a permanent, ids-only record (`purge_tombstones`: no names, no content, no foreign keys). The retention role gained `DELETE` on the tables a purge empties but, for credential and content tables (`api_keys`, `alert_destinations`, policies, approvals, alert notifications), only column-level `SELECT` of the key columns: it can delete a key or a destination, never read a secret or sealed configuration (pinned by `tests/retention/test_retention_role.py`; its startup check still refuses a role that can read or forge a credential). The runtime role can only read the record and still cannot delete projects, workspaces, runs or events. Caveats: the application role can write `projects.purge_state` and `workspaces.purge_state` (it can hide a scope, not delete one; KI-212); backups made before a purge keep the data (KI-215).
- Sign-in and sessions (implemented, Phase 15, ADR-060): OIDC code + PKCE against a configured issuer; opaque 32-byte session cookie
  (`__Host-abb_session`, `HttpOnly`, `Secure`, `SameSite=Lax` on https), only `sha256` stored, absolute 7 d / idle 24 h, revoked at once by
  logout, member removal (the next request re-reads membership), or `relink-user`. State, nonce, PKCE verifier and a validated `return_to` live in
  a database row bound to an `abb_login` cookie (login CSRF fails); ID tokens are verified (RS256/ES256, `iss`, `aud`, `azp`, `exp`, `nonce`,
  `email_verified` required); identity links on `(provider, subject)` and a verified email may adopt only a user with no subject yet. Tested in
  `tests/test_auth_sessions.py`. Cookie-authenticated writes need an `Origin` equal to `ABB_WEB_ORIGIN` (proxy and API); the API never reads
  `X-Forwarded-*`. The web proxy forwards an allowlist of headers and never `Authorization`, other cookies or `Set-Cookie`.
  **No person without an invitation** (Phase 19a step 5, ADR-157, KI-076): a verified identity gets a `users` row at the callback only if it
  is already a user (by subject, or a CLI-made user with no identity yet) or some workspace holds an open invitation for its verified
  email; otherwise nothing is written and the answer is one fixed `403 NO_ACCESS` for every un-invited case (the page never echoes the
  address). **Sessions page** (KI-068): a person lists and ends only their own sessions (`/v1/auth/sessions`; another person's session id,
  an unknown one and an ended one are the same 404; the session in use is ended by signing out), the list carries no cookie value or hash
  and a device label chosen from a closed vocabulary (the `User-Agent` is never stored), each change is audited in the person's workspaces
  and limited per person (`ABB_SESSION_WRITES_PER_MINUTE`). **Invitations follow their inviter** (KI-077): demoting a person to a role
  without `invite.write`, or removing them, revokes their open invitations in that workspace in the same transaction, one audit row each.
  **The login cookie is `__Host-abb_login` on https** (ADR-156, KI-075, implemented): `Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=600`, no `Domain`,
  so a sibling subdomain cannot plant or overwrite it (login CSRF by cookie planting); the unprefixed name is never read on https (no fallback), and the
  web relay refuses a callback whose `Cookie` header carries the login cookie twice. On plain http (development) the name stays `abb_login` with
  `Path=/api/auth` and the prefix protection does not exist (the same as the session cookie); production requires an https web origin.
- Authorization (implemented, Phase 15, ADR-061): one `authorize()` path for keys and people, the role/scope matrix as data with a literal
  second copy in the tests (both directions), every route in a registry the tests walk (a route without a case fails CI), an AST test against
  scattered role checks, canary tests that nothing of another workspace leaks, `404` (never `403`) for a workspace or id the actor cannot see.
  VIEWER and BILLING never receive captured content (`payload.read`; event payloads are withheld, artifact content is `403`).
  Captured content includes shell command text and file paths (review F3, ADR-061). **A VIEWER can see**: that a run, a model call, a tool
  call, a shell command, a file edit or a git step happened; times, durations, statuses, exit codes, risk classes and categories, token
  counts, costs, model and tool names, line counts, languages, content hashes, run names, project and agent names, test counts and the
  analytics built from them. **A VIEWER cannot see**: inline payloads, artifact content (diffs, stdout, stderr), `shell.command`,
  `shell.cwd`, `file.path`, `git.repo`, `git.branch`, `git.push_target`, `http.url`, `test.failing`, the same-class attributes sent under
  an unregistered name (last segment `command`, `path`, `url` ...), and the names of shell, file and git spans: those values are replaced by
  `[withheld]` on the server (events, spans, streams), so the page cannot leak what the API did not send. Free text an integration puts in
  other fields (`span.name` of a custom span, `tool.operation`, `policy.reason`, `retry.reason`, `run.name`, tags, run `metadata`) is not
  classified as content; tracked as KI-078.
- Search (implemented, Phase 16, ADR-100/101): `GET /v1/runs?q=` is permission-safe by construction. *An actor may search a field, or receive a derived flag or count, only if the same actor receives that
  field's values on the existing read routes.* `file:` and `command:` need `payload.read` and are refused with `403 PERMISSION_DENIED` (canonical `details.field`, through `authorize_audited`, before any
  statement); free text for everyone else compiles to the metadata document only; the derived `run_search` table keeps metadata and content in separate columns with separate truncation flags, a statement
  for an actor without `payload.read` never references a content column (asserted on the compiled SQL); facets are metadata-only; there is no total count and no ranking; error positions depend only on the
  text; the `span.name` *attribute* is indexed, never the derived span name; unregistered attributes that are not content by name are not indexed (KI-080). `run_search` holds commands and paths: it is as
  sensitive as the events and is reachable only through the compiler. Search terms are the searcher's own input: the API logs no query text, the audit log never records `q`, and the web sends
  `Referrer-Policy: no-referrer`. A search runs under a time budget (which includes the wait for a database connection) and a per-process fair share of permits: a cap per actor, a share per workspace and a process total, refused with `SEARCH_BUSY` and a fixed reason, so one noisy key or workspace cannot take every permit (KI-082, ADR-158). Concurrency is per process by nature; the rate side is the shared limiter.
- Members are visible to every role of a workspace (`member.read` is in every role, including BILLING and VIEWER: names, emails and roles of the
  people one works with). If that is too open for an organisation, remove `member.read` from a role in `authz/matrix.py` and the literal table
  (one line in two places); tracked as KI-070.
- Audit (implemented, Phase 15, ADR-062): every mutating administrative action and a bounded number of denials by people go to an append-only
  `audit_log` the runtime role cannot UPDATE or DELETE; `details` never hold secrets, tokens or emails (validated, tested); readable by OWNER, ADMIN
  and SECURITY at `GET /v1/audit`. Purging it is a privileged owner job (`docs/runbooks/auth-and-access.md`).
- Unscoped lookups (deliberate, each by an unguessable value, then every query is tenant-scoped): `api_keys.key_id`, `sessions.token_hash`,
  `login_states.state_hash`, `invitations.token_hash` (at acceptance, before the workspace is known). `GET /v1/me` reads a person's memberships
  across workspaces; it is the only cross-workspace read.
- Bounds on what a member can create: 500 members, 200 active keys, 200 open invitations, 200 projects, 1,000 pricing overrides per workspace
  (`409 LIMIT_REACHED`); denial audit rows 10 a minute per person; web request bodies 64 KiB.
- Development conveniences exist only when `ABB_ENVIRONMENT` is `development` or `test`: an `http` issuer, the fake OIDC provider, `create-session`
  and the seeded `owner@local.test` (the last two also need `ABB_ALLOW_DEV_SESSIONS=1`), fixture mode refuses production unless
  `ABB_WEB_ALLOW_FIXTURES=1`. `ABB_ENVIRONMENT=production` is mandatory for any reachable deployment; the API warns at startup when OIDC is
  configured outside it.
- Local/demo credentials are generated, documented as dev-only, never real. `owner@local.test` and the E2E people (`*@auth.test`) are fake
  addresses that only the fake provider can authenticate.

Evaluations, comparison and experiments (Phase 11, ADR-090..092): what a reader without `payload.read` (VIEWER, BILLING-like keys) receives. Scores, labels, states, error codes, evaluator
names, kinds, versions, allowlisted built-in `metrics` and the template explanations of built-ins (numbers only, identical for two runs that differ only in captured content) are visible
to every reader of the run. Withheld, as `null` plus a marker: the free text of a `human` verdict or a model judge's reply (`explanation_withheld`), `metadata.raw` (`metadata_withheld`),
and an evaluator's path-bearing config (`allowed_globs`, `forbidden_globs`, `required_paths`) and `rubric` (`config_withheld`). The judge prompt is never stored or returned. A comparison for such a reader is
computed on a metadata projection (kinds, tool names, model names): commands, paths and content span names never enter an alignment key or label, two runs that differ only in them compare as all `equal`, and `files`
carries per-side counts only (an intersection would be a set-membership oracle); the response says `alignment: metadata` and the page says divergence is computed on metadata only. Variant configs of an experiment are
labels visible to every reader (documented: no secrets, prompts or paths; values at most 256 characters). All free text (evaluator, experiment and variant names, explanations, labels, error text, config values) is
rendered as text in the web app. Writes need the web app's `Origin` (people) and the actions `evaluation.run`, `evaluation.label`, `evaluator.write`, `experiment.write` (OWNER, ADMIN, DEVELOPER; keys hold none of them,
KI-101). The judge treats the run as untrusted data (a delimited, escaped block; clamped score, allowlisted label; `include_content` is opt-in and sends commands and paths only, INV-5); a reply above `max_cost_usd` fails the row and
pauses the evaluator for `ABB_JUDGE_COOLDOWN_SECONDS`. The fake judge refuses to start outside development or test unless `ABB_JUDGE_ALLOW_FAKE=1`. An evaluator can never change a run: the handler writes only to `evaluations`,
runs under a cooperative time budget, and stores only a built-in's own error text or a fixed code (never `str(exc)` of anything else); the `evaluations` table refuses updates of terminal rows with a database trigger.
Evaluations are derived from telemetry the sender controls: `tests_passed` trusts the `test.failed` and `test.framework` attributes of the last test event the client sent, so `ground_truth: true` means "not a model opinion", not "verified" (KI-116).
Open: KI-100 (injection through client-controlled labels cannot be excluded by construction; no real provider), KI-106, KI-116.

Reliability findings (Phase 10, ADR-080): evidence is metadata only by construction (an allowlist of keys per finding type; no command, path, payload, payload digest, run name or
error text; the `fingerprint` and the `id` derived from it are functions of public metadata only, never of payload bytes, so they cannot be used to test a guessed payload; a reader learns only that the events cited by an `identical_request` finding carried equal payloads), so `GET /v1/runs/{id}/findings` needs only `run.read` and returns the same body to every reader; a detector failure surfaces as a fixed-text job error that cannot quote an
attribute value; the cost baseline is scoped to the caller's workspace, project and agent.

Policies (Phase 14, ADR-140): a policy is configuration that decides what agents may do, so it is owner/admin-only to change (`policy.write`; no API key scope
holds it), readable by owner, admin, developer and security (`policy.read`), versioned (immutable rows: the runtime database role cannot UPDATE or DELETE
`policy_versions`) and audited (`policy.publish` records scope, version, SHA-256 and rule count, never the text or the comment, in the same transaction as the version).
The document is untrusted input: it is size-checked before parsing, read through PyYAML's event stream only (no object is ever constructed, so no tag can name a class),
and refuses anchors, aliases, tags, merge keys, control and invisible formatting characters, deep nesting and over 5,000 nodes; errors carry a line, a column and a
fixed message and never echo a value. Evaluation is a pure function with a linear-time glob matcher (no regular expressions, no backtracking) whose reason text comes
only from the rule's own description or id, never from the action. The dry run reads recorded events through the event store contract, attributes only, behind
`policy.write` (held only by roles that also read payloads), and returns counts, rule ids and ids: no command, path or argument. **The guarantee is cooperative**: the
policy service stops an agent that asks it (through the SDK); it does not stop one that does not, and no screen or document may claim otherwise. Management limits are
shared across API processes (ADR-152); the dry run's mapping covers seven event kinds (KI-140).

Policy checks in the SDK (Phase 14 step 3, D1/D3/D5; `docs/architecture/sdk-python.md`): **what is enforced is only what asks.** `guard()`, `@guarded`, the coding recorder (`read_file`, `write_file`, `delete_file`, `run_command`, the `git_*` helpers that run a command, `git_push`) and the MCP adapter (`call_tool`) ask the policy service before acting and do not act on a denial; the LangGraph, OpenAI and Anthropic adapters do not run tools and ask nothing unless a tool function carries `@guarded`. An agent that bypasses the SDK (its own subprocess, file or network call, or a tool the integration does not wrap) is not stopped, and the three-line git metadata reads the recorder makes for event attributes are not guarded. It is off by default (`policy_mode`, `BLACKBOX_POLICY`). Failure never crashes the host (INV-4): `guard()` raises only `PolicyDenied` (and its subclasses `ApprovalDenied`, `ApprovalExpired`) and `PolicyUnavailable`; any other failure, an SDK bug included, follows the fail mode (R0-R2 and unclassified open with a `policy.warning`, R3-R4 closed; per capability overrides; the server's fail-mode table only if cached before the outage) and a wait for a person never falls open. Every wait is bounded (a decision by `policy_timeout`, an approval by its expiry plus 5 s and an absolute cap), an approval is spent by presenting the whole intent once and a consume that did not finish is a denial (KI-145), arguments over the server's limits are never sent, and arguments, resources and server error text are never logged or put in a `policy.warning` (its reason is one of nine fixed words, plus `policy_warnings_dropped` written by the spool). The fail-open path is the price of availability: with the service down an R0-R2 action goes ahead and the only trace is the warning, so a deployment that cannot accept that sets `policy_fail_modes={"shell.execute": "closed"}` (or `on_error="closed"`). Limits: KI-148 to KI-151.

**Residual risk of the policy engine: fail-open during an outage (Phase 14 review).** The single most dangerous property of this phase is that the SDK fails **open** for R0-R2 and for unclassified actions when the policy service cannot be asked, and **every MCP tool call is unclassified**, so with the service down for longer than its short timeouts every low-risk action and every tool call goes ahead with no decision, and the only trace is a `policy.warning` (which the SDK now keeps in a bounded in-memory spool and re-sends once the API answers, KI-152; a process that exits during the outage still loses it). A deployment that must never fall open for a capability sets it closed, in this order of strength: `on_error="closed"` on the call or `@guarded(on_error="closed")`; `policy_fail_modes` / `BLACKBOX_POLICY_FAIL_MODES` (for example `shell.execute=closed,filesystem.write=closed,tool.*=closed`), which is operator-owned and holds even if the agent's code is changed; and `on_error: closed` on the rule, which is honoured only by an SDK that cached the server's fail-mode table before the outage (ADR-141 point 9). **Recommended for a sensitive deployment:** `tool.*=closed` and `shell.execute=closed`, `github.*=closed`, `database.*=closed`; `on_error: closed` on every rule that guards secrets, production or money; monitor `policy_fail_open` and the `policy.warning` events; accept that a closed mode turns a policy outage into an agent outage. Related, stated plainly: a key that holds only `policy:check` can ask about runs that exist but cannot create runs; a key that holds `events:write` creates runs by ingesting anyway. The server's own event ids are an HMAC under a secret stored once per installation in `server_secrets` (append-only for the application role); anyone who can read that table can compute them, so protect database read access as you protect the runtime credential.

Alerting (Phase 17 step 1, ADR-150; `docs/architecture/api-v1.md`): **an alert is a notification that points at one checkable thing; it never carries content.** A destination's target (a webhook or Slack URL, or an
email recipient list) and a webhook's signing secret exist in the database only as AES-256-GCM ciphertext under a key in the append-only `server_secrets` table (`alerts-encryption`; HKDF-derived; each blob names the key
that sealed it, so rotation needs no re-encryption), with associated data binding each blob to its destination row, workspace and purpose (a copied blob does not open). Only the sender (step 2) and the "send test" path
decrypt; the API returns a masked form computed once at save, shows the signing secret once on create and rotate, and never puts a URL, a recipient, a secret or free text in a response, a log line or an audit row
(sentinel scans of responses, `audit_log`, `alert_destinations` bytes and logs in `tests/test_alerts_api.py` and `tests/test_alerts_sealed.py`). Destinations are visible to owners and admins only (`alert.destination.read`);
other roles that read runs see rules (without their destinations) and the history (destination type only; no approval id without `approval.read`); no API key holds any alert action; foreign and unknown ids are one 404.
Saving a target applies an early guard (https only, no credentials, no non-public address literals in any spelling, no internal names; nothing is resolved at save) that is **not** the SSRF defence: the sender resolves the
name at delivery and refuses non-public addresses (step 2). Payloads are built from a closed set of fields (fixed title per trigger, ids, project/agent/rule names, severity, counts and amounts, times, a relative link) and never
read events, attributes, evidence, error text or run names (`tests/test_alerts_evaluation.py` plants a sentinel in every such place and scans the notification rows, jobs, audit rows and logs). The project, agent and rule **names** in a payload are metadata
the product shows every reader but are text an SDK or an owner chose; each channel must escape them. Evaluation never runs on a hot path (producers enqueue one small keyed job), reads metadata only and is idempotent on
`(workspace, rule, destination, dedupe key)`. Residual risks: whoever can read `server_secrets` and `alert_destinations` can decrypt every destination (protect database read access as the event-id key); the email destination
type adds an outbound SMTP path that the operator configures (step 2 bounds it).

Alert delivery (Phase 17 step 2, ADR-151). **The outbound sender is the SSRF defence; the save-time guard is not.** For every webhook and Slack request the hostname is resolved at delivery time and **every**
returned address must be public (private, loopback, link-local including the metadata address, CGNAT, multicast, reserved, IPv6 unique-local and the IPv4-mapped, 6to4, Teredo and NAT64 forms, judged by the IPv4
they carry; one non-public answer refuses the whole set), the connection is made to that checked address (pinned) with the hostname as SNI and certificate name so a rebinding answer can never be used, redirects are
never followed, one 5 s deadline covers the whole exchange, only the first bytes up to the status line are read (2 KiB at a time, 16 KiB at most) and discarded, never stored or logged, and no body is read, proxy environment variables are ignored, and a header or path cannot be used
to start another request. Secrets are opened for the length of one send and appear in no log line, audit row, history row or response (`tests/test_alerts_delivery.py`, `tests/test_alerts_send_test.py` scan logs,
receivers, mailboxes, rows and responses with planted sentinels). Names are escaped per channel (Slack mentions and links, email headers and HTML). Webhooks are signed `v1=HMAC-SHA256(secret, timestamp.body)`;
receivers must verify it over the raw bytes, reject stale timestamps and dedupe on `X-ABB-Alert-Id` (delivery is at-least-once). Residual risks: an owner (or an attacker holding an owner session) can point a
destination at any **public** host and port, so the server can be used for blind requests to public services, bounded by the 20 deliveries a minute and 500 a day per destination (an overlapping pair of delivery ticks can overshoot that by up to one batch of 5), the 5 s deadline, the attempt cap and
auto-pause after ten consecutive failures, and the answer is never shown; a hostname's TLS certificate is verified with the system trust store, so a receiver with a private CA cannot be used; the SMTP relay is
operator configuration, trusted as such, and email is plain text over the relay's own security (STARTTLS fail-closed by default); the signing secret is shared with the receiver and a leaked one lets anyone forge
alerts to that receiver until it is rotated (`rotate_signing_secret`, no overlap window).

Alert destination ports (Phase 19a step 6, ADR-158, KI-162). A webhook or Slack destination may only use the ports in `ABB_ALERTS_ALLOWED_PORTS` (default 443; an `https://` URL without a port is 443), so a destination cannot be used to probe SSH, SMTP, Redis, Elasticsearch and similar services of third parties. The port is refused when the destination is saved and checked again, before any lookup or connection, at delivery and in send-test (`blocked_port`, permanent), so a destination saved under a wider list is stopped too. Residual: blind requests to public hosts on allowed ports remain, bounded by 20 deliveries a minute and 500 a day per destination, the destination limit, the 5 s deadline, the attempt cap and auto-pause; no response is ever shown. The test network file does not bypass the port check.

Alert end-to-end test network (Phase 17 step 4, ADR-151 addendum). `ABB_ALERTS_TEST_NETWORK_FILE` replaces the sender's DNS, connect address and TLS trust with a local file so the end-to-end suite can use receivers on this
machine. It is **unset by default**, **refused at startup unless `ABB_ENVIRONMENT` is `development` or `test`**, logs a warning when active, and changes nothing between the address check and the connection
(a private or loopback answer is still refused). Never set it on a deployment that receives real alerts; `tests/test_alerts_testnet.py` proves the default and the refusal.

### Failed-authentication throttling (Phase 19a step 2, ADR-153)

Threats and answers. *Credential stuffing and key guessing*: a key is `abb_live_<12-char id>.<256-bit secret>`, so guessing a secret is not
feasible; the risk is load. Failures (not successes) are counted per client address, per presented key id and globally, in fixed windows shared by every API process;
beyond a budget the answer is `429 AUTH_THROTTLED` with `Retry-After`, identical for an existing and an unknown key id. *Flood amplification*:
a refused request is answered from memory (no lookup, no counter write); an invalid token seen moments ago is answered from memory; the global
ceiling bounds the lookups per window. The database-load figures are in `docs/benchmarks/phase-19a-auth-throttle.md`.
*Header spoofing*: the client address is read only when `ABB_TRUST_PROXY=1`, from the last `X-Forwarded-For` entry (the one the proxy appended); otherwise the header is ignored, so it cannot be
used to evade a bucket or to frame another address. Set it only when the proxy is the only route to the API. The stored counter key is a domain-separated SHA-256 prefix of the address,
kept at most a window plus 60 s; addresses and secrets are never logged or audited.
*Throttle as a denial-of-service lever* (the tradeoff, stated plainly): a caller who knows a key id (ids are visible to project members, secrets are not) can close **that key id's own** window for up to a minute with ten bad
secrets (KI-191); authentication of that key id's valid requests that are not in the process cache is then refused with `429` for the rest of the window. A flood that closes the address or the global window no longer locks out any other valid key:
while those are closed a request for a key id that exists (a per-process set of active key ids, ids only, refreshed every 60 s and on key creation) still reaches full authentication, bounded by that key id's own budget, and a random key id is refused from memory.
Anyone sharing a trusted address with an attacker shares that address budget. Ingestion, people's sessions, and sign-in starts and callbacks are never refused by it.
The throttle fails open (a counter outage falls back to the local budget; any throttle error leaves the normal 401). One `auth.throttled` audit row per workspace per window is written for key ids that
belong to a workspace; others are logged by scope name only (KI-177). Failed session cookies and sign-in starts are not covered (KI-176, KI-073).

### Project quotas (Phase 19a step 3, ADR-154)

Threats and answers. *A runaway agent or a hostile key inventing data* (agent slugs, run ids, events, artifacts): each project has four
quotas, with environment defaults (`ABB_QUOTA_*`) and per-project overrides. At a limit the excess is **refused loudly**, never folded or silently dropped:
the batch answer names the refused event, the fixed code `QUOTA_EXCEEDED` and the closed quota name (`agents`, `runs_per_day`, `events_per_day`,
`artifact_bytes`); the other endpoints answer `413 QUOTA_EXCEEDED` with the same name. Data the project already has is never refused: events of runs
and agents it already holds, and replays of events it already stored, are accepted at any usage. The counters are written in the same transaction as the data
(so they equal what committed) under one per-project lock, so a race cannot pass a limit, for any quota (tested with concurrent batches and uploads).
*Isolation*: every counter, override and lookup carries the workspace and project; one project's use never counts against another, in the
same workspace or another (tested). *Who may raise a limit*: only owners and admins (`project.write`), every change is an audit entry naming the quota and its old
and new value, and a person without the role (or a key) gets 403, a project of another workspace 404. A raised limit is a lever on the operator's disk and database; the
defaults are generous abuse rails, not sizing advice. *What a refusal reveals*: the quota name and, on the non-batch endpoints, the limit and current use of the caller's own project;
never another tenant's data. *Limits of the control* (stated plainly): it does not stop a single project from using its allowance, there is no alert on usage yet, server-written decision and
approval events are not counted, and the day boundary follows the API process clock (UTC). See ADR-154.

### Edge hardening (Phase 19a step 4, ADR-155)

Threats and answers. *Memory exhaustion by concurrent large bodies* (KI-026): the process counts the body bytes it holds and refuses more with `503 BUSY` (before any byte is read when `Content-Length` says it cannot fit: the declared size is reserved at admission and reconciled with what arrives; a reservation is held at most the read timeout, so clients that declare large bodies and stall can make other large bodies wait up to 60 s, never longer, and never small ones);
small bodies (64 KiB) are always admitted; a body is dropped after 60 s; accounting is released on disconnect, cancel and exception (tested); every route has a global 8 MiB ceiling. Per process, first come first served (KI-182); the reverse proxy's body limit and timeouts remain the first line.
*Script injection in the web app (XSS)*: scripts run only with the response's nonce (`strict-dynamic`), no `'unsafe-inline'` or `'unsafe-eval'`; the policy is enforced and tested in a real browser on every page (zero violations; an injected inline handler is blocked). `connect-src 'self'` and `form-action 'self'` stop exfiltration and credential posting to other origins; `font-src`/`img-src` self.
`frame-ancestors 'none'` and `X-Frame-Options: DENY` stop clickjacking; `base-uri`, `object-src` close the rest. **Accepted weakening:** `style-src-attr 'unsafe-inline'` (style attributes cannot execute script; KI-181). *Sniffing, referrer and capability leaks*: `nosniff`, `Referrer-Policy: no-referrer`, a deny-all `Permissions-Policy`, COOP and CORP `same-origin`.
*API responses*: deny-all CSP, `no-store` on every response (authenticated, session and secret-bearing included), HSTS only when the deployment is https. *Reconnaissance*: `/docs`, `/redoc` and `/openapi.json` are off in production. *INV-1 lost by misconfiguration* (KI-031): the API and worker refuse to start in production as a role that can rewrite events or alter the schema, or when that cannot be verified; the override is explicit and logged on every start; messages never contain a credential.
Limits (stated plainly): CSP reduces the impact of an injection, it does not prevent one; a reverse proxy that caches HTML or strips headers undoes it; the role check is a start-time check (KI-183).

### Backups, restore and secret rotation (Phase 19b step 4, ADR-162)

- **A backup bundle holds `server_secrets`** (the alert-encryption key and the event-id key) and all captured data: it is as sensitive as the database itself. Bundles are created with mode `0700`/`0600` and the tool refuses a non-empty output; encrypt them at rest (`--encrypt`: openssl AES-256-CBC with PBKDF2 and an HMAC over the manifest; a wrong passphrase or any change fails with one fixed error before decryption) or keep them on encrypted storage. An unencrypted bundle's manifest is not authenticated (checksums detect corruption, not replacement).
- The passphrase is read from a file (mode 0600 at most), an environment variable name or a descriptor, never an argument; it and connection URLs never reach logs, output or errors (tests scan for planted sentinels). `PGPASSWORD` is set only in the tool child's environment.
- The tools need the database owner role and are operator-only; the API has no route for them. Restore never drops or overwrites and refuses a non-empty target.
- Alert-key rotation (`python -m abb_api.rotate alerts`) re-seals with the same associated data, never logs plaintext, and never deletes an old key (the runtime role cannot); retiring a key is a manual owner action after the report says zero blobs reference it. The **event-id key is not rotatable without breaking dedupe** of already-accepted events; the index of every secret and its rotation is `docs/runbooks/secrets.md`.
- Not in a bundle and therefore the operator's to protect: `.env`, the OIDC client secret, the SMTP password, database role passwords, the backup passphrase.

## Known gaps (tracked in KNOWN_ISSUES.md)

Login and key-management endpoints are throttled only by the per-client/per-instance web limits and the API's global backstop (now counted across API processes, Phase 19a step 1, ADR-152); failed API-key authentication is throttled (ADR-153) (KI-019) and projects have quotas (ADR-154) (KI-018, KI-040). The identity provider integration was exercised against the fake
provider only; a real provider login is an open acceptance check (KI-067). Sessions can be listed and ended in the UI (Settings, Sessions; KI-068 resolved). Providers that omit `email_verified` cannot sign in (KI-069). Rate limits hold across API processes while the database answers and are per process when it does not (the shared limiter fails open, ADR-152). The Python SDK
redacts on the client (Phase 3: key rules, best-effort secret patterns, callback, payload modes; default drops payloads); since Phase 13 the server
redacts again, but detection is best effort, so **clients should still keep secrets out of events**, and a project that needs no payloads should stay
METADATA_ONLY. Stored text can carry a `[REDACTED:credential]` marker where an assignment looked secret-shaped (a name ending in `token`, `secret` or
`password` is redacted whatever it holds, KI-135); the stored event is immutable, so it is not repaired. A time-budget drop (`scan_failed`) is the one
non-deterministic redaction: an identical redelivery that scans in time conflicts with the stored version and the first one wins (ADR-130 Amendment 2).
The capture mode is read once per batch, so a tightening can miss one batch already in flight (KI-139).

## Process
Security-sensitive changes require a `review-change` pass and a note in the phase plan.
Incidents: severity scale and flow in spec §124; cross-tenant exposure is SEV-1.

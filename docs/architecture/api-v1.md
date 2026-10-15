# HTTP API v1

Reference for the public ingestion and query API. The machine-readable contract is
[`apps/api/openapi.json`](../../apps/api/openapi.json) (regenerate with `make openapi`; the quality
gate fails if it is stale). Event bodies are defined by the [event contract](events.md). Spec: §41, §71,
§101; decisions: ADR-002, ADR-006, ADR-012, ADR-060..062.

## Authentication

Two kinds of caller, one enforcement path (ADR-061). Which one a request is depends on the credential it carries; if both are sent, the
`Authorization` header wins and the cookie is ignored.

**API keys** (SDKs and tools): `Authorization: Bearer abb_live_<key_id>.<secret>`. Created by the CLI or by `POST /v1/api-keys` and carry scopes and
an optional project:

| Scope | Allows |
| --- | --- |
| `events:write` | `POST /v1/events`, `POST /v1/events/batch`, `POST /v1/runs` (needs a project-bound key); also the agent-side policy routes below |
| `runs:read` | every run, artifact, analytics and pricing `GET` (it implies the `payload.read` and `artifact.read` actions, so artifact content stays readable) |
| `artifacts:write` | `PUT /v1/artifacts/{id}` |
| `policy:check` | the agent-side policy routes only (ask, wait, consume, withdraw, fail-mode table; a project-bound key); `events:write` grants the same, so an SDK key needs no change |

Every key also reads its own workspace row and its project(s). Keys never receive member, invitation, key-management or audit actions. A
**project-bound** key only ever sees its own project. A **workspace-wide** key (no project) can read every project of its workspace but cannot
ingest. Unknown, malformed, wrong-secret, revoked and expired keys, and a request with no credential at all, return the same
`401 API_KEY_INVALID` (with `WWW-Authenticate: Bearer`). A valid key without the needed scope gets `403 INSUFFICIENT_SCOPE`
(`details.required_scope` is unchanged since Phase 2). A key may send `X-ABB-Workspace` only with its own workspace id (anything else is 404).

**People** (the dashboard, ADR-060): a session cookie (`__Host-abb_session` on https, `abb_session` on a localhost http origin) set by
`GET /v1/auth/callback`. A missing, expired or revoked session is `401 SESSION_INVALID`; database trouble is `503`, never 401. A person acts in one
workspace per request, named by the header `X-ABB-Workspace: ws_...` (missing on a workspace route: `400 WORKSPACE_REQUIRED`; a workspace the person
is not a member of: `404 WORKSPACE_NOT_FOUND`, the same as one that does not exist). A person without the role's permission gets
`403 PERMISSION_DENIED` with `details.required_permission`. Any request authenticated by cookie that is not `GET`/`HEAD` must carry an `Origin`
equal to `ABB_WEB_ORIGIN` (`403 CSRF_REJECTED`). Roles (per workspace) and what they hold:

| Role | Reads | Writes |
| --- | --- | --- |
| OWNER | everything, including payloads and the audit log | members (incl. owners), invitations, keys, projects, pricing |
| ADMIN | everything, including payloads and the audit log | the same, except changing or inviting owners |
| DEVELOPER | runs, payloads, artifacts, analytics, key list | create keys, revoke their own |
| VIEWER | run metadata, analytics, prices, members (no payloads or artifact content) | nothing |
| SECURITY | runs, payloads, key list, audit log | revoke keys |
| BILLING | analytics, prices, members (no runs, no payloads) | pricing overrides, cost rebuild |

The authoritative table is `apps/api/src/abb_api/authz/matrix.py`; `GET /v1/me` returns each membership's effective `permissions` list and the
web shows or hides by it. Resources of another workspace, or of another project for a project key, are `404`, indistinguishable from nonexistent ones.

### Sign-in and identity
- `GET /v1/auth/login?return_to=/w/...`: starts OIDC; `302` to the provider and sets the login cookie (`__Host-abb_login`, `Path=/` on an https web origin; `abb_login`, `Path=/api/auth` on http). `return_to` must be one of the app's
  own routes (`422 RETURN_TO_INVALID`). `503 AUTH_NOT_CONFIGURED` when no issuer is set.
- `GET /v1/auth/callback?code=&state=`: finishes it; `302` to the stored `return_to` with the session cookie. Every failure is `400/401 LOGIN_FAILED`
  (the cause is logged, never returned). Both endpoints are rate-limited (`429`) by a global backstop.
- `POST /v1/auth/logout`: revokes the session and clears the cookie; `200` even if the cookie was already invalid. Never a GET.
- `GET /v1/me`: the person, and every membership with workspace id and slug, role, `permissions` and `own_permissions`. The one read across workspaces.

### Workspace administration (all take `X-ABB-Workspace` for people)
| Operation | Permission | Notes |
| --- | --- | --- |
| `GET /v1/projects`, `POST /v1/projects` | `project.read` / `project.write` | id, slug, name; at most 200 per workspace (`409 LIMIT_REACHED`); project keys list their project only |
| `GET /v1/members`, `PATCH /v1/members/{user_id}`, `DELETE /v1/members/{user_id}` | `member.read` / `member.write` (+ `member.write_owner` when the old or new role is OWNER) | the last owner cannot be demoted or removed (`409 LAST_OWNER`, enforced under a row lock); at most 500 members |
| `GET /v1/invitations`, `POST /v1/invitations`, `DELETE /v1/invitations/{id}` | `invite.read` / `invite.write` | `POST` returns the one-time link `${ABB_WEB_ORIGIN}/invite#<token>` once; the token is in the fragment and no response or list ever repeats it; `409 ALREADY_MEMBER` / `ALREADY_INVITED`; at most 200 open |
| `POST /v1/invitations/accept` | a signed-in person | body `{token}`; no workspace header; `404 INVITATION_NOT_FOUND`, `409 INVITATION_USED`, `410 INVITATION_EXPIRED`, `403 INVITATION_EMAIL_MISMATCH` (the verified email must equal the invited one), `409 ALREADY_MEMBER` |
| `GET /v1/api-keys`, `POST /v1/api-keys`, `DELETE /v1/api-keys/{key_id}` | `api_key.read` / `api_key.create` / `api_key.revoke` (DEVELOPER: own keys only) | `POST` returns the token once (`Cache-Control: no-store`); ingestion scopes need `project_id` (`422 PROJECT_REQUIRED`); a person cannot mint scopes beyond their own (`422 SCOPE_NOT_ALLOWED`); a revoked key is `401` on its next request and ends its open streams; at most 200 active |
| `GET /v1/pricing`, `POST /v1/pricing/overrides`, `POST /v1/cost/rebuild` | `pricing.read` / `pricing.write` | overrides are append-only, at most 1,000 per workspace; `rebuild` takes `project_id`, `since`, `limit` (<= 10,000) and answers `202 {matched, queued, truncated}` |
| `GET /v1/audit` | `audit.read` | newest first, keyset `cursor`, optional `since` (an instant with a time zone, `422 INVALID_SINCE`) |

Every mutating call above writes one `audit_log` row (ADR-062); denials of people are audited at most `ABB_AUDIT_DENIALS_PER_MINUTE` a minute per
person. `payload.read` decides content: without it, `GET /v1/runs/{id}/events/{event_id}` returns the event with `payload: null` and
`payload_withheld: true`, and `GET /v1/artifacts/{id}/content` is `403 PERMISSION_DENIED` before any lookup (artifact metadata stays readable).
Command text and file paths are content too (ADR-061, review F3): to an actor without `payload.read`, event lists, detail and stream frames carry
`"[withheld]"` in place of the registry's content attributes (`shell.command`, `shell.cwd`, `file.path`, `git.repo`, `git.branch`,
`git.push_target`, `http.url`, `test.failing`, and unregistered keys ending in `command`, `path`, `url` and similar) and list those keys in
`withheld_attributes`; spans of kind `shell`, `file`, `git` or `null` return `name: null, name_withheld: true`. Metadata (exit code, duration, risk
class, line counts, hashes) is unchanged. Actors with `payload.read` see the stored values.

## Errors

Every non-2xx response, from every route, has this shape and an `X-Request-ID` header (also in the body):

```json
{"error": {"code": "RUN_NOT_FOUND", "message": "Run not found.", "category": "NOT_FOUND",
           "retryable": false, "request_id": "req_...", "details": {}}}
```

| Code | Status | Meaning |
| --- | --: | --- |
| `API_KEY_INVALID` | 401 | missing, malformed, unknown, revoked or expired key, or no credential |
| `SESSION_INVALID` | 401 | the session cookie is missing, unknown, expired or revoked |
| `PERMISSION_DENIED` | 403 | a person's role lacks the action; `details.required_permission` |
| `CSRF_REJECTED` | 403 | a cookie-authenticated write whose `Origin` is not `ABB_WEB_ORIGIN` |
| `WORKSPACE_REQUIRED` / `WORKSPACE_NOT_FOUND` | 400 / 404 | a person sent no `X-ABB-Workspace`; a workspace they are not in (or that does not exist) |
| `LOGIN_FAILED`, `AUTH_NOT_CONFIGURED`, `AUTH_UNAVAILABLE`, `RETURN_TO_INVALID` | 400/401, 503, 503, 422 | sign-in (never says why) |
| `LIMIT_REACHED`, `LAST_OWNER`, `ALREADY_MEMBER`, `ALREADY_INVITED`, `INVITATION_USED`, `PROJECT_EXISTS` | 409 | per-workspace bounds and membership rules |
| `INVITATION_NOT_FOUND`, `MEMBER_NOT_FOUND`, `KEY_NOT_FOUND` | 404 | absent or not visible |
| `INVITATION_EXPIRED` | 410 | the invitation lapsed |
| `INVITATION_EMAIL_MISMATCH` | 403 | the signed-in verified email is not the invited one |
| `SCOPE_NOT_ALLOWED`, `PROJECT_REQUIRED` | 422 | key creation beyond the creator's own actions; an ingestion key without a project |
| `INSUFFICIENT_SCOPE` | 403 | `details.required_scope` names the missing scope; `details.required_permission` (additive, Phase 15) names the action it maps to |
| `PROJECT_KEY_REQUIRED` | 403 | ingestion or run creation with a workspace-wide key |
| `RUN_NOT_FOUND`, `EVENT_NOT_FOUND`, `PROJECT_NOT_FOUND` | 404 | absent or not visible to this key |
| `RUN_PROJECT_MISMATCH` | 409 | the run id belongs to another project (per event in a batch) |
| `CURSOR_INVALID` | 400 | malformed cursor, or one from another listing |
| `CURSOR_STALE` | 409 | the run's ordering mode changed while paging; restart (retryable) |
| `BATCH_INVALID` | 400 | not JSON, no `events` array, empty, too many events, bad `batch_id`, broken gzip |
| `EVENT_INVALID` | 422 | single-event endpoint: the event failed validation (`details.issues`) |
| `EVENT_SCHEMA_UNSUPPORTED` | 400 | single-event endpoint: unsupported `schema_version` major |
| `PAYLOAD_TOO_LARGE` | 413 | body (compressed or decompressed) over 5 MiB; on the single-event endpoint also an event over 256 KB (inside a batch an oversized event is a per-event `EVENT_TOO_LARGE` entry in `errors`) |
| `UNSUPPORTED_MEDIA_TYPE` / `UNSUPPORTED_ENCODING` | 415 | not `application/json`; `Content-Encoding` other than gzip/identity |
| `REQUEST_INVALID` | 422 | invalid query parameter or run-creation body |
| `QUERY_INVALID` | 422 | the search text `q` could not be parsed (`details.position`, `details.reason`) |
| `SEARCH_BUSY` / `SEARCH_TIMEOUT` | 503 | this process's search permits are in use, or this actor's or workspace's fair share of them is (`details.reason`: `actor_limit`, `workspace_share`, `process_full`; ADR-158) / the search exceeded its time budget, which includes the wait for a database connection; retryable |
| `RATE_LIMITED` | 429 | retryable; honour `Retry-After` |
| `QUOTA_EXCEEDED` | 413 | a project quota is reached (Phase 19a, ADR-154): `details.quota` names it (`agents`, `runs_per_day`, `events_per_day`, `artifact_bytes`), with `limit` and `used` where known. Not retryable until the limit changes or, for the per-day quotas, the UTC day ends. On the batch endpoint the same code is reported per event inside a `202` instead |
| `QUOTA_SETTINGS_INVALID` | 422 | an override outside its range (`details.quota`); the message never repeats the value |
| `TOO_MANY_WAITERS` / `TOO_MANY_OPEN_APPROVALS` | 429 | too many long polls (workspace or process) / too many requests waiting for a decision; retryable (Phase 14) |
| `APPROVAL_NOT_FOUND` | 404 | absent, malformed, or not visible to this credential (another workspace, project or key): one body for all |
| `APPROVAL_NOT_APPROVED`, `APPROVAL_EXPIRED`, `APPROVAL_CONSUMED`, `APPROVAL_INTENT_MISMATCH` | 409 | consuming an approval; fixed codes that echo nothing |
| `APPROVAL_NOT_REQUESTED` | 409 | the approval is no longer waiting (`details.state`) |
| `APPROVER_ROLE_REQUIRED`, `SELF_APPROVAL_FORBIDDEN` | 403 | the member's role does not satisfy the rule; the request was made with their own key |
| `INTENT_INVALID` | 422 | the action intent cannot be canonicalised (`details.reason`) |
| `POLICY_UNAVAILABLE` | 503 | the stored policy cannot be read; retryable |
| `DEPENDENCY_UNAVAILABLE` | 503 | database trouble; retryable; `Retry-After: 2` |
| `INTERNAL_ERROR` | 500 | a bug; no internals are exposed, the stack trace is in the server log |

## Ingestion

`POST /v1/events/batch` takes `{"batch_id"?, "sent_at"?, "events": [...]}`: 1 to 1000 events, 5 MiB
uncompressed, optionally gzip (`Content-Encoding: gzip`; the limit applies to the compressed bytes and to the
expansion, which is capped while streaming). Each event is an [`EventIn`](events.md); the workspace and project
come from the key (an event naming another tenant is rejected, never overwritten).

`202` means the valid events are **committed to PostgreSQL**. The body reports per-event outcomes:

```json
{"batch_id": "b1", "accepted": 98, "duplicates": 1, "conflicts": 0, "rejected": 1,
 "errors": [{"index": 4, "event_id": "evt_...", "code": "EVENT_INVALID",
             "issues": [{"loc": ["attributes", "tool.name"], "code": "attribute_required", "message": "..."}]}],
 "server_time": "2026-10-07T12:05:00Z", "request_id": "req_..."}
```

- Invalid events never affect valid ones. Errors never echo submitted values.
- **Retrying is always safe.** Identity is `(workspace, event_id)`: same id and content is a `duplicate`;
  same id with different content is a `conflict` (the first copy is kept; history is never rewritten).
- Order of arrival does not matter. Events may arrive after their run completed.
- `POST /v1/events` is the single-event form: `202 {event_id, status: accepted|duplicate|conflict}` or a
  typed 4xx (`422 EVENT_INVALID`, `400 EVENT_SCHEMA_UNSUPPORTED`, `413`, `409 RUN_PROJECT_MISMATCH`).
- **Server-side redaction (Phase 13, ADR-130).** Before an event is stored the server applies the project's capture mode (`FULL`,
  `METADATA_ONLY` by default, `DISABLED`; set with `python -m abb_api.cli set-capture-mode` until the settings API of step 2) and scans every
  string it carries; secret-shaped text becomes `[REDACTED:<label>]`, and the event records `redaction.server_count`, `redaction.server_kinds`
  and `redaction.dropped_reason` ([events](events.md)). An event that cannot be scanned keeps its metadata and loses the value that could not be
  scanned. An event whose redaction fails outright is rejected with `REDACTION_FAILED`. An event of a type the server writes (`policy.decision`, `policy.action.blocked`, `approval.requested|granted|denied|consumed|expired|cancelled`) is rejected with `RESERVED_EVENT_TYPE`; the rest of the batch is stored and the SDK does not retry it. If the project's settings cannot be read the whole batch
  is refused with a retryable `503`. Artifacts follow the same modes (see `PUT /v1/artifacts/{id}` in the OpenAPI document): dropped bodies are
  stored as a one-line note and the `sha256`/`size_bytes` returned describe what is stored.
- **Quotas (Phase 19a, ADR-154).** Each project has four quotas: distinct agents (500), runs created per UTC day (50,000), events accepted per UTC day
  (5,000,000) and total artifact bytes (5 GiB); the per-run event cap is unchanged. An event the project cannot take is refused per event, inside a normal `202`, as
  `{"index": 2, "event_id": "evt_...", "code": "QUOTA_EXCEEDED", "quota": "events_per_day", "issues": [{"loc": ["quota"], "code": "events_per_day", "message": "..."}]}` and counted in
  `rejected`. `quota` is additive and closed (`agents | runs_per_day | events_per_day | artifact_bytes`); older clients see an ordinary rejected event and do not retry it.
  The rule, in batch order: an event naming an agent slug the project has not seen is refused at the agent limit; an event of a run the project has not seen is
  refused at the run limit (every event of that run, in this and later batches); any other event is refused when the day's events are used up. An event that was refused counts
  nothing (no agent, no run, no event). Events of existing runs and agents, and replays of stored events (`duplicate`, `conflict`), are never refused for a quota and never counted twice.
  `POST /v1/events` and `POST /v1/runs` answer `413 QUOTA_EXCEEDED` instead; `PUT /v1/artifacts/{id}` does when the stored bytes would pass the limit (a retry of an artifact
  already stored is still `200`). Usage and the audited overrides are in "Project quotas" below.
- Per-project rate limit: 2,000 events/s (burst 10,000) and 10 MiB/s by default, per API process
  (`ABB_RATE_LIMIT_*`); exceeding it returns `429` and stores nothing from that request.

## Runs

`POST /v1/runs` (optional; the first event of an unseen run creates it anyway) creates a `QUEUED` run:
`{run_id?, trace_id?, name?, agent_id?, metadata?}`; `201` when created, `200` when it already existed;
idempotent on `run_id`; `metadata` is at most 16 KB and 8 levels deep.

`GET /v1/runs` lists newest first (`sort=started_at` for oldest first) with `status` (repeatable), `agent_id`,
`project_id`, `started_after` (inclusive), `started_before` (exclusive) (RFC 3339 with offset; values with no UTC
representation are `422`), `limit` (1-200, default 50) and
`cursor`, and `q` (the search grammar below, at most 512 characters, combined with the other filters by AND). Paging is keyset-based, so runs arriving between page
requests never cause repeats or gaps.

`GET /v1/runs/{id}` returns the run: `status`, `name`, `agent_id`, `trace_id`, `started_at`, `completed_at`,
`duration_ms`, `ordering_mode`, `summary`, `summary_version`, `summary_state`, `metadata`, and `first_error`
(`{event_id, sequence, retries_after, seconds_after, cost_after_usd, cost_after_unpriced_calls}`, a typed projection of the summary's
first-error facts, ADR-112). It is null when the run has no first error or was summarised below the current version 6; read `summary_version`: at the
current version null is final ("no error event recorded"), below it the facts are "not rebuilt yet". A BLOCKED run with no error event takes its
blocking event (the first `policy.action.blocked`, else the blocking `run.completed`) as its first error; `error_count` is unchanged.

`summary_state`: `current`; `processing` while events wait to be folded in (new events debounce for about
1 s, then the worker recomputes); `failed` if summarization was dead-lettered and needs an operator
(`python -m abb_api.cli jobs-list` / `jobs-retry`). Everything in a run except its events is **derived** and
rebuildable (INV-2).

### How a run is derived

Events are put in canonical order, then folded:

| Field | Rule |
| --- | --- |
| `status` | starts `RUNNING`; `run.started` RUNNING; `run.completed` by event status (none/success SUCCESS, error FAILED, timeout TIMED_OUT, blocked BLOCKED, cancelled CANCELLED); `run.failed` FAILED; `run.cancelled` CANCELLED; `approval.requested` WAITING_FOR_APPROVAL; `approval.granted/denied` RUNNING. Transitions the state machine forbids are ignored, so a terminal status is final. A run created by `POST /v1/runs` is `QUEUED` until events arrive. |
| `started_at` / `completed_at` / `duration_ms` | earliest event time; time of the event that made the run terminal; their difference (null while running) |
| `name`, `agent_id`, `trace_id` | `run.name` of the earliest `run.started` (an explicit name from `POST /v1/runs` is kept unless events name the run); agent and trace of the **first `run.started` that carries a `sequence`** (the root emitter's; remote and forked emitters send none), else the earliest `run.started` (a skewed clock, a forged child agent event or `trace_id`, or a sequence-less `run.started` cannot become the run's agent or trace; a write key of the same project can still write any event into that project's runs, so this is consistency, not authentication); runs with no `run.started` (created by `POST /v1/runs`) use the earliest event |
| `ordering_mode` | `sequence` if every event has a `sequence`, else `time`. A multi-process run (Phase 9, ADR-070) is `time`: continued runs and forked children send no `sequence`. |

`summary` (version 5; **the next bump is 6**; first-error facts since 5, ADR-112): `event_count`, `duration_ms`, `llm_calls` (`llm.request.completed|failed`), `tool_calls`
(`tool.call.completed|failed`), `input_tokens` and `output_tokens` (sums over `llm.request.completed`),
`estimated_cost_usd` (the effective cost: sum over model calls of the cost line, ADR-040; name kept for compatibility), `retry_cost_usd`, `initial_cost_usd`, `cost_by_source_usd`, `unpriced_calls`, `tool_spans_ok/finished`, `llm_spans_ok/finished`, `retry_count`, `retries_unattributed`
(`retry.attempted`), `error_count` (events with status `error`/`timeout` or a `*.failed` type, each counted
once), `files_modified` (distinct `file.path` of created/modified/deleted), `models`, `first_event_at`, `last_event_at`, `agent_count` (exact number of distinct agents, counting agents only named by an
`agent.spawned`) and `agents` (**run detail only**; `GET /v1/runs` items carry `agent_count` but omit `agents`), then the first-error facts: `first_error_event_id`, `first_error_sequence`, `retries_after`, `seconds_after`, `cost_after_usd`, `cost_after_unpriced_calls` (all null/0 for a run with no error; "after" means later in canonical order; same rule as the web client's `firstError()`; metadata, identical for every role).
`agents` has at most 50 entries in order of first appearance, then one `(other)` row (state `mixed`) folding the rest; each entry:
`agent_id`, `parent_agent_id` (the agent owning the span the child's `agent.started` nests under, else the agent that emitted the
`agent.spawned` naming it; null for the root), `state` (`spawned` = named by a spawn and never started, `started`, `completed`,
`failed`), `event_count`, `llm_calls`, `tool_calls`, `error_count`, `estimated_cost_usd` (cost lines carry the event's agent,
ADR-040), `first_event_at`, `last_event_at` (null for a spawned-only agent).

**Multi-agent runs and clock skew (ADR-070, KI-085).** A child agent is a span with its own `agent_id` inside the parent's run.
Processes other than the one that created the run send no `sequence`, so the run is ordered by time and a skewed clock interleaves
emitters in the chronological list; `started_at`, `duration_ms`, the runs-list position and the analytics day bucket follow the
earliest `occurred_at`. Span nesting uses `parent_span_id` only and is unaffected. Re-deriving a run (`rebuild-costs`) applies the
root rule above, so a run whose first event was a skewed child may move to its true root agent (and between agents in the run
rollups).

### Findings and health

`GET /v1/runs/{id}/findings` (`run.read`; a key needs `runs:read`; BILLING people get 403) returns `{run_id, state, health, items}`. Derived by the `detect_findings` job
(ADR-080); a run not yet analysed returns `state.status: none` and empty `items`. The body is the same for every caller who may read the run.

- `state`: `status` (`none | processing | current | outdated | failed`), `detectors_version`, `summary_version_seen`, `computed_at`, `event_count_seen` and `truncated`
  (true when only the first `ABB_FINDINGS_MAX_EVENTS` events were read). After a dead letter the previous items are still returned, status `failed`.
- `items[]` (the §81.2 contract): `id` (`fnd_...`, stable across rebuilds), `detector`, `detector_version` (`name@x.y.z`), `finding_type`, `kind`
  (`deterministic | probable`), `severity` (`info | warning | medium | high | critical`), `category` (`reliability | security`), `confidence` (0 to 1), `title` (starts with `Probable:` iff `kind` is `probable`),
  `message`, `start_event_id`, `end_event_id`, `evidence_event_ids` (every cited `evt_` id; each resolves through `GET /v1/runs/{id}/events/{event_id}`),
  `fingerprint`, `evidence` and `created_at`/`updated_at`. `evidence` is typed per `finding_type`, holds metadata only (ids, counts, tool and model names,
  codes) and lists the `thresholds` the detector used.
- `health`: `overall` and `dimensions[]` (`reliability`, `tool_reliability`, `cost_efficiency`, `loops`, `context`), each `ok | warn | bad | no_data` with
  `inputs`, a one-sentence `rule` and `finding_ids`. No numeric score exists (ADR-081); finding-based dimensions are `no_data` unless the analysis is `current`.

`RunOut` (list and detail) gains `findings`: `{count, max_severity, state}` with `state` one of `none | current | outdated`, read from one query per page.
`findings` counts reliability findings only; `security_findings` (Phase 13, ADR-131) is the same shape for the security category (`max_severity` is `medium`,
`high` or `critical`). The security detectors' findings appear in `items` with `category: "security"`, `detector` `sec.*`, `kind: "probable"`, and evidence
`{rule, rules, count, event_ids, artifact_ids?, kinds?}`: fixed wording, event ids, counts and credential kind names only.

### Security (Phase 13, ADR-131)

All metadata: no route accepts or returns captured content, and each returns the same body to every caller it admits.

- `GET /v1/security/findings` (`run.read`; BILLING 403; a project-bound key sees its project): `{items, next_cursor, facets}`, newest run first (`run_started_at`
  then id), `limit` 1-200 (default 50). Filters (AND): `severity` (repeatable), `detector` (repeatable `sec.*`), `project_id`, `run_status` (repeatable),
  `since`/`until` (on the run's start, with a timezone). Item: `id` (`fnd_`), `run_id`, `run_name`, `run_status`, `project_id`, `project_slug`, `project_name`,
  `detector`, `detector_version`, `finding_type`, `kind`, `severity`, `title`, `message` (the detector's fixed sentence), `rule`, `count`, `start_event_id`,
  `evidence_event_ids`, `run_started_at`, `detected_at`, `updated_at`. `facets` = `{severity, detector, project}` of `{value, label, count}`, each counted under
  the other filters but not its own (`label` is the project name). `400 CURSOR_INVALID`, `404 PROJECT_NOT_FOUND` for an unknown or foreign `project_id`.
- `GET /v1/security/detectors[?project_id=]` (`run.read`): `items[]` of `{id, title, description, severities[], version, muted, active}`. Without a project
  `muted` is false and `active` is null for the destination detector (it depends on a project's allowlist).
- `GET /v1/projects/{project_id}/security-settings` and `PUT` (`project.write`: owners and admins): `{project_id, capture_mode, muted_detectors,
  destination_allowlist, updated_at, updated_by, detectors[]}`. `PUT` takes any of `capture_mode` (`FULL | METADATA_ONLY | DISABLED`), `muted_detectors`
  (`sec.*` ids; at most 64) and `destination_allowlist` (host names or `*.suffix`; at most 256); a list given replaces the stored list, omitted fields stay,
  a request that changes nothing writes no audit row. `422 SETTINGS_INVALID` (fixed message, never the value), `404 PROJECT_NOT_FOUND`. A new capture mode applies
  to the next accepted batch; a change to the mutes or the allowlist queues the project's recent runs for re-analysis in the same transaction (at most `ABB_SECURITY_REANALYSE_MAX_RUNS`=200 started within `ABB_SECURITY_REANALYSE_MAX_AGE_DAYS`=30, newest first, coalesced per run, and a wave within `ABB_SECURITY_REANALYSE_COOLDOWN_SECONDS`=60 of the project's previous mute or allowlist change is queued but not due until the cooldown ends; older runs: `python -m abb_api.cli rebuild-findings`), a capture mode change does not. An allowlist entry is a host name of at most 253 characters (punycode for an international name).

### Project quotas (Phase 19a, ADR-154)

- `GET /v1/projects/{project_id}/quotas` (`project.read`: every role; a project-bound key sees only its own project): `{project_id, day, resets_at, enforced, quotas[], updated_at, updated_by}`.
  Each `quotas[]` item is `{quota, period (total|day), limit, default, overridden, used, percent, status}`, in the fixed order `agents`, `runs_per_day`, `events_per_day`, `artifact_bytes`.
  `percent` is the whole percent of the limit used (above 100 after a limit was lowered below use); `status` is `ok`, `near_limit` (from 80 percent) or `at_limit` (new items are being refused).
  `day` and `resets_at` say which UTC day the daily figures belong to. `enforced` is false while `ABB_QUOTAS_ENABLED=false` (nothing checked or counted). `404 PROJECT_NOT_FOUND` for a project that is not in the workspace or not visible to the key.
- `PUT /v1/projects/{project_id}/quotas` (`project.write`: owners and admins; keys and other roles get 403): body `{agents?, runs_per_day?, events_per_day?, artifact_bytes?}`, each a whole number
  within `1..1,000,000`, `1..100,000,000`, `1..100,000,000,000` and `1..1 PiB`, or `null` to remove the override. A field left out stays; unknown fields are 422. Returns the same body as the `GET`.
  Each changed quota writes one audit entry `project.quota.update` (resource: the project; details: `quota`, `old`, `new`, nothing else); a request that changes nothing writes none.
  `422 QUOTA_SETTINGS_INVALID`. The new limit applies to the next request; lowering it below current use refuses new items only.

### Policies (Phase 14, ADR-140)

Policy management. Enforcement is cooperative: a policy governs agents that ask the policy service through the SDK, and says nothing about one that does not.
The document is the §151 YAML (`version: 1`, optional `default`, ordered `policies`; first match wins; ADR-140 lists every key and limit: 64 KiB, 200 rules,
256-character globs). Scopes: the workspace base (omit `project_id`) or one project, whose policy replaces the base for that project. The current policy of a
scope is its highest version; versions are immutable, numbered 1, 2, 3 per scope and never reused. People only: no API key scope grants these actions, and a
cookie write needs the web app's `Origin`.

- `GET /v1/policies/current[?project_id=]` (`policy.read`: owner, admin, developer, security): `{project_id, source, policy}`. `source` is `project`, `workspace`
  or `default` (nothing published: every action is allowed; `policy` is null). `policy` is a version with its text: `{scope, project_id, version, document_sha256,
  rule_count, created_at, created_by, comment, document}`.
- `GET /v1/policies/versions[?project_id=&limit=&before_version=]` (`policy.read`): `{items, next_before_version}`, newest first, `limit` 1-100 (default 50), items
  without `document`. With a project only that project's own versions are listed.
- `GET /v1/policies/versions/{version}[?project_id=]` (`policy.read`): one version with its text; `404 POLICY_VERSION_NOT_FOUND`.
- `GET /v1/policies/diff?from_version=&to_version=[&project_id=]` (`policy.read`): `{project_id, from_version, to_version, diff, truncated}`; `diff` is unified diff
  text of at most 64 KiB (`truncated` says if it was cut); versions over 2,000 lines are `422 POLICY_TOO_LARGE_TO_DIFF`.
- `POST /v1/policies/validate` (`policy.write`: owner, admin) `{document}`: `{valid, issues[], rule_count}`; always 200, no side effects, no audit row.
  An issue is `{line, column, code, message}` (1-based; `code` is one of the 33 in `policies/parser.ISSUE_CODES`; the message is a fixed sentence that may name a key
  and never a value).
- `POST /v1/policies/publish` (`policy.write`) `{document, project_id?, comment?, base_version?}`: `201` with the new version (including `document`). An invalid
  document is `422 POLICY_INVALID` with `error.details.issues` (the same issue objects) and stores nothing, audit row included. `base_version` is the version the
  editor started from (0: none); if the scope's current version differs it is `409 POLICY_VERSION_CONFLICT` with `details.current_version`. One transaction writes the
  version and the audit row `policy.publish` (`details`: `scope`, `version`, `sha256`, `rules`; never the text or the comment). Unknown or foreign `project_id`: `404 PROJECT_NOT_FOUND`.
- `POST /v1/policies/dry-run` (`policy.write`) `{project_id, document}`: replays the project's newest runs through the draft and returns `{runs_considered,
  events_evaluated, events_skipped, truncated, by_decision, by_rule[], samples[], limits}`. `by_decision` is `{allow, deny, require_approval}`; `by_rule` items are
  `{rule_id (null: the default applied), decision, count}`, most frequent first; `samples` (at most 25, in run then time order, only denied or approval-gated
  actions) are `{run_id, event_id, capability, risk_class, decision, rule_id}`: ids and labels only, never a command, path or argument. `truncated` is null,
  `runs` (more than 100 runs), `events` (more than 20,000 mapped events) or `time_budget` (5 s); the counts then cover a prefix. Events map to intents as in
  ADR-140 (`shell.execute`, `filesystem.read`, `filesystem.write`, `github.push`, `tool.<name>`). `504 POLICY_DRY_RUN_TIMEOUT` if the read exceeds 8 s.
- Limits per workspace and API process: 30 publishes, 120 validations and 20 dry runs a minute (`ABB_POLICY_WRITES_PER_MINUTE`, `ABB_POLICY_VALIDATIONS_PER_MINUTE`,
  `ABB_POLICY_DRY_RUNS_PER_MINUTE`); `429 RATE_LIMITED` with `Retry-After`.

### Decisions and approvals (Phase 14 step 2, ADR-141)

An agent asks the policy service whether an action may happen; if a rule needs a person, an **approval** is created, a signed-in approver decides, and the agent spends the
approval for exactly that action once. Enforcement is cooperative (it governs agents that ask). Two kinds of caller, never mixed: **agent routes** need an API key bound to a
project with scope `policy:check` or `events:write` (a session is 403; a workspace-wide key is `PROJECT_KEY_REQUIRED`); **person routes** need a session whose role holds
`approval.read` / `approval.decide` (owner, admin, developer, security; no key scope grants them; viewer and billing are 403). Agent routes only ever see the approvals **their own key**
asked for: any other id is the same `404 APPROVAL_NOT_FOUND`. Request bodies are JSON, at most 64 KiB, unknown fields are refused, and a validation error never echoes a value.

**Agent routes**

- `POST /v1/policy/decide` `{run_id, agent_id, capability, resource?, risk_class? (R0-R4), arguments? (object), trace_id?, idempotency_key?}` -> `200 {decision, policy_id, policy_version,
  reason, on_error, approval_id?, approval_state?, expires_at?, on_expiry?}`. `decision` is `allow`, `deny` or `require_approval`; `policy_id` is the matching rule (null: the default); `reason` is the
  policy author's own text; `on_error` is `open` or `closed` (the rule's, else `closed` for R3/R4 and `open` for R0-R2 and unclassified). `arguments` hold JSON strings, integers, booleans, null, lists and
  objects (8 deep, 512 values, strings 4,096 characters, 24 KiB canonical); a float is `422 INTENT_INVALID` (`details.reason`: `float_not_allowed`, `too_deep`, `string_too_long`, ...). Top-level scalar
  arguments are the attributes a rule's `attributes` match. `trace_id` and `idempotency_key` are not part of the intent hash. The server writes the `policy.decision`, `policy.action.blocked` and
  `approval.requested` events itself. The same intent asked again while its approval is open returns that approval (no duplicate). There is no hash in any response.
- `GET /v1/approvals/{id}/wait[?timeout=0..25]` -> `{approval_id, state, expires_at, on_expiry, policy_id, policy_version, consumed}`. A long poll: at once if the state is no longer `REQUESTED`, else at the first change or after
  `timeout` seconds. `429 TOO_MANY_WAITERS` beyond 20 waiters per workspace or 200 per process (`ABB_APPROVAL_WAITERS_PER_WORKSPACE`, `..._PER_PROCESS`); no database connection is held while waiting.
- `POST /v1/approvals/{id}/consume` `{intent: <the intent as sent to decide>}` -> `200 {approval_id, consumed_at}` once. Refusals (all 409 but the first, none echoes the intent): `404 APPROVAL_NOT_FOUND`,
  `APPROVAL_NOT_APPROVED`, `APPROVAL_EXPIRED`, `APPROVAL_CONSUMED`, `APPROVAL_INTENT_MISMATCH`, and `APPROVAL_POLICY_DENIED` (the policy published now denies the action; nothing is spent, the approval is unchanged). A lost response cannot be retried (the second call is `APPROVAL_CONSUMED`).
- `DELETE /v1/approvals/{id}` -> `204`: withdraw a request that is still waiting (`409 APPROVAL_NOT_REQUESTED` otherwise).
- `GET /v1/policies/fail-modes[?project_id=]` (also `policy.read` roles) -> `{project_id, source, policy_version, rules[{rule_id, capability, resource, risk_classes, has_attribute_conditions, on_error}], defaults}`:
  the rules that set `on_error` explicitly, plus `R0`..`R4` and `unclassified` defaults, for an SDK to cache at startup. Approximate by design (ADR-141 point 9).

**Person routes** (`X-ABB-Workspace`; a cookie write needs the web app's `Origin`)

- `GET /v1/approvals[?state=pending|decided|all&project_id=&limit=1..100&cursor=]` -> `{items[], next_cursor}`; `GET /v1/approvals/pending-count` -> `{count}`; `GET /v1/approvals/{id}` -> the detail
  (`approval.read`). An item is `{id, state, project_id, run_id, agent_id, capability, resource, risk_class, policy_id, policy_version, approver_role, requested_at, expires_at, seconds_left, decided_at, decided_by, consumed}`;
  the detail adds `arguments`, `content_withheld`, `redactions`, `allow_self_approval`, `on_expiry`, `comment`, `closed_at`, `consumed_at`. `resource` and `arguments` are secret-scrubbed (null when the scanner could not clear them); there is no hash.
- `POST /v1/approvals/{id}/decision` `{decision: "approve"|"deny", comment? (<= 1000 characters)}` (`approval.decide`) -> the detail. `403 APPROVER_ROLE_REQUIRED` when the role does not satisfy the rule's `approver_role`
  (unset: owner or admin; `owner`: owner; `admin`: owner, admin; `security`: owner, admin, security; `developer`: owner, admin, security, developer); `403 SELF_APPROVAL_FORBIDDEN` for the person whose key made the request unless
  the rule has `allow_self_approval`; `409 APPROVAL_NOT_REQUESTED` (`details.state`) for the loser of any race and for an approval already decided, withdrawn or expired. One transaction holds the transition, the
  `approval.granted`/`approval.denied` event and the audit row `approval.decide` (`details`: `approval_id`, `decision`, `approver_role`, `policy_id`).

Every state change is an event and an audit row: `approval.consume`, `approval.consume_refused` and `approval.cancel` (actor: the key), `approval.expire` (actor kind `system`). Plain allow decisions are events only. Decision calls (decide, wait start, consume,
withdraw) share a per-key and a per-workspace budget (`ABB_POLICY_DECIDE_PER_MINUTE_KEY`=1200, `..._WORKSPACE`=6000; `429 RATE_LIMITED` with `Retry-After`); at most 100 requests per key and 1,000 per workspace may be waiting
(`429 TOO_MANY_OPEN_APPROVALS`).

### Alerts (Phase 17, ADR-150)

Destinations are where an alert goes, rules say what to watch; both are configured by signed-in people only (no API key scope grants an alert action; a key is `403 INSUFFICIENT_SCOPE`). Every matched event writes a notification row with status `pending`; the delivery worker (ADR-151) sends it. Request bodies refuse unknown fields; a validation error names the field or a fixed reason and never echoes a value. Writes share a per-workspace budget (`ABB_ALERTS_WRITES_PER_MINUTE`, 30; `429 RATE_LIMITED` with `Retry-After`).
Foreign and unknown ids (path, query or body) are the same `404` (`ALERT_DESTINATION_NOT_FOUND`, `ALERT_RULE_NOT_FOUND`, `PROJECT_NOT_FOUND`).

**Roles.** `alert.read` (rules, preview, history): owner, admin, developer, viewer, security. `alert.write` (every change) and `alert.destination.read` (destinations): owner and admin. A rule read without `alert.destination.read` has `destinations: []` and `destination_count`;
a history row shows `destination: {id: null, name: null, type}`; `approval_id` is `null` without `approval.read`; the `destination_id` history filter is `403 PERMISSION_DENIED` (`required_permission: alert.destination.read`).

**Destinations**

- `POST /v1/alerts/destinations` `{name (1-80), type: webhook|slack|email, target: {url} | {recipients[1..10]}}` -> `201 {id: dst_..., name, type, masked_target, state, consecutive_failures, paused_reason, rule_count, created_by, created_at, updated_by, updated_at, signing_secret}`.
  `signing_secret` (`whsec_...`, webhook only, else null) is **returned only here and by a rotation**. Errors: `422 DESTINATION_TARGET_INVALID` (`details.reason`: `url_required`, `url_too_long`, `url_malformed`, `not_https`, `credentials_in_url`, `host_invalid`, `host_internal`, `ip_not_public`, `ip_literal_form`, `port_invalid`, `port_not_allowed` (the port is not in `ABB_ALERTS_ALLOWED_PORTS`, default 443), `fragment_not_allowed`, `recipients_required`, `too_many_recipients`, `recipient_invalid`, `target_shape`),
  `422 EMAIL_NOT_CONFIGURED` (no `ABB_SMTP_HOST`), `409 ALERT_NAME_TAKEN`, `409 ALERT_LIMIT_REACHED` (50 per workspace). Audit `alert.destination.create` (`details`: `type`).
- `GET /v1/alerts/destinations` -> `{items[], email_configured}`; `GET /v1/alerts/destinations/{id}` -> the destination (masked; never a URL, recipient or secret).
- `PATCH /v1/alerts/destinations/{id}` `{name?, target?, state?: active|paused, rotate_signing_secret?}` -> the destination with `signing_secret` set only when rotated. A new target is re-validated, resets the failure count and moves `failing` to `active`;
  `state: paused` sets `paused_reason: manual`, `active` resumes and clears failures; rotation on a non-webhook is `422 DESTINATION_INVALID` (`reason: no_signing_secret`). Audit rows `alert.destination.update` (`changed`: `["name","target"]`), `.pause`, `.resume`, `.rotate_secret`.
- `POST /v1/alerts/destinations/{id}/test` (no body; owner/admin, `alert.write`) -> `200 {ok: bool, error_code: string|null}`. Sends a fixed `kind: test` alert (no project, agent or content; link to the Alerts page) through the same SSRF-safe sender as real deliveries,
  synchronously within the 5 s delivery deadline (email: the SMTP timeout). `error_code` is one of the fixed delivery codes (`timeout`, `connect_failed`, `dns_failed`, `tls_failed`, `blocked_address`, `blocked_port`, `http_3xx`, `http_4xx`, `http_5xx`, `bad_response`, `smtp_failed`, `config_unreadable`, `internal_error`);
  what the target answered is never returned. The HTTP status is `200` whether or not the target accepted it. A test does not change `state` or `consecutive_failures` and creates no history row; it works on a paused destination. `404 ALERT_DESTINATION_NOT_FOUND`
  (foreign and unknown ids alike), `403` for other roles and API keys, `429 RATE_LIMITED` (10 a minute per workspace per process, plus the write budget). Audit `alert.destination.test` (`type`, `ok`, `error_code`).
- Every alert write (create, change, pause, rotate, delete of a destination or rule; send test's audit) fails fast with `409 ALERT_BUSY` (`retryable`) when a row lock it needs is held by a running delivery tick for longer than `ABB_ALERTS_LOCK_TIMEOUT_SECONDS` (3 s); nothing is changed and the same request can be repeated.
- `DELETE /v1/alerts/destinations/{id}` -> `204`. Waiting notifications become `skipped` (`destination_deleted`), history keeps its rows (destination cleared), rules left with no destination are switched off. Audit `alert.destination.delete` (`type`, `rules_disabled`).

**Rules**

- `POST /v1/alerts/rules` `{name, trigger, project_id?, filters, destination_ids[1..10], cooldown_seconds? (0..604800, default 0), enabled? (default true)}` -> `201` rule `{id: rul_..., name, trigger, project_id, filters, cooldown_seconds, enabled, last_fired_at, destination_count, destinations[{id, name, type, state}], created_*/updated_*}`.
  Triggers and their `filters` (a field outside the trigger's set is `422 RULE_INVALID` with `details: {field, reason: not_applicable|required|unknown_agent|too_small}`): `approval_requested` (`agent`); `approval_expiring` (`agent`, `expiring_lead_seconds` 60..86400, required);
  `security_finding` (`agent`, `min_severity`: info|warning|medium|high|critical, required); `run_failed` (`agent`, `run_statuses` subset of FAILED, TIMED_OUT, BLOCKED, default all three); `spend_over` (`agent`, `threshold_usd` > 0 and <= 1,000,000, required, `period: day`).
  `agent` is an agent slug that exists (in the project, or any project when `project_id` is null). `project_id: null` means every project of the workspace. `409 ALERT_NAME_TAKEN`, `409 ALERT_LIMIT_REACHED` (100 rules, 10 destinations per rule). Audit `alert.rule.create` (`trigger`, `destinations`).
- `GET /v1/alerts/rules`, `GET /v1/alerts/rules/{id}`. `PATCH /v1/alerts/rules/{id}` (any of `name`, `project_id` (null clears), `filters` (replaces), `destination_ids` (replaces), `cooldown_seconds`, `enabled` (false mutes); the trigger cannot change). Audit `alert.rule.update` (`changed`: field names). `DELETE /v1/alerts/rules/{id}` -> `204` (waiting notifications become `skipped`, `rule_deleted`; audit `alert.rule.delete`).
- `GET /v1/alerts/rules/{id}/preview?days=1..30` (default 7) -> `{days, count, daily[{day, count}]}`: how many times the rule would have fired, from recorded findings, approvals, runs and daily spend, before cooldown; counts only; `504 ALERT_PREVIEW_TIMEOUT` past 5 s.

**History**

- `GET /v1/alerts/notifications[?rule_id=&destination_id=&status=pending|delivered|failed|dead|skipped&trigger=&since=&until=&limit=1..200&cursor=]` -> `{items[], next_cursor}`, newest first. An item is `{id: alt_... (the stable alert_id receivers dedupe on), rule: {id, name}|null, destination: {id, name, type}|null, trigger, severity, status, skip_reason (cooldown|destination_paused|destination_deleted|rule_deleted|rate_limited|daily_cap), attempts, error_code (a fixed code), project_id, run_id, finding_id, approval_id, agent_slug, created_at, delivered_at, next_attempt_at}`.
  There is no payload and no response body in the history; the payload (stored in `alert_notifications.payload_json`) is content-free and is read only by the sender.

**Delivery (ADR-151).** A worker sends `pending` notifications: webhook (`POST` JSON = the payload plus an absolute `link`, headers `X-ABB-Alert-Id: alt_...`, `X-ABB-Delivery-Attempt`, `X-ABB-Timestamp`, `X-ABB-Signature: v1=<hex HMAC-SHA256(signing secret, timestamp + "." + body)>`),
Slack (an incoming-webhook message), email (operator SMTP, plain text plus HTML). Delivery is at-least-once: dedupe on `alert_id`. `status`: `pending` (with `attempts > 0` and a future `next_attempt_at` between retries), `delivered`, `dead` (retries used up or a permanent failure; `error_code` says why), `skipped`.
`next_attempt_at` is meaningful only while `pending`. A destination's health is `state` (`active`, `failing` from 3 consecutive failed attempts, `paused` from 10 with `paused_reason: failures`), `consecutive_failures`.

**What produces notifications.** A run summarised as FAILED, TIMED_OUT or BLOCKED; a security finding that is new or worse than the stored one; an approval requested; (every 60 s, per workspace with such a rule) an approval whose time to expiry has fallen to the rule's lead, and a UTC day whose spend for the rule's project and agent is strictly over the threshold.
Dedupe keys (one notification per rule, destination and key): `run_id:STATUS`, `finding_id:detector_version:severity`, `approval_id:requested`, `approval_id:expiring`, `rule_id:YYYY-MM-DD`. A cooldown suppresses new notifications for the same rule and destination as `skipped` rows (`cooldown`).

### Search (`GET /v1/runs?q=`)

`q` is a flat conjunction of terms separated by whitespace, parsed on the server into a typed AST (spec §152; the web app sends the text and renders what the server parsed):

```text
term  := '-'? ( field ':' op? value | value )          op := '>=' | '<=' | '>' | '<'  (numeric and time fields)
value := '"' ... '"'  (escapes \" and \\) | bare word up to whitespace;  a trailing '*' = prefix
```

A value without a field is free text (quoted = phrase); a leading `-` negates the term once (`--force` is NOT the word `force`; a quoted value is never negation; after an operator `-` is part of the
value). Names and enum values are case-insensitive; text is NFKC-normalised and case-folded. There is no `OR`, no parentheses and no `NOT` keyword. Example:
`status:failed tool:github retries:>=2 started:>=-7d "npm test"`.

| Field | Matches |
| --- | --- |
| `status` | the run status (case-insensitive) |
| `agent`, `root_agent` | any agent of the run (children included), or the run's first agent; `*` prefix |
| `name` | the run name contains (case-insensitive) |
| `cost` (USD), `retries`, `errors`, `duration` (`ms|s|m|h`, a unit is required) | numeric comparison; `severity:error` is `errors:>0` |
| `started` | a date (`2026-10-01`: that UTC day for `=`, midnight for comparisons), an RFC 3339 instant (`Z` recommended; send `+` offsets as `%2B`), or a relative time `-<n><m|h|d|w>` resolved by the server at request time (the echo shows the instant), so a bookmarked `started:>=-24h` stays relative |
| `event`, `tool`, `model`, `provider`, `tag` | the run has such a value; `*` prefix (`event:shell.*`) |
| `security` | `true` or `false`: the run has security-class events |
| `file` (content), `command` (content) | glob over file paths (`src/auth/**` matches `/home/x/src/auth/login.ts`; `*.ts`; `/etc/passwd` is exact; `*` and `**` cross `/`; `?` is one character); case-insensitive substring over command lines |
| free text | all words present in the run's metadata document (name, tags, agents, event types, tools, models, providers, `span.name`, `tool.operation`, ... and the string leaves of the run's `metadata`); with `payload.read` also the captured commands, paths and other content; quoted = adjacent words; `*` on the last word is a prefix |

Not fields: `finding:` and `verdict:` are reserved (Phases 10, 11) and refused like any unknown field. A `name:` prefix that is not a catalogue field is `422` (quote values such as `"http://x"`).
Limits: 512 characters, 16 terms, 8 text terms, a value of 256 characters, 16 words in a text term, 8 wildcards in a glob, relative times up to 3,660 days; control characters are refused.

**Response.** Whenever `q` is sent the page gains `query` (`terms[]` with `kind`, `field`, `op`, `value`, `lexemes`, `phrase`, `prefix`, `negated` and `span` = `[start, end)` character offsets; `fields`,
the catalogue this caller may search; `content_allowed`; `unindexed`, `unindexed_capped`). When `q` has at least one term the page also has `facets` (`status`, `agents`, `tools`, `models`,
`event_types`: the top ten values with counts over the newest 1,000 matching runs; `null` with `facets_skipped: "budget"` when the time budget ran short) and each run that matched on indexed
fields has `matched` (the tools, models, event types, agents and tags the query named, and `metadata_truncated`; `content_truncated` only with `payload.read`). Results are newest first; there is
no total count and no relevance ranking (by design: they would be oracles, ADR-101). An empty `q` executes exactly the statements of the request without `q` (plus the catalogue echo).

**Indexing and the inner join.** Search reads a derived per-run document written by the summarizer (ADR-100). A query that uses an *index term* (tools, models, agents, event types, tags,
errors, severity, security, file, command or free text) inner-joins it: a run with no document yet (queued, or summarised before `SUMMARY_VERSION` 4) matches neither a positive nor a
negated index term. A query with only run-row terms (`status`, `cost`, `retries`, `duration`, `started`, `name`, `root_agent`) sees every run. `query.unindexed` counts the runs of the
request's scope with no document (capped at 1,000, `unindexed_capped`); `python -m abb_api.cli search-rebuild` adds them (`docs/runbooks/search.md`). A null column never matches, negated or not
(`-duration:>60s` does not return running runs).

**Permissions (ADR-101).** An actor may search a field only if they receive that field's values: `file:` and `command:` need `payload.read`. For anyone else the request is `403 PERMISSION_DENIED`
with `details.field` (the catalogue's name, never the typed spelling and independent of any data), logged and audited like every denial, and no run statement is executed. Free text then
searches the metadata document only, so content words give the same items, facets and cursor as a nonsense word. A BILLING person is refused at `run.read` before the query is parsed.

**Errors.** `422 QUERY_INVALID` (`details.position`, `details.reason`; positions depend only on the text), `403 PERMISSION_DENIED` (above), `503 SEARCH_BUSY` (retryable, `Retry-After: 1`, fixed `details.reason`: this actor's or workspace's
fair share of this process's search permits, or all of them, is in use) and `503 SEARCH_TIMEOUT` (retryable: the statement exceeded the time budget). The web proxy refuses an encoded query string over 2,048 characters with `414 QUERY_TOO_LONG`
(a legal 512-character `q` of non-ASCII text can exceed it); the UI shows it like a 422 without a caret. Search is per request bounded: at most two statements, a read-only transaction, the budget
`ABB_SEARCH_TIMEOUT_SECONDS` (5) and `ABB_SEARCH_MAX_CONCURRENT` (4) permits per process.

## Evaluations, comparison and experiments (Phase 11)

Fifteen operations (ADR-090, ADR-091, ADR-092). All take `X-ABB-Workspace` for people; a project-bound key sees the evaluators of its project (or workspace-wide
ones), the experiments of its project, and compares runs of its project only. Foreign and random ids answer one uniform `404` (`EVALUATOR_NOT_FOUND`,
`EVALUATION_NOT_FOUND`, `EXPERIMENT_NOT_FOUND`, `VARIANT_NOT_FOUND`, `RUN_NOT_FOUND`). Writes by people carry the web app's `Origin` (as everywhere).

| Operation | Action | Notes |
| --- | --- | --- |
| `GET /v1/evaluators?include_archived=` | `evaluation.read` | `EvaluatorOut`: `id` (`evr_`), `name`, `kind` (`deterministic`, `metric`, `human`, `llm_judge`), `implementation`, `version`, `config`, `project_id`, `archived_at`; `ground_truth` is derived (`false` only for `llm_judge`). For actors without `payload.read` the path-bearing config fields (`allowed_globs`, `forbidden_globs`, `required_paths`) and `rubric` are `null` and named in `config_withheld` |
| `POST /v1/evaluators` | `evaluator.write` | `{name, kind, implementation, config, description?, project_id?}`. Built-ins: `run_succeeded`, `tests_passed`, `files_touched_only`, `no_policy_blocks`, `expected_files_changed`, `cost_under`, `latency_under`, `tool_error_rate_under`, `retries_under`, `llm_calls_under`, `human`; `judge` for `llm_judge`. `422 EVALUATOR_INVALID` (unknown implementation, invalid or oversized config; names fields, never values), `409 EVALUATOR_NAME_TAKEN`, `409 JUDGE_NOT_CONFIGURED` (`ABB_JUDGE_PROVIDER=none`), `409 LIMIT_REACHED` |
| `DELETE /v1/evaluators/{id}` | `evaluator.write` | Archives (204); results stay |
| `GET /v1/runs/{run_id}/evaluations` | `evaluation.read` | `{items, truncated}`, newest first, at most 20 rows per (run, evaluator); `latest` marks the newest row of each evaluator. `EvaluationOut`: `state` (`pending`, `running`, `completed`, `failed`), `score` (0..1 or null), `label`, `explanation`, `evidence_event_ids`, `metrics` (allowlisted keys per implementation), `metadata`, `error_code`, `error`, `evaluator_name`/`evaluator_version` (snapshots), `ground_truth`, timestamps. A `human` or `llm_judge` explanation and `metadata.raw` are `null` with `explanation_withheld` / `metadata_withheld` for actors without `payload.read`; built-in explanations are templates of numbers and are always visible |
| `POST /v1/runs/{run_id}/evaluations` | `evaluation.run` | `{evaluator_id}` -> `202` with the `pending` row; a second trigger while a row is pending returns that row (`200`, no new job); after completion a trigger adds a row; the 21st is `409 LIMIT_REACHED`. `422 EVALUATOR_KIND_MISMATCH` (a human evaluator), `422 EVALUATOR_PROJECT_MISMATCH`, `409 EVALUATOR_ARCHIVED`. Computed by the `evaluate_run` job |
| `POST /v1/runs/{run_id}/verdicts` | `evaluation.label` | `{evaluator_id, label, score?, explanation?}` for a `human` evaluator; `completed` at insert; audited without its explanation; `422 EVALUATOR_KIND_MISMATCH` otherwise |
| `GET /v1/runs/{run_id}/compare?with_run_id=` | `run.read` (both runs) | See below |
| `GET /v1/experiments?project_id=&include_archived=` | `experiment.read` | `ExperimentSummaryOut` (`id` `exp_`, `name`, `description`, `dataset_label`, `variant_count`, `run_count`) |
| `POST /v1/experiments` | `experiment.write` | `{name, project_id, description?, dataset_label?, evaluator_ids}` (up to 20 evaluators of the workspace); `409 LIMIT_REACHED` |
| `GET /v1/experiments/{id}` | `experiment.read` | `ExperimentOut`: `variants`, `warnings`, `metrics`, `runs`, `evaluators` (below) |
| `DELETE /v1/experiments/{id}` | `experiment.write` | Archives (204) |
| `POST /v1/experiments/{id}/variants` | `experiment.write` | `{name, role: baseline|candidate, config}`; `config` is a flat map of at most 32 keys with string values of at most 256 characters (8,192 bytes in total). Variant configs are labels visible to every reader: do not put secrets, prompts or paths in them. `409 BASELINE_EXISTS`, `409 BASELINE_REQUIRED` (a candidate needs the baseline first) |
| `POST /v1/experiments/{id}/runs` | `experiment.write` | `{run_id, variant_id}`; at most 500 runs per experiment (`409 LIMIT_REACHED`); `422 RUN_PROJECT_MISMATCH`; a run joins an experiment once |
| `DELETE /v1/experiments/{id}/runs/{run_id}` | `experiment.write` | Detaches (204) |
| `POST /v1/experiments/{id}/evaluate` | `evaluation.run` | Starts every experiment evaluator on every attached run: a (run, evaluator) pair is started when it has no non-terminal and no completed row and fewer than 20 rows (a failed pair is retried). `202 {enqueued, remaining, jobs, at_limit, skipped_evaluators[]}`: at most 500 new rows per call (call again while `remaining > 0`), one `evaluate_run` job per run delayed by `ABB_EVAL_BATCH_DELAY_SECONDS`; archived, human, unconfigured-judge or cooling-down judge evaluators are skipped with a reason (`cooling_down`: a judge refused after an expensive reply; retrying would only add refusal rows); a repeat while rows are pending enqueues nothing. The body is empty; the web proxy still requires `Content-Type: application/json` on POST, so the browser sends `{}` |

### Evaluation states

An evaluation is a derived record with its own state, never part of the run (INV-2 and the roadmap rule "evaluator failures never change run status"): the
handler runs the evaluator in a worker thread under a cooperative budget (`ABB_EVAL_TIMEOUT_SECONDS`, 25 s, which must be below half the worker lease), claims
its `pending` rows with `FOR UPDATE SKIP LOCKED` and writes only to `evaluations`; it never locks or updates `runs` and enqueues no summary. Failures
become `failed` rows with a fixed-text `error_code` (`TIMEOUT`, `RUN_TOO_LARGE` above `ABB_EVAL_MAX_EVENTS`, `INVALID_RESULT`, `JUDGE_BUDGET`, `JUDGE_COOLDOWN`, `JUDGE_INPUT`, `JUDGE_UNAVAILABLE`,
`EVALUATOR_ERROR`, and `JOB_FAILED` for a pending row whose job was dead-lettered or that is older than `ABB_EVAL_STALE_SECONDS`); `error` carries only a built-in's own
`EvaluatorError` text, never a value from the run. Terminal rows are immutable (a database trigger refuses updates). Such a stale `pending` row reads as `failed/JOB_FAILED`; a read writes nothing, and the next trigger of that evaluator makes the state real (`pending -> failed`) before it adds a fresh row, so a row once reported failed never completes. A model judge's row has `ground_truth: false` and `metadata.judge_model`; the prompt is never stored or returned.

### Comparison

`GET /v1/runs/{run_id}/compare?with_run_id=` compares two runs of the caller's scope (either order; a run with itself is allowed) from the actor's own projection:

- `left`, `right`: `{run_id, status, steps, truncated, truncated_at_event}`; a run longer than `ABB_COMPARE_MAX_EVENTS` (20,000 events) is cut and says so.
- `alignment`: `content` (with `payload.read`: steps align on the family and a normalised name, command text and relative paths included) or `metadata` (kinds, tool names,
  model names only; labels are `null` with `label_withheld`). Every relation, the divergence and the files are computed from that projection, so a viewer can see a
  different divergence than an owner; the UI says so.
- `alignment_quality`: `full`, or `partial` when the middle needed more than `ABB_COMPARE_MAX_EDITS` edits (600; ADR-092): the common prefix is exact, so the first
  divergence is exact, and the rest is one `left_only` and one `right_only` block. Steps are capped at `ABB_COMPARE_MAX_STEPS` (2,000) per side.
- `steps[]`: `{relation: equal|changed|left_only|right_only, left, right}` with `StepOut` (`event_id`, `span_id`, `family`, `event_type`, `status`, `duration_ms`,
  `offset_ms`, `label`, `label_withheld`). `changed` is an aligned pair whose status or projected label differs.
- `divergence`: `{index, relation}` (the row is `steps[index]`) or `null` when every row is `equal`; `first_outcome_difference`: the index of the first aligned pair whose
  status differs, or `null`.
- `summary[]`: `{metric, left, right, delta}` for `duration_ms`, `estimated_cost_usd`, `retry_cost_usd`, `llm_calls`, `tool_calls`, `retry_count`, `error_count`,
  `files_modified`, `input_tokens`, `output_tokens` (`delta` is right minus left, `null` when either side is unknown).
- `cost_by_model[]`, `evaluations[]` (per evaluator on either run: the latest completed `{score, label}` per side and `delta`; no explanations), and `files`:
  `{only_left, only_right, both}` for `content`; for `metadata` only `{left_count, right_count, paths_withheld: true}` (an intersection would be a membership oracle).

### Experiments

`ExperimentOut.variants[]` carry `controlled` and `changed_variables` against the baseline (a key that is missing counts as different; spec §86 single-variable
rule: `controlled` is true when exactly one differs; both are `null` on the baseline). `warnings[]`: `not_single_variable`, `variant_models_vary`,
`variant_agents_vary`, `unevaluated_runs`. `metrics[]` per variant (finished runs only; the latest terminal result per run and evaluator): `runs`, `finished`,
`success_rate`, `cost_usd` and `duration_ms` as `{mean, median, p95}`, `retry_rate`, `tool_calls_mean`, `failure_categories` and `evaluations[]`
(`completed`, `failed`, `mean_score`, `pass_rate` per evaluator). The statement runs under the analytics statement timeout.

## Cost and analytics

All endpoints need `runs:read`. A project-bound key is confined to its project: `project_id` of another project is
`404 PROJECT_NOT_FOUND`; a workspace-wide key may name any project of its workspace or none. Windows are `from`/`to`
(ISO 8601; naive times are UTC; default today and the six days before, at most 92, else `422 INVALID_WINDOW`) and are
snapped outward to whole UTC days; a run belongs to the day it started in. Figures come from daily rollups (ADR-043) that lag
changes by up to `ABB_ANALYTICS_REFRESH_DELAY_SECONDS` (default 60); percentiles are histogram-based, within one bucket, about 10%. Grouped lists return the top `top` groups (default 10, max 50) plus an `*_other` bucket.
A query is cut off after `ABB_ANALYTICS_TIMEOUT_SECONDS` (default 10) with `503 ANALYTICS_TIMEOUT` (retryable).

| Endpoint | Returns |
| --- | --- |
| `GET /v1/analytics/summary` | run counts, success/failure/timeout/retry rates, cost headline (total, per run, per successful run, retries, unpriced calls), run latency p50/p95, behaviour averages, tool and model-call success, active agents |
| `GET /v1/analytics/cost` | headline, retry breakdown (initial vs retry cost, spec §24), cost by day, agent, model, source and project, most expensive runs |
| `GET /v1/analytics/reliability` | rates, failure trend by day, tool success and p95, retry-heavy runs |
| `GET /v1/analytics/performance` | p50/p95 for runs, model calls and tools; slowest operations (tool and model spans by name, other kinds by kind) |
| `GET /v1/analytics/waste` | where the money was wasted: spend on failed, timed-out and blocked runs plus retries in the other runs, spend after the first error, by agent and by model; read live (ADR-113) |
| `GET /v1/pricing` | the price entries the engine uses (built-in, illustrative, plus the caller's overrides) |

Definitions: finished = success, failed, timed out or blocked (cancelled runs are counted separately and excluded
from rates); `success_rate` = success / finished; `failure_rate` = (failed + blocked) / finished; `timeout_rate` =
timed out / finished; `retry_rate` = runs with a retry / runs. Rates are `null`, not 0, when nothing can be
divided. Money is USD rounded to 9 places. Every cost line states its source: `provider_reported` (event attribute
`cost.provider_usd`), `estimated` (tokens x a versioned price, `pricing_version` stored per line), `client_estimate`
(`cost.estimated_usd`) or `unpriced` (counted as $0 and reported). `input_tokens` includes cached tokens. A model
call is retry cost when it follows a `retry.attempted` event of its span or an ancestor span (ADR-042).
Attribution (Phase 9): `cost.by_agent` groups cost lines by the **event's** agent (a child's model calls appear under the child); run counts, success rates, expensive/retry-heavy runs and `active_agents` use the **run's** root agent. Per-agent run metrics are KI-084. The cost headline's and the waste report's `unrebuilt_runs` count runs summarised below the current `summary_version` (including runs that predate cost lines; first-error facts are missing too).
The failure trend uses the same definitions per day (`failure_rate` and `timeout_rate`). "Running agents" on the summary
is read from the live `runs` table (current state, not a rollup; ADR-041 allows this one status-indexed lookup). Retry cost is
an estimate (ADR-042). Adapters (Phase 8) must report `llm.input_tokens` including cached tokens, or costs will be
mis-split. Numbers in attributes must be within +-2^53 (floats included) or the event is rejected.
Prices: `python -m abb_api.cli set-pricing-override` then `rebuild-costs` (ADR-040); built-in prices are illustrative (KI-050).

## Live streaming

`GET /v1/runs/{id}/stream` (action `run.read`, scope `runs:read`, same visibility rules as the other run reads) returns `text/event-stream`. Design and
trade-offs: ADR-022.

| Message | Meaning |
| --- | --- |
| `retry: 3000` | first line: the browser waits 3 s before reconnecting |
| `id: evt_...` `event: trace_event` `data: {...}` | one event, in the list endpoint's shape **without payload** (`has_payload` says if one exists) |
| `event: run_end` `data: {"reason":"run_finished"}` | the run has a terminal `run.*` event and nothing arrived for 5 s: do not reconnect |
| `event: error` `data: {"error": {...}}` | `STREAM_UNAVAILABLE`, retryable: the stream ends, reconnect with `Last-Event-ID`; or `STREAM_UNAUTHORIZED`, not retryable: the credential, session or membership no longer holds (a reconnect gets the precise 401/403/404) |
| `: keepalive` / `: open` / `: max-lifetime...` | comments: idle keep-alive every 15 s, stream opened, closed at the maximum lifetime (15 min) |

**Order and duplicates.** Events arrive in *arrival* order, not canonical order. Clients must de-duplicate by `event_id` and sort
themselves (`abb_event_schema.ordering`; the web app does, with a golden-file parity test). The server deliberately repeats a window of
events it already sent (the last `ABB_STREAM_OVERLAP_SECONDS`, default 30 s) on every poll boundary and reconnect, because a transaction can
become visible after a later one.

**Resume.** Send `Last-Event-ID: evt_...` (browsers do this on reconnect) or, for a first connection, `?last_event_id=evt_...`; the header
wins. The server re-reads from that event's arrival time minus the overlap. An id that is malformed, unknown or from another run means "from
the start of the run". Without either, the whole run is replayed, then followed live.

**Errors before the stream starts** are ordinary JSON envelopes: `401`, `403`, `404 RUN_NOT_FOUND`, `422`, and
`429 STREAM_LIMIT` (with `Retry-After`; per key or per person `ABB_STREAM_MAX_PER_KEY`=10, `details.scope` is `key`, `user` or `server`; per API process `ABB_STREAM_MAX_TOTAL`=50). EventSource cannot send headers, so people select the workspace with `?workspace=ws_...` (the web proxy turns it into `X-ABB-Workspace`).

**Lifecycle.** A stream ends at `run_end`, at its maximum lifetime, when the client disconnects, or after a 10 s write timeout against a
client that stopped reading. Late events after `run_end` appear on a new connection. Every `ABB_STREAM_REAUTH_SECONDS` (default 30) the stream re-checks that its key (active, same scope) or session (not revoked or expired, person still a member with `run.read`) still holds and otherwise ends with `STREAM_UNAUTHORIZED`; the re-check never extends a session.

## Events and spans

`GET /v1/runs/{id}/events` returns events in **canonical order** (the same total order as
`abb_event_schema.ordering.sort_events`): by `(sequence, occurred_at, received_at, event_id)` when the run's
`ordering_mode` is `sequence`, otherwise by `(occurred_at, sequence, received_at, event_id)`. Filters:
`event_type` (repeatable; well-formed names only), `status` (repeatable; `success|error|timeout|cancelled|blocked`), `span_id`; `limit` 1-500 (default 100). The page includes
`ordering_mode`; cursors embed it, so a cursor from before a mode change gets `409 CURSOR_STALE`.
Payloads are **not** in lists (`has_payload` says whether one exists); `GET /v1/runs/{id}/events/{event_id}`
returns the full event with its inline payload (or `payload: null`, `payload_withheld: true` to an actor without `payload.read`; their `attributes`
also omit command text and file paths, see `withheld_attributes` under Authorization).
Events (lists and detail) also carry `cost_usd` and `cost_unpriced` (ADR-121): the figure the cost engine settled for a completed model call
(provider-reported, else price book, else the caller's estimate), so the events of a run add up to the summary's `estimated_cost_usd`. They are
`null`/`false` for every other event, for a run whose cost lines are not derived yet, and on stream frames. Metadata for every role.

`GET /v1/runs/{id}/spans` returns derived spans (`parent_span_id`, `kind`, `status`, timings, `event_count`)
by start time (spans that never saw a start event come last), `limit` 1-2000 (default 500). It is empty until
the first summarization.

# HTTP API v1

Reference for the public ingestion and query API. The machine-readable contract is
[`apps/api/openapi.json`](../../apps/api/openapi.json) (regenerate with `make openapi`; the quality
gate fails if it is stale). Event bodies are defined by the [event contract](events.md). Spec: §41, §71,
§101; decisions: ADR-002, ADR-006, ADR-012.

## Authentication

`Authorization: Bearer abb_live_<key_id>.<secret>`. Keys are issued by the CLI
(`python -m abb_api.cli create-key`) and carry scopes and an optional project:

| Scope | Allows |
| --- | --- |
| `events:write` | `POST /v1/events`, `POST /v1/events/batch`, `POST /v1/runs` (needs a project-bound key) |
| `runs:read` | every `GET` below |

A **project-bound** key only ever sees its own project. A **workspace-wide** key (no project) can read
every project of its workspace but cannot ingest. Unknown, malformed, wrong-secret, revoked and expired
keys all return the same `401 API_KEY_INVALID` (with `WWW-Authenticate: Bearer`). A valid key without the
needed scope gets `403 INSUFFICIENT_SCOPE`. Resources of another workspace, or of another project for a
project key, are `404`, indistinguishable from nonexistent ones.

## Errors

Every non-2xx response, from every route, has this shape and an `X-Request-ID` header (also in the body):

```json
{"error": {"code": "RUN_NOT_FOUND", "message": "Run not found.", "category": "NOT_FOUND",
           "retryable": false, "request_id": "req_...", "details": {}}}
```

| Code | Status | Meaning |
| --- | --: | --- |
| `API_KEY_INVALID` | 401 | missing, malformed, unknown, revoked or expired key |
| `INSUFFICIENT_SCOPE` | 403 | `details.required_scope` names the missing scope |
| `PROJECT_KEY_REQUIRED` | 403 | ingestion or run creation with a workspace-wide key |
| `RUN_NOT_FOUND`, `EVENT_NOT_FOUND`, `PROJECT_NOT_FOUND` | 404 | absent or not visible to this key |
| `RUN_PROJECT_MISMATCH` | 409 | the run id belongs to another project (per event in a batch) |
| `CURSOR_INVALID` | 400 | malformed cursor, or one from another listing |
| `CURSOR_STALE` | 409 | the run's ordering mode changed while paging; restart (retryable) |
| `BATCH_INVALID` | 400 | not JSON, no `events` array, empty, too many events, bad `batch_id`, broken gzip |
| `EVENT_INVALID` | 422 | single-event endpoint: the event failed validation (`details.issues`) |
| `EVENT_SCHEMA_UNSUPPORTED` | 400 | single-event endpoint: unsupported `schema_version` major |
| `PAYLOAD_TOO_LARGE` | 413 | body (compressed or decompressed) over 5 MiB, or an event over 256 KB |
| `UNSUPPORTED_MEDIA_TYPE` / `UNSUPPORTED_ENCODING` | 415 | not `application/json`; `Content-Encoding` other than gzip/identity |
| `REQUEST_INVALID` | 422 | invalid query parameter or run-creation body |
| `RATE_LIMITED` | 429 | retryable; honour `Retry-After` |
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
- Per-project rate limit: 2,000 events/s (burst 10,000) and 10 MiB/s by default, per API process
  (`ABB_RATE_LIMIT_*`); exceeding it returns `429` and stores nothing from that request.

## Runs

`POST /v1/runs` (optional; the first event of an unseen run creates it anyway) creates a `QUEUED` run:
`{run_id?, trace_id?, name?, agent_id?, metadata?}`; `201` when created, `200` when it already existed;
idempotent on `run_id`; `metadata` is at most 16 KB and 8 levels deep.

`GET /v1/runs` lists newest first (`sort=started_at` for oldest first) with `status` (repeatable), `agent_id`,
`project_id`, `started_after`, `started_before` (RFC 3339 with offset), `limit` (1-200, default 50) and
`cursor`. Paging is keyset-based, so runs arriving between page requests never cause repeats or gaps.

`GET /v1/runs/{id}` returns the run: `status`, `name`, `agent_id`, `trace_id`, `started_at`, `completed_at`,
`duration_ms`, `ordering_mode`, `summary`, `summary_version`, `summary_state`, `metadata`.

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
| `name`, `agent_id`, `trace_id` | `run.name` of the earliest `run.started` (an explicit name from `POST /v1/runs` is kept unless events name the run); agent and trace of the earliest event |
| `ordering_mode` | `sequence` if every event has a `sequence`, else `time` |

`summary` (version 1): `event_count`, `duration_ms`, `llm_calls` (`llm.request.completed|failed`), `tool_calls`
(`tool.call.completed|failed`), `input_tokens` and `output_tokens` (sums over `llm.request.completed`),
`estimated_cost_usd` (sum of `cost.estimated_usd`; the pricing engine arrives in Phase 7), `retry_count`
(`retry.attempted`), `error_count` (events with status `error`/`timeout` or a `*.failed` type, each counted
once), `files_modified` (distinct `file.path` of created/modified/deleted), `models`, `first_event_at`, `last_event_at`.

## Events and spans

`GET /v1/runs/{id}/events` returns events in **canonical order** (the same total order as
`abb_event_schema.ordering.sort_events`): by `(sequence, occurred_at, received_at, event_id)` when the run's
`ordering_mode` is `sequence`, otherwise by `(occurred_at, sequence, received_at, event_id)`. Filters:
`event_type` (repeatable), `status` (repeatable), `span_id`; `limit` 1-500 (default 100). The page includes
`ordering_mode`; cursors embed it, so a cursor from before a mode change gets `409 CURSOR_STALE`.
Payloads are **not** in lists (`has_payload` says whether one exists); `GET /v1/runs/{id}/events/{event_id}`
returns the full event with its inline payload.

`GET /v1/runs/{id}/spans` returns derived spans (`parent_span_id`, `kind`, `status`, timings, `event_count`)
by start time (spans that never saw a start event come last), `limit` 1-2000 (default 500). It is empty until
the first summarization.

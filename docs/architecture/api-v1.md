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
| `PAYLOAD_TOO_LARGE` | 413 | body (compressed or decompressed) over 5 MiB; on the single-event endpoint also an event over 256 KB (inside a batch an oversized event is a per-event `EVENT_TOO_LARGE` entry in `errors`) |
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
`project_id`, `started_after` (inclusive), `started_before` (exclusive) (RFC 3339 with offset; values with no UTC
representation are `422`), `limit` (1-200, default 50) and
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

`summary` (version 2): `event_count`, `duration_ms`, `llm_calls` (`llm.request.completed|failed`), `tool_calls`
(`tool.call.completed|failed`), `input_tokens` and `output_tokens` (sums over `llm.request.completed`),
`estimated_cost_usd` (the effective cost: sum over model calls of the cost line, ADR-040; name kept for compatibility), `retry_cost_usd`, `initial_cost_usd`, `cost_by_source_usd`, `unpriced_calls`, `tool_spans_ok/finished`, `llm_spans_ok/finished`, `retry_count`, `retries_unattributed`
(`retry.attempted`), `error_count` (events with status `error`/`timeout` or a `*.failed` type, each counted
once), `files_modified` (distinct `file.path` of created/modified/deleted), `models`, `first_event_at`, `last_event_at`.

## Cost and analytics

All endpoints need `runs:read`. A project-bound key is confined to its project: `project_id` of another project is
`404 PROJECT_NOT_FOUND`; a workspace-wide key may name any project of its workspace or none. Windows are `from`/`to`
(ISO 8601; naive times are UTC; default today and the six days before, at most 92, else `422 INVALID_WINDOW`) and are
snapped outward to whole UTC days; a run belongs to the day it started in. Figures come from daily rollups (ADR-043) that lag
changes by up to `ABB_ANALYTICS_REFRESH_DELAY_SECONDS` (default 60); percentiles are histogram-based, within about 5%. Grouped lists return the top `top` groups (default 10, max 50) plus an `*_other` bucket.
A query is cut off after `ABB_ANALYTICS_TIMEOUT_SECONDS` (default 10) with `503 ANALYTICS_TIMEOUT` (retryable).

| Endpoint | Returns |
| --- | --- |
| `GET /v1/analytics/summary` | run counts, success/failure/timeout/retry rates, cost headline (total, per run, per successful run, retries, unpriced calls), run latency p50/p95, behaviour averages, tool and model-call success, active agents |
| `GET /v1/analytics/cost` | headline, retry breakdown (initial vs retry cost, spec §24), cost by day, agent, model, source and project, most expensive runs |
| `GET /v1/analytics/reliability` | rates, failure trend by day, tool success and p95, retry-heavy runs |
| `GET /v1/analytics/performance` | p50/p95 for runs, model calls and tools; slowest operations (tool and model spans by name, other kinds by kind) |
| `GET /v1/pricing` | the price entries the engine uses (built-in, illustrative, plus the caller's overrides) |

Definitions: finished = success, failed, timed out or blocked (cancelled runs are counted separately and excluded
from rates); `success_rate` = success / finished; `failure_rate` = (failed + blocked) / finished; `timeout_rate` =
timed out / finished; `retry_rate` = runs with a retry / runs. Rates are `null`, not 0, when nothing can be
divided. Money is USD rounded to 9 places. Every cost line states its source: `provider_reported` (event attribute
`cost.provider_usd`), `estimated` (tokens x a versioned price, `pricing_version` stored per line), `client_estimate`
(`cost.estimated_usd`) or `unpriced` (counted as $0 and reported). `input_tokens` includes cached tokens. A model
call is retry cost when it follows a `retry.attempted` event of its span or an ancestor span (ADR-042).
Prices: `python -m abb_api.cli set-pricing-override` then `rebuild-costs` (ADR-040); built-in prices are illustrative (KI-050).

## Live streaming

`GET /v1/runs/{id}/stream` (scope `runs:read`, same visibility rules as the other run reads) returns `text/event-stream`. Design and
trade-offs: ADR-022.

| Message | Meaning |
| --- | --- |
| `retry: 3000` | first line: the browser waits 3 s before reconnecting |
| `id: evt_...` `event: trace_event` `data: {...}` | one event, in the list endpoint's shape **without payload** (`has_payload` says if one exists) |
| `event: run_end` `data: {"reason":"run_finished"}` | the run has a terminal `run.*` event and nothing arrived for 5 s: do not reconnect |
| `event: error` `data: {"error": {...}}` | `STREAM_UNAVAILABLE`, retryable: the stream ends, reconnect with `Last-Event-ID` |
| `: keepalive` / `: open` / `: max-lifetime...` | comments: idle keep-alive every 15 s, stream opened, closed at the maximum lifetime (15 min) |

**Order and duplicates.** Events arrive in *arrival* order, not canonical order. Clients must de-duplicate by `event_id` and sort
themselves (`abb_event_schema.ordering`; the web app does, with a golden-file parity test). The server deliberately repeats a window of
events it already sent (the last `ABB_STREAM_OVERLAP_SECONDS`, default 30 s) on every poll boundary and reconnect, because a transaction can
become visible after a later one.

**Resume.** Send `Last-Event-ID: evt_...` (browsers do this on reconnect) or, for a first connection, `?last_event_id=evt_...`; the header
wins. The server re-reads from that event's arrival time minus the overlap. An id that is malformed, unknown or from another run means "from
the start of the run". Without either, the whole run is replayed, then followed live.

**Errors before the stream starts** are ordinary JSON envelopes: `401`, `403`, `404 RUN_NOT_FOUND`, `422`, and
`429 STREAM_LIMIT` (with `Retry-After`; per key `ABB_STREAM_MAX_PER_KEY`=10, per API process `ABB_STREAM_MAX_TOTAL`=50).

**Lifecycle.** A stream ends at `run_end`, at its maximum lifetime, when the client disconnects, or after a 10 s write timeout against a
client that stopped reading. Late events after `run_end` appear on a new connection. An open stream is not re-authenticated (KI-033).

## Events and spans

`GET /v1/runs/{id}/events` returns events in **canonical order** (the same total order as
`abb_event_schema.ordering.sort_events`): by `(sequence, occurred_at, received_at, event_id)` when the run's
`ordering_mode` is `sequence`, otherwise by `(occurred_at, sequence, received_at, event_id)`. Filters:
`event_type` (repeatable; well-formed names only), `status` (repeatable; `success|error|timeout|cancelled|blocked`), `span_id`; `limit` 1-500 (default 100). The page includes
`ordering_mode`; cursors embed it, so a cursor from before a mode change gets `409 CURSOR_STALE`.
Payloads are **not** in lists (`has_payload` says whether one exists); `GET /v1/runs/{id}/events/{event_id}`
returns the full event with its inline payload.

`GET /v1/runs/{id}/spans` returns derived spans (`parent_span_id`, `kind`, `status`, timings, `event_count`)
by start time (spans that never saw a start event come last), `limit` 1-2000 (default 500). It is empty until
the first summarization.

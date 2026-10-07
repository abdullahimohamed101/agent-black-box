# Phase 5 - Live execution streaming

Status: Active (decisions D1-D6 confirmed; steps 1-2 done)
Owner: implementer agent
Branch: `feature/phase-5-live-streaming` (from `main` fa4c346)
Depends on: Phase 2 (ingestion, query API), Phase 4 (run detail, read proxy)
Spec refs: §21, §76 (+76.1, 76.2), §67.5 (payload privacy), §62 (INV-1/2/3), §145 live-stream items; ADR-020 (interim live-run section), ADR-021

## Outcome
Open a running agent's run page and watch events appear within about a second, with no refresh. Duplicates, out-of-order events,
dropped connections and browser refreshes mid-run never corrupt the timeline; the UI says when its view may be incomplete.

## Non-goals
Auth/login and per-user access (Phase 15; KI-029 stays), WebSockets and control messages (Phase 14), Kafka/Redis/NATS fan-out
(spec §76.2 "scale" path; no measured trigger), run-list live updates (the runs list keeps polling), the SDK (no change),
incremental summarization (KI-016), span/trace waterfall (Phase 9), live analytics (Phase 7).

## Current architecture (what exists)
- Ingestion commits events and coalesced `summarize_run` outbox jobs in one transaction (`ingestion/store.py`); `received_at` is stamped
  once per request from the app clock (`ingestion/service.py:80`), before the transaction.
- `GET /v1/runs/{id}/events` is keyset-paged in canonical order (`runs/event_queries.py`, `runs/cursors.py`); payloads are withheld from lists.
- The run record is derived by a debounced worker (about 1 s) and can lag events (`summary_state`).
- Web: browser calls only `GET /api/abb/*` (allowlisted read proxy, ADR-021). ADR-020 interim: poll the run every 3 s and refetch every
  events page when `event_count` changes (O(pages) per change).
- No `text/event-stream` anywhere; no pub/sub.

## Known issues considered
- KI-029 (S1, target Phase 15): stays. The stream adds no new credential; it is the same server-side read key. D5 bounds the resources a
  visitor can hold, and the web app must still not be publicly exposed.
- KI-017 (S2, per-process limiter) and KI-019 (S1, failed-auth throttling, Phase 19): stay; stream caps in D5 are per process, noted in the ADR.
- KI-016 / KI-022 (summarizer cost): not affected; the live view is driven by events, not the summary (D4), so summary lag does not delay the UI.
- KI-026 (request buffering) and KI-021 (outbox purge): unrelated.
- KI-027/028 (lookup and aggregate endpoints): out of scope.
- Pulled in: the ADR-020 interim live-run behaviour is replaced (acceptance 7).

## Decisions (confirm before implementation)
- **D1 Resume cursor and ordering.** The SSE `id:` is the `event_id`. The server cannot resume by canonical order (late events sort before
  already-sent ones) nor by an insertion counter (a bigserial can commit out of order and silently skip rows). It resumes by arrival time:
  watermark = `received_at` of the `Last-Event-ID` event, replay everything with `received_at >= watermark - 30 s` ordered by
  `(received_at, event_id)`, then continue live with the same overlap logic. The 30 s overlap covers transactions that commit after a later
  one (`received_at` is stamped before the transaction). The client de-duplicates by `event_id` (idempotent merge), as spec §21 requires.
  No migration: an index on `(workspace_id, run_id, received_at)` is added only if the plan's EXPLAIN check shows the existing indexes
  insufficient. An unknown `Last-Event-ID` (or none) means "from the start of the run".
- **D2 Live wake-up without new infrastructure.** Ingestion issues `pg_notify('abb_run_events', '<run_id>')` inside its transaction
  (delivered on commit; payload is only the run id, no tenant data). Each API process holds one dedicated listener connection and fans out
  in-process to subscribed streams; each stream then queries the database for new rows under its own tenant context. A fallback poll every
  2 s covers lost notifications and listener reconnects (bounded backoff). Works across several API processes; no Redis.
  Rejected: purely in-process pub/sub (breaks with more than one API process), polling only (latency floor), Redis (no trigger, spec §63 deferred list).
- **D3 What is streamed.** The same light event shape as the list endpoint (payload withheld, `has_payload` flag); the drawer keeps fetching
  payloads on demand. SSE frames: `event: trace_event` with `id: evt_...` per event (batched writes), `: keepalive` comment every 15 s,
  `event: run_end` then close once the run has a terminal `run.*` event and no new event arrived for 5 s (late telemetry after completion is
  still allowed on a new connection, spec §67), and `event: error` with the standard error envelope for stream-fatal conditions.
  Responses carry `Cache-Control: no-store`, `X-Accel-Buffering: no`, nosniff.
- **D4 Frontend merge.** One `EventSource` per run detail page, same origin through the proxy. Events are merged into the TanStack cache keyed
  by `event_id`, inserted at their canonical position (port of the `sequence`/time comparator from `abb_event_schema.ordering`, covered by a
  parity test against shared fixtures), applied in animation-frame batches so a burst re-renders once. The reorder buffer is therefore the
  sorted merge itself, no timers. The live status line (Planning / Reading / Calling model / Running tool / Waiting for approval / Done / Failed)
  is derived from the latest events, not the lagging summary. The UI shows "Live", "Reconnecting" or "Partial data" states; after a gap longer
  than the overlap, or when the ordering mode flips, it reconciles through the REST paging path (existing `CURSOR_STALE` restart logic stays).
  A finished run never opens a stream.
- **D5 Resource bounds.** Per process: at most 50 concurrent streams total and 10 per API key (excess gets 429 `STREAM_LIMIT` with
  `Retry-After`); a stream lives at most 15 minutes (server sends `run_end`-less close, client reconnects with `Last-Event-ID`); a database connection
  is borrowed per poll, never held while idle (no pool starvation, ties to the pool-exhaustion 503 handling); writes have a 10 s timeout
  (stale client cleanup) and client disconnect is checked each cycle. All limits are settings.
- **D6 Proxy.** The allowlist gains `v1/runs/{id}/stream`; the proxy streams the upstream body through unbuffered with no 10 s total timeout
  (connect timeout only), still adding the server-side key and mapping upstream 401/403 to 502. It forwards `Last-Event-ID`.
  The OpenAPI document gets the endpoint (`text/event-stream`); the generated JSON client does not cover it, the SSE reader is hand-written and typed with the generated `Event` type.

ADRs to write: ADR-022 (resume watermark + LISTEN/NOTIFY wake-up, D1-D3, D5), ADR-020 amended (interim section replaced by incremental merge, D4).

## Affected files
API: `apps/api/src/abb_api/streaming/` (new: `hub.py` listener and fan-out, `service.py`, `router.py`, `sse.py` framing), `ingestion/store.py` (notify),
`runs/event_queries.py` (`after_arrival`), `core/config.py` (limits), `main.py`, `openapi.py` + `openapi.json`, tests.
Web: `src/lib/stream.ts` (EventSource client), `src/lib/ordering.ts`, `src/lib/queries.ts`, `src/components/RunDetail.tsx`, `StatusLine`, `src/server/upstream.ts`,
`src/app/api/abb/[...path]/route.ts`, fixtures and tests, Playwright specs.
Other: `scripts/stream-e2e.sh`, `docs/benchmarks/phase-5-streaming.md`, `docs/architecture/api-v1.md`, `docs/RELIABILITY.md`, `docs/OPERATIONS.md`, runbook `docs/runbooks/stream-issues.md`, ADR-022, `docs/DECISIONS.md`, `docs/PROJECT_STATE.md`.

## Acceptance criteria (each needs command + output)
1. `GET /v1/runs/{id}/stream` returns `text/event-stream`; events ingested after connect arrive without polling by the client; scoped to the key's workspace and project (cross-workspace and wrong-project runs give 404, wrong scope 403) - API tests.
2. Resume: connect, drop, ingest more, reconnect with `Last-Event-ID` receives exactly the missed events (plus bounded overlap duplicates) - test including an event whose transaction commits after a later one.
3. Keep-alive comment within 15 s of idleness; a client that stops reading is dropped within the write timeout; a disconnected client releases its stream slot - tests with shortened settings.
4. Limits: 51st concurrent stream and 11th per key get 429 `STREAM_LIMIT`; stream closes at max lifetime; no database connection is held while idle (pool-size-1 test keeps ingestion working during an open stream).
5. Works with two API processes (NOTIFY on one, stream on the other) and with the listener connection killed (fallback poll delivers within 3 s, listener reconnects) - integration tests.
6. Run completion: `run_end` after terminal event plus quiet period, stream closes, the client does not reconnect; late event after completion appears on a fresh connection.
7. Frontend: duplicate delivery, out-of-order delivery, reconnect with missed events, browser refresh mid-run, run completion, ordering-mode flip, `CURSOR_STALE` - component tests; the O(pages) refetch is gone (test asserts no events-list refetch on `event_count` change while streaming); ordering comparator parity test against the Python implementation on shared fixtures.
8. Playwright E2E with the SDK example agent writing through the real API: the trace populates with no refresh; screenshots captured; axe passes; live/reconnecting/partial indicators have non-colour cues.
9. p95 accept-to-display < 1 s measured (SDK flush interval excluded, ingestion HTTP accepted -> DOM row visible) over a recorded run, in `docs/benchmarks/phase-5-streaming.md`.
10. Security: stream endpoint appears in OpenAPI with auth, payloads are never streamed, no secrets in logs, proxy allowlist test for the new path only, `scripts/quality.sh full` exit 0, CI green.

## Verification plan
Focused tests per step; `scripts/quality.sh full`; `scripts/stream-e2e.sh` (containers, example agent, Playwright); fault injection for acceptance 2/5 (kill listener, kill connection, pause client);
mutation checks on committed code (resume overlap, dedupe, comparator, limits); `verify-change` then `review-change` (security pass: tenant scoping, resource exhaustion, proxy) then `harden-change`.

## Risks
- Out-of-order commit makes a naive cursor skip events: mitigated by D1 overlap plus client dedupe; residual risk is a transaction longer than 30 s, documented and logged as a setting.
- Long-lived connections vs. reverse proxies/timeouts: keep-alive and `X-Accel-Buffering`; runbook entry.
- Listener connection loss: fallback poll plus reconnect; tested.
- Resource exhaustion by a visitor holding streams (KI-029 context): D5 caps; per-process only (KI-017 family).
- Large bursts re-render cost: frame batching plus existing virtualization; 10,000-event fixture replayed through the stream in E2E.
- `received_at` overlap re-sends up to 30 s of rows on every reconnect: bounded by the page size and the light payload.

## Ordered steps (one commit each)
1. [x] Docs: correct `PROJECT_STATE.md`, ADR-022, this plan confirmed.
2. [x] API: arrival-time queries (`arrival_of`, `arrived_since`) + `ArrivalCursor` overlap logic, 10 tests, 7 mutants killed.
3. API: `pg_notify` on commit, listener hub with fan-out, fallback poll, reconnect, tests (incl. two processes).
4. API: SSE endpoint (framing, keep-alive, run_end, limits, lifetime, disconnect handling), OpenAPI, tests.
5. Web: proxy streaming + allowlist, `ordering.ts` with parity test, `stream.ts` client.
6. Web: incremental merge in `RunDetail`, status line, live/partial/reconnect states, remove interim refetch; component tests.
7. E2E: `stream-e2e.sh`, Playwright specs, latency benchmark, screenshots.
8. Docs: api-v1, RELIABILITY, OPERATIONS, runbook, ADR-020 amendment, DECISIONS index, known issues.
9. Verify, review, harden, complete-phase.

# Runbook: live streams (run pages stop updating, or show "Partial data")

**Symptoms**: a running run's page stops growing until refreshed; the live bar says "Reconnecting" or "Live updates unavailable";
`GET /v1/runs/{id}/stream` answers `429 STREAM_LIMIT`; API logs mention `stream listener unavailable` or `stream failed`.
The event data itself is safe: streams only read what ingestion already committed, and the REST list (`/events`) is always complete.

1. **Is it the stream or the data?** Open `GET /v1/runs/{id}/events` (or reload the page). If the missing events are there, only live
   delivery is affected; if not, follow [ingestion rejections](ingestion-rejections.md) and `/readyz`.
2. **`Live updates unavailable`** (the browser gave up after 8 failed reconnects, or there is no `EventSource`): the page keeps polling
   the run record and reloads events when it changes, so the data still arrives, only slower. Find out why the stream answers
   non-200: `curl -i -N -H "Authorization: Bearer $KEY" $API/v1/runs/$RUN/stream`.
   - `401` (`SESSION_INVALID` / `API_KEY_INVALID`): the session expired or was revoked, or the key was revoked; sign in again. A stream that
     was open when this happened ends with `event: error` code `STREAM_UNAUTHORIZED` (it re-checks every `ABB_STREAM_REAUTH_SECONDS`,
     default 30) and the page asks for a new sign-in instead of reconnecting forever. `403 PERMISSION_DENIED`: the role lost `run.read`.
   - `404`: the run is not visible to that person or key (other project or workspace, or `X-ABB-Workspace` names a workspace they are not in).
   - `429 STREAM_LIMIT`: see step 4.
   - `503`/connection refused: API down or database unreachable; fix `/readyz` first, streams resume by themselves.
3. **Reconnect loop / stream closes every few seconds**: a proxy or load balancer between web and API is buffering or cutting idle
   connections. The API sends a keepalive comment every 15 s (`ABB_STREAM_KEEPALIVE_SECONDS`) and `X-Accel-Buffering: no`; set the proxy's
   read timeout above that and disable response buffering for `text/event-stream`.
4. **`STREAM_LIMIT`**: the per-process caps were hit (`ABB_STREAM_MAX_TOTAL` 50, `ABB_STREAM_MAX_PER_KEY` 10). Slots free when a client
   disconnects or a stream reaches `ABB_STREAM_MAX_LIFETIME_SECONDS` (900). The cap is per person (`scope: user` in the error
   details; `key` for API keys): 10 streams across all of one person's tabs and devices. Close tabs, raise the limits if the host can afford one idle
   connection plus a query every 2 s per stream, or run more API processes (limits are per process, KI-017).
5. **Delivery is slow but not broken** (events appear after about 2 s instead of immediately): the `NOTIFY` listener is down and streams are
   on the 2 s fallback poll. Check the log for `stream listener unavailable (...)` and that the API's database user can `LISTEN`
   (`abb_runtime` can). Listener connections: `SELECT pid, state FROM pg_stat_activity WHERE application_name = 'abb-stream-listener';`
   (one per API process). Pooled or transaction-mode connection proxies (pgbouncer) break `LISTEN`: point `ABB_DATABASE_URL` at a
   session-mode endpoint or at Postgres directly.
6. **A viewer sees a gap that REST does not have**: only possible for an event whose transaction stayed open longer than
   `ABB_STREAM_OVERLAP_SECONDS` (30) after later events were already delivered (KI-034). The page reloads the history after a long gap and
   when the run ends; a manual refresh also fixes it. Raise the overlap if long transactions are expected.
7. **A revoked key is still receiving events**: streams are not re-authenticated, so it lasts at most the stream lifetime (KI-033). Lower
   `ABB_STREAM_MAX_LIFETIME_SECONDS`, or restart the API to drop every stream at once.

8. **Ingestion slows while many viewers open a hot run**: every new viewer replays the last `ABB_STREAM_OVERLAP_SECONDS` of events. On a
   5,000-event run, 50 simultaneous viewers took 17 s to replay and ingest p50 rose to about 200 ms meanwhile (`docs/benchmarks/phase-5-streaming.md`).
   Lower `ABB_STREAM_DB_CONCURRENCY` to protect ingestion further (streams queue), lower the overlap, or limit viewers. Steady-state slow ingests
   on very large runs are the summarizer (KI-016), not streaming.

**Do not** add a second consumer that reads events "to feed streams": the stream reads Postgres by design (ADR-022). If many viewers of one
hot run make the per-stream query a measured cost (see `docs/benchmarks/phase-5-streaming.md`), that is the trigger for a shared fan-out, via an ADR.

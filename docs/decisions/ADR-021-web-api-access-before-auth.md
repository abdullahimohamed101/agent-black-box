# ADR-021: The Web App Reads the API Through a Server-Side Proxy Holding a `runs:read` Key

Status: Superseded by [ADR-060](ADR-060-dashboard-identity-oidc-sessions.md) (Phase 15, 2026-10-09). Kept for history: the web server no longer holds a key; `ABB_WEB_API_KEY` is gone.
Date: 2026-10-07

## Context
The API authenticates with project API keys; user login arrives in Phase 15. A key in browser code (`NEXT_PUBLIC_*`) would
be exposed to every visitor.

## Decision
- Browser code calls same-origin `GET /api/abb/<path>`. A Next route handler (`app/api/abb/[...path]/route.ts`) forwards only
  allowlisted read paths (`/v1/runs`, `/v1/runs/{id}`, `/events`, `/events/{id}`, `/spans`, and since Phase 5 `/stream`) with the server-only
  `ABB_WEB_API_KEY` and `ABB_API_INTERNAL_URL`, adds a timeout, and relays status, body and `X-Request-ID`.
- Only `GET` is accepted; query strings pass through unchanged (the API validates them).
- The key should be a **workspace-wide, `runs:read`-only** key. Until Phase 15 the deployment is single-tenant per web instance:
  anyone who can reach the web app can read that workspace. This is a documented limitation, not for public exposure.
- `ABB_WEB_DATA_SOURCE=fixtures` makes the same route serve deterministic fixtures (tests, demos, UI development).

## Live streams (Phase 5)
`GET /api/abb/v1/runs/{id}/stream` is relayed as Server-Sent Events: the body is passed through as it arrives, only reaching the
API is time-limited (10 s), the browser's `Last-Event-ID` is forwarded (validated), and the upstream stream is aborted when the browser
disconnects. API errors (404, 429 `STREAM_LIMIT`, 503) are relayed unchanged; upstream 401/403 still become 502. With fixture data there
is no live source: the proxy answers 404 `STREAM_NOT_AVAILABLE` and the UI keeps polling.

**Exposure to state plainly:** every browser shares the one key, and live streams are capped at 10 per key (per API process). Anyone who
can reach the web app can open ten streams (tabs or `curl`) and make other viewers' streams answer `429 STREAM_LIMIT`; their pages then fall
back to polling. Data is unaffected. Until Phase 15 gives each viewer their own identity, either keep the web app private or raise
`ABB_STREAM_MAX_PER_KEY` and put per-IP limits in front of it (KI-029).

## Consequences
No CORS needed for the browser; the key never leaves the server; Phase 15 replaces the proxy's key with a per-user session.

# ADR-021: The Web App Reads the API Through a Server-Side Proxy Holding a `runs:read` Key

Status: Accepted
Date: 2026-10-07

## Context
The API authenticates with project API keys; user login arrives in Phase 15. A key in browser code (`NEXT_PUBLIC_*`) would
be exposed to every visitor.

## Decision
- Browser code calls same-origin `GET /api/abb/<path>`. A Next route handler (`app/api/abb/[...path]/route.ts`) forwards only
  allowlisted read paths (`/v1/runs`, `/v1/runs/{id}`, `/events`, `/events/{id}`, `/spans`) with the server-only
  `ABB_WEB_API_KEY` and `ABB_API_INTERNAL_URL`, adds a timeout, and relays status, body and `X-Request-ID`.
- Only `GET` is accepted; query strings pass through unchanged (the API validates them).
- The key should be a **workspace-wide, `runs:read`-only** key. Until Phase 15 the deployment is single-tenant per web instance:
  anyone who can reach the web app can read that workspace. This is a documented limitation, not for public exposure.
- `ABB_WEB_DATA_SOURCE=fixtures` makes the same route serve deterministic fixtures (tests, demos, UI development).

## Consequences
No CORS needed for the browser; the key never leaves the server; Phase 15 replaces the proxy's key with a per-user session.

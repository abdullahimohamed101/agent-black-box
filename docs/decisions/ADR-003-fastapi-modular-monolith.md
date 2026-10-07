# ADR-003: FastAPI Modular Monolith for v1

Status: Accepted
Date: 2026-10-07

## Context

Spec §57.3, §103: the first constraint is product velocity and correctness, not raw throughput. The
SDK ecosystem is Python; the web app is Next.js. Telemetry ingestion and the control-plane query API
have different scaling profiles but no measured evidence yet that they need separate processes.

## Decision

- One deployable FastAPI application (`apps/api`, package `abb_api`) with logical modules
  (`ingestion, runs, traces, analytics, evaluations, auth, policies, artifacts`) added as phases land.
  Modules interact through service/repository interfaces, never each other's tables.
- Ingestion and query routers are separate so a slow query is never on the SDK path.
- App factory `create_app(settings)`; settings from `ABB_*` environment variables validated at startup.
- Python 3.12, SQLAlchemy 2 (async, asyncpg), Alembic (linear, additive migrations), uv for locking.
- Typed error envelope `{error:{code,message,category,retryable,request_id,details}}` and request IDs
  from Phase 0.
- Workers run from the same codebase via a separate entrypoint (Phase 2), not a separate service.

## Alternatives

- Go ingestion service now: second language and duplicated auth/validation before evidence of need.
  Migration condition recorded in spec §57.3 (sustained >5,000 events/s per deployment, p95 ack >150 ms,
  or validation CPU in the top three bottlenecks after batching).
- Microservices per module: network boundaries without a scaling reason (anti-pattern, build prompt §9).

## Consequences

- Positive: one deploy, one test suite, fast iteration, strong typing end to end.
- Negative: noisy-neighbour risk between ingestion and queries until split; mitigated by separate
  routers, bounded pools and later extraction.

## Migration implications

Extracting a module means moving its package behind its existing interface; the HTTP contract and event
schema do not change.

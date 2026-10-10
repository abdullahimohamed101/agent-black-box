# Agent Black Box

A flight recorder, observability, debugging, evaluation and (eventually) control plane for
AI agents. See what an agent actually did: every model call, tool call, file edit, shell
command, retry and failure, with cost and latency, live.

> Status: Phases 0-8 merged (event contract, ingestion, Python SDK, web UI, live streaming, coding-agent demo, cost and analytics, framework adapters). The MVP gate is open; see `docs/IMPLEMENTATION_PLAN.md`.
> You can run the stack, trace an agent with the Python SDK or a framework adapter, and watch the run in the web UI, live.
> See `docs/PROJECT_STATE.md`.

## What it does

```python
from blackbox import BlackBox

bb = BlackBox(api_key=..., project="coding-agent")
with bb.run("Fix OAuth timeout") as run:
    with run.span("run-tests", kind="shell"):
        agent.execute()
```

Open the dashboard and watch the run populate live: timeline, diffs, retries, cost, and
where it failed. Diffs, shell output, cost and analytics are in the UI today; evaluations, policy and approvals, and multi-agent tracing are later phases (`docs/IMPLEMENTATION_PLAN.md`).

## Architecture in one picture

```text
SDK -> FastAPI (modular monolith) -> PostgreSQL (events + outbox) -> workers
                    \-> SSE -> Next.js
```

Simple on purpose: Kafka, ClickHouse, Redis and Kubernetes are deferred until measured
triggers say otherwise (spec §57). The stable asset is the versioned canonical telemetry
contract, not any single datastore. Details: `ARCHITECTURE.md`.

## Repository Layout

```text
apps/{api,web}  packages/{event-schema,sdk-python}  integrations/{langgraph,openai,anthropic,mcp,conformance}
examples/coding-agent  infrastructure/  scripts/  docs/
```
New directories appear when their phase begins (for example `processors/`).

## Quick Start

Requires Python 3.12 + `uv`, Node 22 + `pnpm`, and Docker (see `docs/development/setup.md`).

```bash
cp .env.example .env
make setup      # Python env, JS deps, Postgres (Compose, host port 5433), migrations
make dev        # API http://localhost:8000 and web http://localhost:3000
curl localhost:8000/readyz
make test lint typecheck
```

Whole stack in containers: `make up` (migrate, api, worker, web, postgres, and a dev-only fake sign-in provider), `make down` to stop.

Signing in to the dashboard (no identity-provider account needed locally): `make seed` adds the workspace `local` and its owner
`owner@local.test` (development only), `scripts/fake-oidc.sh` serves a fake provider that accepts any email, and the dashboard's "Sign in"
button takes you through it. Details, roles and a real provider: `docs/development/setup.md` and `docs/runbooks/auth-and-access.md`.
`scripts/quality.sh full` is the single gate used by CI and agents.

Try the data plane (needs `make up`):

```bash
make smoke                      # provisions a tenant, ingests a run over gzip HTTP, reads it back
make seed                       # local workspace + project + dev key in .local/dev-api-key
KEY=$(cat .local/dev-api-key)
curl -s localhost:8000/v1/runs -H "Authorization: Bearer $KEY"
```

See a real run in about two minutes (needs the Quick Start above, `make seed`, and `pnpm --filter @abb/web build`):
the flagship demo is a small coding agent that fixes a broken OAuth service, hits a failing test, retries and passes.
`scripts/coding-e2e.sh` runs it end to end on a throwaway database and drives a browser; `examples/coding-agent/README.md`
shows how to point it at your own stack and open the run (diff, shell and cost panels).

API reference: `docs/architecture/api-v1.md` and `apps/api/openapi.json`. Operations: `docs/OPERATIONS.md`.

## Phase Status

See `docs/IMPLEMENTATION_PLAN.md` for the full table (Phases 0-8 and 15 merged, MVP gate open). Summary: Phases 0-7 build the MVP
(foundation, event contract, ingestion, SDK, web, live streaming, coding-agent demo,
analytics) followed by a human review gate; Phases 8-20 add integrations, multi-agent
tracing, reliability intelligence, evaluations, replay, security, policy/approvals, RBAC,
search, alerting, scale hardening, production hardening and release polish.

## Working in this repo (humans and agents)

Start at `AGENTS.md`. Build instructions: `docs/BUILD_PROMPT.md`. Spec:
`docs/architecture/agent-black-box-spec.md`. Decisions: `docs/DECISIONS.md`.

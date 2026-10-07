# Agent Black Box

A flight recorder, observability, debugging, evaluation and (eventually) control plane for
AI agents. See what an agent actually did: every model call, tool call, file edit, shell
command, retry and failure, with cost and latency, live.

> Status: Phases 0-1 done and merged; Phase 2 (ingestion, storage, run queries) complete and awaiting merge.
> You can already run the stack, ingest events over HTTP and read runs back; there is no SDK or product UI yet.
> See `docs/PROJECT_STATE.md`.

## What it will do

```python
from blackbox import BlackBox

bb = BlackBox(api_key=..., project="coding-agent")
with bb.run("Fix OAuth timeout") as run:
    with run.span("run-tests", kind="shell"):
        agent.execute()
```

Open the dashboard and watch the run populate live: timeline, diffs, retries, cost, and
where it failed. (The API above is the Phase 3 target and does not exist yet.)

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
apps/{api,web}  infrastructure/  scripts/  docs/        (exists now)
packages/{event-schema,sdk-python,...}  integrations/  processors/  examples/  tests/   (later phases)
```
Directories appear when their phase begins.

## Quick Start

Requires Python 3.12 + `uv`, Node 22 + `pnpm`, and Docker (see `docs/development/setup.md`).

```bash
cp .env.example .env
make setup      # Python env, JS deps, Postgres (Compose, host port 5433), migrations
make dev        # API http://localhost:8000 and web http://localhost:3000
curl localhost:8000/readyz
make test lint typecheck
```

Whole stack in containers: `make up` (migrate, api, worker, web, postgres), `make down` to stop.
`scripts/quality.sh full` is the single gate used by CI and agents.

Try the data plane (needs `make up`):

```bash
make smoke                      # provisions a tenant, ingests a run over gzip HTTP, reads it back
make seed                       # local workspace + project + dev key in .local/dev-api-key
KEY=$(cat .local/dev-api-key)
curl -s localhost:8000/v1/runs -H "Authorization: Bearer $KEY"
```

API reference: `docs/architecture/api-v1.md` and `apps/api/openapi.json`. Operations: `docs/OPERATIONS.md`.

## Phase Status

See `docs/IMPLEMENTATION_PLAN.md` for the full table (Phases 0-1 merged, Phase 2 complete). Summary: Phases 0-7 build the MVP
(foundation, event contract, ingestion, SDK, web, live streaming, coding-agent demo,
analytics) followed by a human review gate; Phases 8-20 add integrations, multi-agent
tracing, reliability intelligence, evaluations, replay, security, policy/approvals, RBAC,
search, alerting, scale hardening, production hardening and release polish.

## Working in this repo (humans and agents)

Start at `AGENTS.md`. Build instructions: `docs/BUILD_PROMPT.md`. Spec:
`docs/architecture/agent-black-box-spec.md`. Decisions: `docs/DECISIONS.md`.

# Agent Black Box

A flight recorder, observability, debugging, evaluation and (eventually) control plane for
AI agents. See what an agent actually did: every model call, tool call, file edit, shell
command, retry and failure, with cost and latency, live.

> Status: **Phase 0 not started.** This repository currently holds the specification, the
> agent operating guide, and the phase plans. See `docs/PROJECT_STATE.md`.

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
apps/{api,web}  packages/{event-schema,sdk-python,...}  integrations/  processors/
examples/  tests/  infrastructure/  scripts/  docs/
```
Directories appear when their phase begins.

## Quick Start

Not available yet; arrives with Phase 0. Tooling requirements: `docs/development/setup.md`.

## Phase Status

See `docs/IMPLEMENTATION_PLAN.md` for the full table. Summary: Phases 0-7 build the MVP
(foundation, event contract, ingestion, SDK, web, live streaming, coding-agent demo,
analytics) followed by a human review gate; Phases 8-20 add integrations, multi-agent
tracing, reliability intelligence, evaluations, replay, security, policy/approvals, RBAC,
search, alerting, scale hardening, production hardening and release polish.

## Working in this repo (humans and agents)

Start at `AGENTS.md`. Build instructions: `docs/BUILD_PROMPT.md`. Spec:
`docs/architecture/agent-black-box-spec.md`. Decisions: `docs/DECISIONS.md`.

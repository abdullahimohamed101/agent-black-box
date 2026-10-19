# Agent BlackBox documentation

Agent BlackBox records what an AI agent did (every model call, tool call, edit, command, retry and failure, with cost and
latency) and lets you open the run afterwards or watch it live. This is the index of the written documentation. The project
overview is in the [repository README](../README.md).

## Start here

| I want to | Read |
| --- | --- |
| Run it and see a failed agent run | [Quickstart](quickstart.md) |
| Fix something that did not work | [Troubleshooting](troubleshooting.md) |
| Run it on one machine for other people | [Deployment](deployment.md) |
| Trace my own agent | [Python SDK](architecture/sdk-python.md), then [integrations](architecture/integrations.md) for LangGraph, OpenAI, Anthropic and MCP |
| Set up a development environment | [Development setup](development/setup.md) and [CONTRIBUTING](../CONTRIBUTING.md) |

A complete hosting guide (domain, TLS, a managed identity provider, more than one machine) is planned as a separate piece of
work and does not exist yet. [Deployment](deployment.md) says exactly what it covers and what it leaves out.

## Reference

| Document | What it defines |
| --- | --- |
| [Events](architecture/events.md) | The canonical event contract (schema version 1.0): envelope, event types, attributes, ordering |
| [HTTP API v1](architecture/api-v1.md) | Authentication, scopes, errors, ingestion, runs, search, streaming, policy, alerts. The machine-readable contract is [`apps/api/openapi.json`](../apps/api/openapi.json) |
| [Python SDK](architecture/sdk-python.md) | `BlackBox`, runs and spans, delivery and retries, redaction, policy checks |
| [Integrations](architecture/integrations.md) | The framework adapters and what each one records |
| [Policy SDK guide](development/policy-sdk.md) | Writing and enforcing policies from an agent |
| [Architecture](../ARCHITECTURE.md) | How the pieces fit together |
| [Engineering specification](architecture/agent-black-box-spec.md) | The long-form design the product is built to |
| [Architecture decisions](DECISIONS.md) | Every decision record (ADR) and its status |

## Operate

| Document | What it covers |
| --- | --- |
| [Operations](OPERATIONS.md) | Processes, every environment variable, readiness, retention, backups, quotas, metrics, logs |
| [Runbooks](runbooks/README.md) | What to do for a symptom or an alert: ingestion, workers, database, streams, alerts, retention, backup and restore, secrets, migrations |
| [Security](SECURITY.md) | The threat model, what is enforced and tested, and the known gaps |
| [Reliability](RELIABILITY.md) | What the product promises when parts fail |
| [Known issues](KNOWN_ISSUES.md) | Open problems and accepted limitations, with severity |

## Project

| Document | What it covers |
| --- | --- |
| [Testing](TESTING.md) | How the product is tested, per area |
| [Implementation plan](IMPLEMENTATION_PLAN.md) | The phase roadmap |
| [Project state](PROJECT_STATE.md) | Where the work stands and what is next |
| [Design direction](design/ui-direction.md) | The visual and interaction direction of the web app |
| [Security policy](../SECURITY.md) | How to report a vulnerability |

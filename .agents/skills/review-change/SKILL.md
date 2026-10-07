---
name: review-change
description: Independent skeptical senior review of a diff for correctness, architecture, tenant isolation, security, regressions and maintainability.
---

# Review Change

Act as a skeptical senior reviewer. Do not modify files.

Review against the plan, acceptance criteria, `AGENTS.md` invariants, the spec and ADRs.

Check for: correctness bugs, regressions, missing edge cases, error handling, concurrency
and ordering assumptions, compatibility breaks (event schema, HTTP API, SDK API are three
separately versioned contracts), data integrity, boundary violations, unnecessary
complexity, duplicated logic.

Product-specific checks:

- **Tenancy**: every query/repository call scoped by workspace; a test proves cross-workspace
  reads fail.
- **Idempotency & ordering**: duplicate `event_id` is safe; nothing depends on arrival order.
- **SDK safety**: no exception escapes into the host agent; queues/retries bounded; no
  blocking I/O on the caller thread.
- **Secrets & payloads**: nothing sensitive logged or persisted by default; redaction happens
  before export; API keys hashed, shown once.
- **Untrusted content**: trace payloads rendered as text, never as HTML; no stored XSS.
- **Immutability**: no UPDATE of accepted event rows.
- **Scale creep**: no Kafka/ClickHouse/Redis/K8s without a measured trigger and ADR.

Classify findings P0 critical, P1 must fix before merge, P2 should fix, P3 optional.
For each: severity, exact location, problem, impact, suggested correction.
If there are no substantive issues, say so explicitly.

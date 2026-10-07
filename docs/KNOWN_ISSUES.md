# Known Issues

The register of known limitations, risks and environment gaps. It is read at the start of every phase
(`plan-change` step 3) and updated when a phase completes (`complete-phase` step 4).

**Severity**: **S1** fix before real users or public exposure; **S2** fix before the phase or scale it affects;
**S3** low impact. **Target** is the phase or measured trigger by which the item must be revisited; there is
no named owner because the implementer is the same agent every time, so the target is what keeps an item from being forgotten.
Actionable items have a GitHub issue (label `known-issue`, plus its severity); add one when you add an item here, and
close the issue and move the row to Resolved when it is fixed. A deferral is not a fix: a new item may be added only with a severity and a target.

## Open

| ID | Sev | Issue | Impact | Target | Issue |
| --- | --- | --- | --- | --- | --- |
| KI-018 | S1 | Agents, runs and spans per project are unbounded (a client can invent agent slugs, run ids, span ids) | storage growth and noisy lists; only the rate limit bounds it | Phase 19: per-project quotas and cardinality limits | [#5](https://github.com/abdullahimohamed101/agent-black-box/issues/5) |
| KI-019 | S1 | Failed authentication is not throttled | an unauthenticated flood adds database load | Phase 19: edge or IP rate limiting | [#6](https://github.com/abdullahimohamed101/agent-black-box/issues/6) |
| KI-027 | S3 | The Python SDK queue is in memory only: a killed process (SIGKILL, OOM) loses undelivered events | the last <= 250 ms of events of a crashed agent; `mode="local"` is the durable option | Phase 17 (TypeScript SDK) or on user demand: optional disk spool | (to file) |
| KI-028 | S3 | The API, worker and CLI do not check at startup that they are not connected as the database owner/superuser (KI-020 separation is by configuration) | a misconfigured deployment silently loses the INV-1 privilege protection | Phase 19: startup check / readiness warning | (to file) |
| KI-029 | S3 | SDK secret detection is best-effort patterns; secrets of unknown shape pass through in `FULL` payload mode | unredacted secrets in captured payloads (default mode drops payloads) | Phase 13: server-side detectors and policy | (to file) |
| KI-016 | S2 | Full recomputation of a very large active run (30,000 events) stalls requests 1.7-2.6 s on a 2-vCPU VM | only runs of tens of thousands of events still receiving events (`docs/benchmarks/phase-2-ingestion.md`) | Trigger: a run exceeds ~10,000 active events or p99 ingest > 500 ms: incremental summarization | [#7](https://github.com/abdullahimohamed101/agent-black-box/issues/7) |
| KI-022 | S2 | The summarizer loads every event of a run into memory, with no cap | a run of millions of events could exhaust worker memory (dead-lettered after repeated crashes, not looped) | Trigger: with KI-016, plus a cap | [#8](https://github.com/abdullahimohamed101/agent-black-box/issues/8) |
| KI-017 | S2 | The rate limiter is per API process | N processes allow N times the configured rate | Phase 19: shared limiter | [#9](https://github.com/abdullahimohamed101/agent-black-box/issues/9) |
| KI-026 | S2 | Request bodies (up to 5 MiB) are buffered with no global concurrency limit | memory pressure under many large concurrent batches | Phase 19 | [#10](https://github.com/abdullahimohamed101/agent-black-box/issues/10) |
| KI-021 | S2 | Finished outbox jobs are never purged (an index keeps lookups fast) | slow table growth | Phase 19: retention job (spec §94) | [#11](https://github.com/abdullahimohamed101/agent-black-box/issues/11) |
| KI-024 | S2 | CI actions are pinned by tag, base images by tag, not SHA/digest | supply-chain drift | Phase 19 (spec §106) | [#12](https://github.com/abdullahimohamed101/agent-black-box/issues/12) |
| KI-013 | S3 | `make`/`quality.sh` load `.env` by shell `source`; breaks on values with spaces, `$` or `#` | fine for current values | Next tooling change: read `.env` from `Settings` | [#13](https://github.com/abdullahimohamed101/agent-black-box/issues/13) |
| KI-023 | S3 | `/docs` and `/openapi.json` are served in every environment | should be a deliberate choice per environment | Phase 19 | [#14](https://github.com/abdullahimohamed101/agent-black-box/issues/14) |
| KI-025 | S3 | Cross-project run listing sorts without a `(workspace_id, started_at)` index | slower lists on very large workspaces | When measured | [#15](https://github.com/abdullahimohamed101/agent-black-box/issues/15) |
| KI-008 | S3 | Web pins TypeScript 6 / ESLint 9 (lint ecosystem lacks TS 7 / ESLint 10) | none today | External: revisit when typescript-eslint and eslint-plugin-react support them | [#16](https://github.com/abdullahimohamed101/agent-black-box/issues/16) |
| KI-010 | S3 | `pnpm audit` reports one high advisory in a dev-only dependency (`braces <=3.0.3`, no patch) | dev tooling only; CI audits production dependencies | External: revisit when `eslint-config-next` updates | [#17](https://github.com/abdullahimohamed101/agent-black-box/issues/17) |

## Accepted (documented limitations, no action planned)

| ID | Limitation | Why it is acceptable |
| --- | --- | --- |
| KI-006 | The in-repo spec is a mechanical extraction of the `.docx` (diagram layout degraded) | the `.docx` is kept alongside; ADRs amend |
| KI-009 | `next dev` rewrites `tsconfig.json`, so Prettier ignores it | cosmetic |
| KI-014 | The generated JSON Schema is looser than the models (NUL/surrogates, int-vs-float ranges, calendar validity, self-parenting) | documented in `docs/architecture/events.md`; fixtures flag `jsonschema_rejects` |
| KI-015 | `Event` models are only shallowly frozen (`attributes`, `payload`, `tags` are mutable) | treat as read-only; storage enforces immutability (the runtime role has no UPDATE/DELETE on `events`, KI-020) |

## Resolved

| ID | Was | Resolved |
| --- | --- | --- |
| KI-001..005 | Node/pnpm, Docker, PostgreSQL, Python 3.12, uv missing | 2026-10-07: installed with approval (Docker via Colima) |
| KI-007 | CI had never executed | 2026-10-07: PR #1 green |
| KI-011 | migration tests shared one database | Phase 2: throwaway database per session |
| KI-012 | compose did not run migrations | Phase 2: one-shot `migrate` service |
| KI-020 | runtime DB role could UPDATE/DELETE `events` | Phase 3: migration 0007 `abb_runtime` role, owner-only migrations, tested as the role; [#4](https://github.com/abdullahimohamed101/agent-black-box/issues/4) closes on merge |

Rule: an acceptance criterion that depends on something unavailable stays `UNVERIFIED (env)` and the gate stays open.

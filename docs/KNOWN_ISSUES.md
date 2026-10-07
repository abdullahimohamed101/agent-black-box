# Known Issues and Environment Gaps

Checked 2026-10-07 on the development machine (macOS, Apple Silicon).

| ID | Issue | Impact | Resolution |
| --- | --- | --- | --- |
| KI-001..005 | Missing Node/pnpm, Docker, PostgreSQL, Python 3.12, uv | **Resolved 2026-10-07** (installed via Homebrew with user approval; Docker via Colima) | see `docs/development/setup.md` |
| KI-006 | Spec is a docx; in-repo copy is a mechanical extraction | diagram layout degraded | `.docx` is kept alongside; ADRs amend |

| KI-007 | ~~CI never executed~~ | **Resolved 2026-10-07**: PR #1 run green (`quality`, `containers`) | none |
| KI-008 | Web pins TypeScript 6 / ESLint 9 because the lint ecosystem lacks TS 7 / ESLint 10 support | none today | revisit when typescript-eslint and eslint-plugin-react support them |
| KI-009 | `next dev` rewrites `tsconfig.json`; it is excluded from Prettier | cosmetic | none |
| KI-010 | `pnpm audit` reports 1 high (`braces <=3.0.3`, GHSA-vfj7-8cjw-p6xm, no patched version) via `eslint-config-next` | dev tooling only, not shipped; CI audits production deps only | revisit when `eslint-config-next` updates |
| KI-011 | ~~migration tests shared the `abb_test` database~~ | **Resolved (Phase 2 step 2)**: the test session uses its own throwaway database; migration tests create one each | none |
| KI-012 | ~~compose did not run migrations~~ | **Resolved (Phase 2 step 8)**: one-shot `migrate` service; api and worker wait for it | none |
| KI-013 | `make`/`quality.sh` load `.env` by shell `source`, which breaks on values with spaces, `$` or `#` | fine for current values | read the root `.env` from `Settings` and drop shell sourcing |
| KI-014 | JSON Schema is looser than the models for NUL/surrogates, int-vs-float ranges, calendar validity and self-parenting | external validators can accept events this server rejects | documented in `docs/architecture/events.md`; fixtures flag `jsonschema_rejects` |
| KI-015 | `Event` models are shallowly frozen; `attributes`/`payload`/`tags` are mutable dicts/lists | accidental in-process mutation could change `content_hash` | treat as read-only; storage enforces immutability in Phase 2 |
| KI-016 | Full recomputation of a very large active run (30,000 events) stalls requests for 1.7-2.6 s on a 2-vCPU VM | only runs of tens of thousands of events that still receive events; see `docs/benchmarks/phase-2-ingestion.md` | incremental summarization when a run exceeds ~10,000 active events or p99 ingest exceeds 500 ms |
| KI-017 | The rate limiter is per API process | N processes allow N times the configured rate | shared limiter in Phase 19 |
| KI-018 | Agents, runs and spans per project are unbounded: a client can create rows by inventing `agent_id` slugs, run ids or span ids | storage growth and noisy lists; bounded only by the rate limit | per-project quotas and cardinality limits (Phase 19) |
| KI-019 | Failed authentication is not throttled: each bad key costs one indexed lookup | an unauthenticated flood adds database load | edge or IP rate limiting (Phase 19) |
| KI-020 | The runtime database role can UPDATE and DELETE `events` (INV-1 is enforced by code and a source-scanning test, not by privileges) | a bug or injection could rewrite history | separate migration and runtime roles; REVOKE UPDATE/DELETE on `events` for the runtime role (Phase 19) |
| KI-021 | Finished outbox jobs are never purged (an index keeps lookups fast) | slow table growth | retention job (Phase 19, spec §94) |
| KI-022 | The summarizer loads every event of a run into memory; there is no cap | a run of millions of events could exhaust worker memory; such a job is now dead-lettered after repeated crashes, not looped | incremental summarization (see KI-016) and a cap |
| KI-023 | `/docs` and `/openapi.json` are served in every environment | fine for a public API, but should be a deliberate choice per environment | decide with deployment (Phase 19) |
| KI-024 | CI actions are pinned by tag and base images by tag, not digest | supply-chain drift | pin by SHA/digest at release hardening (spec §106) |
| KI-025 | A workspace-wide key listing runs across projects sorts without a `(workspace_id, started_at)` index | slower lists on very large workspaces | add the index when measured |
| KI-026 | Request bodies (up to 5 MiB) are buffered per request with no global concurrency limit | memory pressure under many large concurrent batches | concurrency limit at the edge or in the app (Phase 19) |

Rule: an acceptance criterion that depends on something unavailable stays `UNVERIFIED (env)` and the gate stays open.

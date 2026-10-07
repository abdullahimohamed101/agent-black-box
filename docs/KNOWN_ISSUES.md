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

Rule: an acceptance criterion that depends on something unavailable stays `UNVERIFIED (env)` and the gate stays open.

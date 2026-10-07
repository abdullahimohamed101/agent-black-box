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
| KI-011 | `tests/test_migrations.py` runs `downgrade base` on the shared `abb_test` database | safe today (pytest is sequential, no tables) but will collide with Phase 2 repository tests | give migration tests their own throwaway database (Phase 2 plan) |
| KI-012 | `docker compose --profile app up` does not run migrations | harmless with the empty baseline | add a migrate step to the api service (Phase 2 plan) |
| KI-013 | `make`/`quality.sh` load `.env` by shell `source`, which breaks on values with spaces, `$` or `#` | fine for current values | read the root `.env` from `Settings` and drop shell sourcing |

Rule: an acceptance criterion that depends on something unavailable stays `UNVERIFIED (env)` and the gate stays open.

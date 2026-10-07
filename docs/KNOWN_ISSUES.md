# Known Issues and Environment Gaps

Checked 2026-10-07 on the development machine (macOS, Apple Silicon).

| ID | Issue | Impact | Resolution |
| --- | --- | --- | --- |
| KI-001..005 | Missing Node/pnpm, Docker, PostgreSQL, Python 3.12, uv | **Resolved 2026-10-07** (installed via Homebrew with user approval; Docker via Colima) | see `docs/development/setup.md` |
| KI-006 | Spec is a docx; in-repo copy is a mechanical extraction | diagram layout degraded | `.docx` is kept alongside; ADRs amend |

| KI-007 | CI workflow (`.github/workflows/ci.yml`) never executed: the repo has no GitHub remote | Phase 0 criterion 7 is `UNVERIFIED (env)`; the same commands were run locally via `scripts/quality.sh full` and `docker compose --profile app up --wait` | push the branch to a remote (needs user approval) and confirm the run is green |
| KI-008 | Web pins TypeScript 6 / ESLint 9 because the lint ecosystem lacks TS 7 / ESLint 10 support | none today | revisit when typescript-eslint and eslint-plugin-react support them |
| KI-009 | `next dev` rewrites `tsconfig.json`; it is excluded from Prettier | cosmetic | none |

Rule: an acceptance criterion that depends on something unavailable stays `UNVERIFIED (env)` and the gate stays open.

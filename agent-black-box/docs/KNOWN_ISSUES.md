# Known Issues and Environment Gaps

Checked 2026-10-07 on the development machine (macOS, Apple Silicon).

| ID | Issue | Impact | Resolution |
| --- | --- | --- | --- |
| KI-001 | Node.js / npm / pnpm not installed | Cannot build, lint or test `apps/web`; Phase 0/4 web criteria `UNVERIFIED (env)` | `brew install node pnpm` (needs approval) |
| KI-002 | Docker not installed | `docker compose up` unverifiable; Phase 0 stack criterion `UNVERIFIED (env)` | Docker Desktop, Colima or OrbStack (needs approval) |
| KI-003 | PostgreSQL not installed natively | No DB for migrations/repository tests unless via Docker | `brew install postgresql@16` or use Docker |
| KI-004 | System `python3` is 3.9.6; Homebrew has 3.12 | Project targets Python 3.12 | create venv with `/opt/homebrew/bin/python3.12` (no install needed) |
| KI-005 | `uv` not installed | optional | `brew install uv` or use venv + pip with a lockfile tool |
| KI-006 | Spec is a docx; in-repo copy is a mechanical extraction | diagram layout degraded | `.docx` is kept alongside; ADRs amend |

Rule: a Phase 0 acceptance criterion that depends on one of these stays `UNVERIFIED (env)` and
the gate stays open until resolved. Backend-only work (Phases 1-3) can proceed on Python 3.12 +
a native or Docker PostgreSQL once available.
